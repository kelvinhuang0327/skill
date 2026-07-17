#!/usr/bin/env ruby
# frozen_string_literal: true

require "digest"
require "json"
require "optparse"
require "pathname"
require "yaml"

module ControlPlanePrototype
  ROOT = Pathname.new(__dir__).parent.expand_path

  REQUIRED_CONTEXT_FILES = [
    ".ai/ai-context/PROJECT_PROFILE.md",
    ".ai/ai-context/PROJECT_CONTEXT.md",
    ".ai/ai-context/RUNBOOK.md",
    ".ai/ai-memory/MEMORY_LOG.md"
  ].freeze

  DURABLE_SOURCE_FILES = [
    "AGENT_CORE.md",
    "ROLE_PROFILES.md",
    "ROUTING_AND_LIFECYCLE.md",
    "TASK_MANIFEST.schema.yaml",
    "WORKER_TASK_TEMPLATE.md"
  ].freeze

  ENUMS = {
    "task.type" => %w[feature bugfix test docs metadata merge pr_fix analysis maintenance entry_check],
    "task.risk_class" => %w[LOW MEDIUM HIGH],
    "task.routing_path" => %w[FAST TECHNICAL STRATEGIC],
    "worktree.mode" => %w[NOT_APPLICABLE REUSABLE EPHEMERAL],
    "scope.pins[].type" => %w[invariant before_after],
    "policies.db" => %w[none read_only write_authorized],
    "policies.runtime" => %w[none tmp_only logs_allowed],
    "policies.external_side_effects" => %w[none listed],
    "policies.pr" => %w[none draft ready],
    "policies.merge" => %w[none after_review after_ci_and_review],
    "policies.cleanup" => %w[standard_lifecycle retain_with_reason owner_override],
    "tests[].side_effects_allowed" => %w[none tmp_only logs db_sandbox],
    "memory.read.mode" => %w[none relevant bounded_recent],
    "memory.write.mode" => %w[forbidden allowed],
    "context.stale_evidence_policy" => %w[mark_stale reject],
    "authorization.class" => %w[NONE SINGLE_PROMPT STANDALONE],
    "status.review_verdict" => %w[PASS PASS_WITH_RISKS FAIL BLOCKED NOT_RUN],
    "status.lifecycle_state" => %w[S0 S1 S2 S3 S4 S5 S6 S7 S8 S9 S10 S11 FROZEN]
  }.freeze

  FIXED_VALUES = {
    "schema_version" => 1,
    "control_plane.schema_version" => 1,
    "task.created_by" => "planner",
    "review.mode" => "fixed_head"
  }.freeze

  LIST_ITEM_TYPES = {
    "project_attachment.required_context_files" => String,
    "steps" => String,
    "success_criteria" => String,
    "scope.allowed_files" => String,
    "scope.protected_paths" => String,
    "scope.forbidden_subsystems" => String,
    "policies.external_list" => String,
    "memory.read.selectors" => String,
    "evidence.required" => String
  }.freeze

  PROFILE_ALLOWED_TOP_LEVEL = %w[
    project control_plane project_attachment risk_domain_overrides protected_paths
    restrictions test_aliases worktree_defaults publication_restrictions
  ].freeze

  PROFILE_FORBIDDEN_NORMALIZED = %w[
    evidence evidence_vocabulary authorization authorization_matrix routing routing_rules
    worktree_lifecycle lifecycle final_classifications common_final_classifications
    memory_contract
  ].freeze

  HIGH_EXTERNAL_PATTERN = Regexp.union(
    /\bproduction\b.*\b(deploy|release|activation|activate)\b/i,
    /\b(online promotion|promote\b.*\bonline|executable.*activation|registry activation)\b/i,
    /\b(credentials?|secrets?|payments?)\b/i,
    /\bexternal\b.*\b(message|notification|publication|publish)\b/i,
    /\b(real[- ]?money|live bet|real bet|下注|實單|真實金流)\b/i,
    /\b(force delete|force remove|broad cleanup|rm -rf|reset --hard|git clean)\b/i
  )

  class ValidationFailure < StandardError; end

  ManifestDocument = Struct.new(:path, :raw, :data, keyword_init: true) do
    def sha256
      Digest::SHA256.hexdigest(raw)
    end

    def source_label
      path.to_s
    end
  end

  LintResult = Struct.new(:id, :passed, :message, keyword_init: true) do
    def to_h
      { "id" => id, "status" => passed ? "PASS" : "FAIL", "message" => message }
    end
  end

  class LintReport
    attr_reader :schema_errors, :results

    def initialize(schema_errors:, results:)
      @schema_errors = schema_errors
      @results = results
    end

    def success?
      schema_errors.empty? && results.all?(&:passed)
    end

    def result(id)
      results.find { |entry| entry.id == id }
    end

    def to_h
      {
        "status" => success? ? "PASS" : "FAIL",
        "schema" => {
          "status" => schema_errors.empty? ? "PASS" : "FAIL",
          "errors" => schema_errors
        },
        "lint" => results.map(&:to_h)
      }
    end
  end

  AttachmentContext = Struct.new(
    :root,
    :default_profile_path,
    :selected_profile_path,
    :profile_raw,
    :profile_data,
    :profile_error,
    :ai_exists,
    :default_profile_exists,
    :missing_context_files,
    :view_path,
    :view_exists,
    :view_raw,
    keyword_init: true
  )

  class Toolchain
    attr_reader :root, :schema_template, :control_plane_version, :schema_version

    def initialize(root = ROOT)
      @root = Pathname.new(root).expand_path
      @schema_template = parse_yaml(File.binread(@root.join("TASK_MANIFEST.schema.yaml")), "TASK_MANIFEST.schema.yaml")
      @control_plane_version = @schema_template.dig("control_plane", "version")
      @schema_version = @schema_template["schema_version"]
    end

    def load_manifest(path)
      manifest_path = Pathname.new(path).expand_path
      raw = File.binread(manifest_path)
      ensure_utf8!(raw, manifest_path)
      data = parse_yaml(raw, manifest_path)
      raise ArgumentError, "manifest root must be a mapping: #{manifest_path}" unless data.is_a?(Hash)

      ManifestDocument.new(path: manifest_path, raw: raw, data: data)
    end

    def validate_schema(data)
      errors = []
      validate_node(schema_template, data, "$", errors)

      worker_final = data.dig("status", "worker_final") if data.is_a?(Hash)
      unless worker_final.is_a?(String) && (worker_final == "NOT_RUN" || worker_final.match?(/\AWORKER_(COMPLETE|COMPLETE_WITH_RISKS|PARTIAL|BLOCKED|WAITING_OWNER)\z/))
        errors << "$.status.worker_final must be NOT_RUN or a valid WORKER_<STATUS>"
      end

      errors
    end

    def lint(document, project_root:, profile_path: nil, view_path: nil, source_label: nil, seen_task_ids: [])
      attachment = build_attachment_context(
        document,
        project_root: project_root,
        profile_path: profile_path,
        view_path: view_path
      )
      schema_errors = validate_schema(document.data)
      label = source_label || document.source_label
      results = (1..22).map do |number|
        id = "L#{number}"
        begin
          send("lint_l#{number}", document, attachment, label, seen_task_ids)
        rescue StandardError => error
          LintResult.new(id: id, passed: false, message: "cannot evaluate safely: #{error.class}: #{error.message}")
        end
      end
      LintReport.new(schema_errors: schema_errors, results: results)
    end

    def render_worker(document, project_root:, profile_path: nil, source_label: nil, seen_task_ids: [])
      label = source_label || document.source_label
      report = lint(document, project_root: project_root, profile_path: profile_path, source_label: label, seen_task_ids: seen_task_ids)
      raise ValidationFailure, JSON.pretty_generate(report.to_h) unless report.success?

      data = document.data
      template = File.read(root.join("WORKER_TASK_TEMPLATE.md"), encoding: "UTF-8")
      auth_block = extract_authorization_block(template, data.dig("authorization", "class"))
      body = extract_worker_body(template)

      condition_values = {
        "reattach" => data.dig("worktree", "reattach") == true,
        "memory.write.mode == allowed" => data.dig("memory", "write", "mode") == "allowed",
        "policies.pr != none" => data.dig("policies", "pr") != "none",
        "policies.merge != none" => data.dig("policies", "merge") != "none"
      }

      auth_block = resolve_conditionals(auth_block, condition_values)
      body = resolve_conditionals(body, condition_values)

      replacements = worker_replacements(document, label)
      tests_marker = "{{tests:  - {name}: {cmd} [side_effects_allowed: {side_effects_allowed}]}}"
      body = body.gsub(tests_marker, replacements.fetch("tests"))
      rendered = [auth_block, body].join("\n\n")
      replacements.each do |slot, value|
        rendered = rendered.gsub("{{#{slot}}}", value.to_s)
      end
      unresolved = rendered.scan(/\{\{.*?\}\}/m).uniq
      raise ArgumentError, "unresolved template slots: #{unresolved.join(', ')}" unless unresolved.empty?

      normalize_output(rendered)
    end

    def render_active_task(document, project_root:, profile_path: nil, source_label: nil, seen_task_ids: [])
      label = source_label || document.source_label
      report = lint(document, project_root: project_root, profile_path: profile_path, source_label: label, seen_task_ids: seen_task_ids)
      raise ValidationFailure, JSON.pretty_generate(report.to_h) unless report.success?
      return nil unless document.data.dig("active_task_projection", "enabled") == true

      render_active_task_unchecked(document, label)
    end

    def projection_drift(document, actual, source_label: nil)
      data = document.data
      enabled = data.dig("active_task_projection", "enabled") == true
      if !enabled
        return [actual.nil? || actual.empty?, actual.nil? || actual.empty? ? "projection disabled and no view exists" : "DRIFT: projection disabled but a view exists"]
      end
      return [false, "DRIFT: enabled projection view is missing"] if actual.nil?

      label = source_label || document.source_label
      expected = render_active_task_unchecked(document, label)
      hash_banner = "<!-- manifest_sha256: #{document.sha256} -->"
      return [false, "DRIFT: manifest_sha256 banner missing or incorrect"] unless actual.include?(hash_banner)
      return [false, "DRIFT: regenerated projection differs from existing view"] unless byte_equal?(actual, expected)

      [true, "projection matches manifest bytes and actual SHA-256"]
    end

    def render_compiled_role(role)
      role_key = role.to_s.downcase
      recipes = {
        "handoff" => {
          title: "Conversation Handoff Reporter — Mechanically Compiled Prototype",
          role_section: "R1",
          core_sections: %w[§1 §2 §3 §7 §8 §10 §12 §13 §14],
          routing_sections: %w[§0],
          template_sections: %w[§C]
        },
        "planner" => {
          title: "Planner / Task Compiler — Mechanically Compiled Prototype",
          role_section: "R4",
          core_sections: %w[§1 §2 §3 §4 §5 §6 §7 §8 §9 §10 §11 §12 §13 §14],
          routing_sections: %w[§0 §1 §2 §4 §5 §6 §7 §8 §9],
          template_sections: %w[§R §T §P §C],
          whole_sources: ["TASK_MANIFEST.schema.yaml"]
        }
      }
      recipe = recipes[role_key]
      raise ArgumentError, "role must be handoff or planner" unless recipe

      sources = DURABLE_SOURCE_FILES.each_with_object({}) do |name, memo|
        memo[name] = File.read(root.join(name), encoding: "UTF-8")
      end
      digest_lines = DURABLE_SOURCE_FILES.map do |name|
        "<!-- durable_source_sha256: #{name}=#{Digest::SHA256.hexdigest(sources.fetch(name))} -->"
      end

      sections = []
      recipe.fetch(:core_sections).each do |key|
        sections << source_section("AGENT_CORE.md", extract_level_two_section(sources.fetch("AGENT_CORE.md"), key))
      end
      sections << source_section("ROLE_PROFILES.md", extract_level_two_section(sources.fetch("ROLE_PROFILES.md"), recipe.fetch(:role_section)))
      recipe.fetch(:routing_sections).each do |key|
        sections << source_section("ROUTING_AND_LIFECYCLE.md", extract_level_two_section(sources.fetch("ROUTING_AND_LIFECYCLE.md"), key))
      end
      recipe.fetch(:template_sections).each do |key|
        sections << source_section("WORKER_TASK_TEMPLATE.md", extract_level_two_section(sources.fetch("WORKER_TASK_TEMPLATE.md"), key))
      end
      Array(recipe[:whole_sources]).each do |source|
        sections << source_section(source, sources.fetch(source).strip)
      end

      header = [
        "<!-- compiled_from: control_plane #{control_plane_version} -->",
        "<!-- generated_by: prototype/control_plane.rb render-role #{role_key} -->",
        "<!-- status: DRAFT_FOR_OWNER_REVIEW — PROTOTYPE ONLY; NOT ACTIVATED -->",
        *digest_lines,
        "",
        "# #{recipe.fetch(:title)}",
        "",
        "This output is a deterministic extraction of authoritative durable sections; it contains no project memory.",
        ""
      ].join("\n")
      normalize_output(header + sections.join("\n\n"))
    end

    private

    def parse_yaml(raw, source)
      YAML.safe_load(raw)
    rescue Psych::Exception => error
      raise ArgumentError, "invalid YAML in #{source}: #{error.message}"
    end

    def ensure_utf8!(raw, source)
      candidate = raw.dup.force_encoding(Encoding::UTF_8)
      raise ArgumentError, "input is not valid UTF-8: #{source}" unless candidate.valid_encoding?
    end

    def validate_node(expected, actual, path, errors)
      unless compatible_type?(expected, actual)
        errors << "#{path} expected #{type_name(expected)}, got #{actual.class}"
        return
      end

      case expected
      when Hash
        missing = expected.keys - actual.keys
        extra = actual.keys - expected.keys
        missing.each { |key| errors << "#{path}.#{key} is required" }
        extra.each { |key| errors << "#{path}.#{key} is not allowed by the manifest schema" }
        expected.each do |key, child|
          validate_node(child, actual[key], "#{path}.#{key}", errors) if actual.key?(key)
        end
      when Array
        item_path = schema_path(path) + "[]"
        if expected.empty?
          expected_class = LIST_ITEM_TYPES[schema_path(path)]
          if expected_class
            actual.each_with_index do |value, index|
              errors << "#{path}[#{index}] expected #{expected_class}, got #{value.class}" unless value.is_a?(expected_class)
            end
          end
        else
          actual.each_with_index do |value, index|
            validate_node(expected.first, value, "#{path}[#{index}]", errors)
          end
        end
        validate_enum(item_path, actual, path, errors)
      else
        canonical = schema_path(path)
        validate_enum(canonical, actual, path, errors)
        if FIXED_VALUES.key?(canonical) && actual != FIXED_VALUES.fetch(canonical)
          errors << "#{path} must equal #{FIXED_VALUES.fetch(canonical).inspect}"
        end
      end
    end

    def validate_enum(canonical, actual, display_path, errors)
      allowed = ENUMS[canonical]
      return unless allowed

      if canonical.end_with?("[]")
        Array(actual).each_with_index do |value, index|
          errors << "#{display_path}[#{index}] must be one of #{allowed.join(', ')}" unless allowed.include?(value)
        end
      elsif !allowed.include?(actual)
        errors << "#{display_path} must be one of #{allowed.join(', ')}"
      end
    end

    def compatible_type?(expected, actual)
      case expected
      when Hash then actual.is_a?(Hash)
      when Array then actual.is_a?(Array)
      when String then actual.is_a?(String)
      when Integer then actual.is_a?(Integer)
      when TrueClass, FalseClass then actual == true || actual == false
      else actual.class == expected.class
      end
    end

    def type_name(value)
      case value
      when Hash then "mapping"
      when Array then "list"
      when String then "string"
      when Integer then "integer"
      when TrueClass, FalseClass then "boolean"
      else value.class.to_s
      end
    end

    def schema_path(path)
      path.sub(/\A\$\.?/, "").gsub(/\[\d+\]/, "[]")
    end

    def build_attachment_context(document, project_root:, profile_path:, view_path:)
      raise ArgumentError, "project_root is required for full L1-L22 lint" if project_root.nil? || project_root.to_s.empty?

      data = document.data
      attachment_root = Pathname.new(project_root).expand_path
      relative_profile = data.dig("project_attachment", "profile_path").to_s
      default_profile = attachment_root.join(relative_profile)
      selected_profile = profile_path ? Pathname.new(profile_path).expand_path : default_profile
      profile_raw = nil
      profile_data = nil
      profile_error = nil
      if selected_profile.file?
        begin
          profile_raw = File.binread(selected_profile)
          ensure_utf8!(profile_raw, selected_profile)
          profile_data = parse_yaml(profile_raw, selected_profile)
          raise ArgumentError, "profile root must be a mapping" unless profile_data.is_a?(Hash)
        rescue StandardError => error
          profile_error = error.message
        end
      else
        profile_error = "profile not found: #{selected_profile}"
      end

      required = Array(data.dig("project_attachment", "required_context_files"))
      missing = required.reject { |relative| safe_join(attachment_root, relative)&.file? }
      candidate_view = view_path && Pathname.new(view_path).expand_path
      AttachmentContext.new(
        root: attachment_root,
        default_profile_path: default_profile,
        selected_profile_path: selected_profile,
        profile_raw: profile_raw,
        profile_data: profile_data,
        profile_error: profile_error,
        ai_exists: attachment_root.join(".ai").directory?,
        default_profile_exists: default_profile.file?,
        missing_context_files: missing,
        view_path: candidate_view,
        view_exists: candidate_view ? candidate_view.file? : false,
        view_raw: candidate_view&.file? ? File.binread(candidate_view) : nil
      )
    end

    def lint_l1(document, _attachment, _label, seen_task_ids)
      data = document.data
      id = data.dig("task", "id").to_s
      issues = []
      issues << "task.id is empty" if id.strip.empty?
      issues << "duplicate task.id #{id}" if seen_task_ids.include?(id)
      issues << "authorization.scope must equal task.id" unless data.dig("authorization", "scope") == id
      issues << "goal is empty" if data["goal"].to_s.strip.empty?
      issues << "steps must be a non-empty list of non-empty strings" unless nonempty_string_list?(data["steps"])
      issues << "success_criteria must be a non-empty list of non-empty strings" unless nonempty_string_list?(data["success_criteria"])
      result("L1", issues, "id/scope/goal/steps/success criteria are complete and unique in the supplied set")
    end

    def lint_l2(document, _attachment, _label, _seen)
      data = document.data
      policies = hash(data["policies"])
      tests = array(data["tests"])
      issues = []
      required_tests = tests.select { |test| hash(test)["required"] == true }
      if required_tests.any? { |test| hash(test)["side_effects_allowed"] == "db_sandbox" } && policies["db"] != "write_authorized"
        issues << "required db_sandbox test conflicts with policies.db=#{policies['db']}"
      end
      if required_tests.any? { |test| hash(test)["side_effects_allowed"] == "logs" } && policies["runtime"] != "logs_allowed"
        issues << "required logs test conflicts with policies.runtime=#{policies['runtime']}"
      end
      if required_tests.any? { |test| hash(test)["side_effects_allowed"] == "tmp_only" } && !%w[tmp_only logs_allowed].include?(policies["runtime"])
        issues << "required tmp_only test conflicts with policies.runtime=#{policies['runtime']}"
      end
      external_list = array(policies["external_list"])
      issues << "external_list must be empty when external_side_effects=none" if policies["external_side_effects"] == "none" && !external_list.empty?
      issues << "external_list must be non-empty when external_side_effects=listed" if policies["external_side_effects"] == "listed" && external_list.empty?

      action_text = (array(data["steps"]) + tests.map { |test| hash(test)["cmd"] }).compact.join("\n")
      db_write_pattern = /\b(insert\s+into|delete\s+from|update\s+\w+\s+set|db\s+write|database\s+write|migrat(?:e|ion)|backfill)\b/i
      if action_text.match?(db_write_pattern) && policies["db"] != "write_authorized"
        issues << "steps/tests request a DB write while policies.db=#{policies['db']}"
      end

      forbidden = array(data.dig("scope", "forbidden_subsystems")).map { |value| normalized_token(value) }
      array(data.dig("scope", "allowed_files")).each do |path|
        path_tokens = path.to_s.downcase.split(/[^a-z0-9]+/)
        overlap = forbidden.find { |token| token.length >= 2 && path_tokens.include?(token) }
        issues << "allowed file #{path} falls inside forbidden subsystem #{overlap}" if overlap
      end
      result("L2", issues, "required actions are consistent with forbidden subsystems and side-effect policies")
    end

    def lint_l3(document, _attachment, _label, _seen)
      issues = array(document.data["tests"]).each_with_index.map do |test, index|
        item = hash(test)
        next unless item["required"] == true
        next if %w[none tmp_only logs db_sandbox].include?(item["side_effects_allowed"])

        "tests[#{index}] required=true lacks a valid side_effects_allowed"
      end.compact
      result("L3", issues, "every required test declares side_effects_allowed")
    end

    def lint_l4(document, _attachment, _label, _seen)
      scope = hash(document.data["scope"])
      protected_paths = array(scope["protected_paths"])
      allowed_files = array(scope["allowed_files"])
      issues = array(scope["pins"]).map do |pin|
        item = hash(pin)
        next unless item["type"] == "invariant"
        path = item["path"]
        next if protected_paths.include?(path) && !allowed_files.include?(path)

        "invariant pin #{path.inspect} must be protected and must not be allowed"
      end.compact
      result("L4", issues, "all invariant pins are protected and excluded from allowed writes")
    end

    def lint_l5(document, _attachment, _label, _seen)
      data = document.data
      mode = data.dig("worktree", "mode")
      path = data.dig("worktree", "path").to_s
      branch = data.dig("repo", "task_branch").to_s
      issues = []
      issues << "#{mode} requires an exact non-empty worktree.path" if %w[REUSABLE EPHEMERAL].include?(mode) && path.strip.empty?
      if mode == "NOT_APPLICABLE"
        issues << "NOT_APPLICABLE requires empty worktree.path" unless path.empty?
        issues << "NOT_APPLICABLE requires empty task_branch for this schema" unless branch.empty?
      end
      result("L5", issues, "worktree mode, exact path, and task branch are consistent")
    end

    def lint_l6(document, _attachment, _label, _seen)
      data = document.data
      issues = []
      if data.dig("policies", "merge") != "none"
        issues << "merge requires review.required=true" unless data.dig("review", "required") == true
        issues << "merge requires review.independent=true" unless data.dig("review", "independent") == true
      end
      result("L6", issues, "merge policy has required independent review")
    end

    def lint_l7(document, _attachment, _label, _seen)
      data = document.data
      risk = data.dig("task", "risk_class")
      auth = data.dig("authorization", "class")
      rank = { "NONE" => 0, "SINGLE_PROMPT" => 1, "STANDALONE" => 2 }
      minimum = { "LOW" => 0, "MEDIUM" => 1, "HIGH" => 2 }
      issues = []
      issues << "authorization.class #{auth.inspect} is below #{risk} minimum" if rank.fetch(auth, -1) < minimum.fetch(risk, 99)
      reason = data.dig("authorization", "high_risk_reason").to_s
      issues << "HIGH requires a precise high_risk_reason" if risk == "HIGH" && reason.strip.empty?
      issues << "LOW/MEDIUM must leave high_risk_reason empty" if risk != "HIGH" && !reason.empty?
      if risk == "LOW"
        issues << "LOW cannot contain repo writes" unless array(data.dig("scope", "allowed_files")).empty?
        issues << "LOW requires policies.db=none" unless data.dig("policies", "db") == "none"
        issues << "LOW requires policies.runtime=none" unless data.dig("policies", "runtime") == "none"
        issues << "LOW requires no external side effects" unless data.dig("policies", "external_side_effects") == "none"
        issues << "LOW requires policies.pr=none and policies.merge=none" unless data.dig("policies", "pr") == "none" && data.dig("policies", "merge") == "none"
        issues << "LOW requires memory.write.mode=forbidden" unless data.dig("memory", "write", "mode") == "forbidden"
      end
      result("L7", issues, "authorization class is at or above the risk minimum")
    end

    def lint_l8(document, _attachment, _label, _seen)
      auth = hash(document.data["authorization"])
      klass = auth["class"]
      token = auth["token"].to_s
      issues = []
      case klass
      when "NONE"
        issues << "NONE requires NOT_REQUIRED" unless token == "NOT_REQUIRED"
      when "SINGLE_PROMPT"
        issues << "SINGLE_PROMPT requires PENDING_OWNER_TOKEN or an Owner replacement value" if token.empty? || %w[NOT_REQUIRED SEPARATE_MESSAGE_REQUIRED].include?(token)
      when "STANDALONE"
        issues << "STANDALONE requires SEPARATE_MESSAGE_REQUIRED" unless token == "SEPARATE_MESSAGE_REQUIRED"
      else
        issues << "unknown authorization class"
      end
      message = token == "PENDING_OWNER_TOKEN" ? "token is pending; rendered Worker semantics must be WAITING_OWNER" : "token value is valid for the authorization class"
      result("L8", issues, message)
    end

    def lint_l9(document, _attachment, _label, _seen)
      data = document.data
      high_trigger = data.dig("policies", "db") == "write_authorized" ||
        array(data.dig("policies", "external_list")).any? { |entry| entry.to_s.match?(HIGH_EXTERNAL_PATTERN) }
      issues = high_trigger && data.dig("task", "risk_class") != "HIGH" ? ["DB write or HIGH external action requires risk_class=HIGH"] : []
      result("L9", issues, high_trigger ? "HIGH trigger is classified HIGH" : "no DB-write or HIGH-external trigger found")
    end

    def lint_l10(document, _attachment, _label, _seen)
      data = document.data
      read_only = array(data.dig("scope", "allowed_files")).empty?
      issues = []
      if read_only
        invalid_tests = array(data["tests"]).select { |test| hash(test)["required"] == true && !%w[none tmp_only].include?(hash(test)["side_effects_allowed"]) }
        issues << "read-only task has required test side effects beyond none/tmp_only" unless invalid_tests.empty?
        issues << "read-only task requires policies.pr=none" unless data.dig("policies", "pr") == "none"
        issues << "read-only task requires policies.merge=none" unless data.dig("policies", "merge") == "none"
        issues << "read-only task requires memory.write.mode=forbidden" unless data.dig("memory", "write", "mode") == "forbidden"
      end
      result("L10", issues, read_only ? "read-only restrictions are satisfied" : "task has an explicit write allowlist")
    end

    def lint_l11(document, _attachment, _label, _seen)
      data = document.data
      risk_rank = { "LOW" => 0, "MEDIUM" => 1, "HIGH" => 2 }
      issue = data.dig("policies", "pr") != "none" && risk_rank.fetch(data.dig("task", "risk_class"), -1) < 1
      result("L11", issue ? ["PR policy implies push and requires risk_class>=MEDIUM"] : [], "PR/risk relationship is valid")
    end

    def lint_l12(document, _attachment, _label, _seen)
      data = document.data
      cleanup = data.dig("policies", "cleanup")
      reason = data.dig("policies", "cleanup_reason").to_s
      task_text = [data.dig("task", "name"), data["goal"]].compact.join(" ")
      issues = []
      if cleanup == "standard_lifecycle" && task_text.match?(/\b(independent|standalone|separate)[-_ ]cleanup\b/i)
        issues << "standard_lifecycle must not create an independent cleanup task"
      end
      if %w[retain_with_reason owner_override].include?(cleanup) && reason.strip.empty?
        issues << "#{cleanup} requires cleanup_reason"
      end
      result("L12", issues, "cleanup policy does not split normal lifecycle and has any required reason")
    end

    def lint_l13(document, _attachment, _label, _seen)
      data = document.data
      issues = []
      if data.dig("worktree", "reattach") == true
        issues << "reattach requires mode=EPHEMERAL" unless data.dig("worktree", "mode") == "EPHEMERAL"
        issues << "reattach requires non-empty pr_ref" if data.dig("worktree", "pr_ref").to_s.strip.empty?
      end
      result("L13", issues, "reattach fields are consistent")
    end

    def lint_l14(document, _attachment, _label, _seen)
      data = document.data
      issue = data.dig("task", "risk_class") == "HIGH" && data.dig("task", "routing_path") != "STRATEGIC"
      result("L14", issue ? ["HIGH must route STRATEGIC"] : [], "HIGH-to-STRATEGIC mapping is valid")
    end

    def lint_l15(document, attachment, _label, _seen)
      data = document.data
      project_attachment = hash(data["project_attachment"])
      issues = []
      issues << "profile_path must equal .ai/agent-profile.yaml" unless project_attachment["profile_path"] == ".ai/agent-profile.yaml"
      issues << "required_context_files must equal the four canonical .ai files" unless project_attachment["required_context_files"] == REQUIRED_CONTEXT_FILES
      identity = project_attachment["profile_sha256_or_version"].to_s
      unless identity == "UNKNOWN" || identity.match?(/\A[0-9a-f]{64}\z/i) || identity.match?(/\A[A-Za-z0-9][A-Za-z0-9._-]*\z/)
        issues << "profile_sha256_or_version must be SHA-256, a version token, or UNKNOWN"
      end
      issues << attachment.profile_error if attachment.profile_error

      if attachment.profile_data
        profile_errors = validate_profile_shape(attachment.profile_data)
        issues.concat(profile_errors)
        forbidden = attachment.profile_data.keys.reject { |key| PROFILE_ALLOWED_TOP_LEVEL.include?(key) }
        normalized_forbidden = forbidden.select { |key| PROFILE_FORBIDDEN_NORMALIZED.include?(normalized_token(key)) }
        unless forbidden.empty?
          issues << "[Risk] profile keys are not allowed and are ignored: #{forbidden.join(', ')}"
        end
        unless normalized_forbidden.empty?
          issues << "[Risk] Shared Core redefinition attempted and ignored: #{normalized_forbidden.join(', ')}"
        end
        if identity.match?(/\A[0-9a-f]{64}\z/i) && attachment.profile_raw && Digest::SHA256.hexdigest(attachment.profile_raw) != identity.downcase
          issues << "profile SHA-256 does not match profile_sha256_or_version"
        end
      end
      result("L15", issues, "profile binding, four required files, profile shape, and forbidden-key gate are valid")
    end

    def lint_l16(document, attachment, _label, _seen)
      data = document.data
      missing_attachment = !attachment.ai_exists || !attachment.default_profile_exists
      missing_context = attachment.missing_context_files
      return result("L16", [], "attachment and all required context files are present") unless missing_attachment || !missing_context.empty?

      downgraded = entry_check_shape?(data)
      policy = data.dig("project_attachment", "missing_context_policy")
      issues = []
      if missing_attachment
        issues << "A_NO_ATTACHMENT requires ENTRY_CHECK/BOOTSTRAP_READINESS shape" unless downgraded
      elsif policy == "entry_check"
        issues << "A_NO_CONTEXT with entry_check policy requires ENTRY_CHECK shape" unless downgraded
      elsif policy == "stop"
        issues << "A_NO_CONTEXT with stop policy blocks compilation"
      else
        issues << "invalid missing_context_policy"
      end
      details = []
      details << ".ai/profile missing" if missing_attachment
      details << "missing context: #{missing_context.join(', ')}" unless missing_context.empty?
      result("L16", issues, downgraded ? "attachment gap is correctly downgraded (#{details.join('; ')})" : details.join("; "))
    end

    def lint_l17(document, attachment, _label, _seen)
      data = document.data
      binding = hash(data["control_plane"])
      profile_binding = hash(attachment.profile_data && attachment.profile_data["control_plane"])
      issues = []
      issues << "control_plane.version is required" if binding["version"].to_s.empty?
      issues << "control_plane.source_path is required" if binding["source_path"].to_s.empty?
      issues << "A_VERSION_MISMATCH: manifest=#{binding['version']} durable=#{control_plane_version}" unless binding["version"] == control_plane_version
      issues << "A_SCHEMA_MISMATCH: manifest=#{binding['schema_version']} durable=#{schema_version}" unless binding["schema_version"] == schema_version && data["schema_version"] == schema_version
      if attachment.profile_data
        issues << "A_VERSION_MISMATCH: profile=#{profile_binding['version']} manifest=#{binding['version']}" unless profile_binding["version"] == binding["version"]
        issues << "A_SCHEMA_MISMATCH: profile=#{profile_binding['schema_version']} manifest=#{binding['schema_version']}" unless profile_binding["schema_version"] == binding["schema_version"]
        issues << "profile control_plane.source_path differs from manifest" unless profile_binding["source_path"] == binding["source_path"]
      end
      result("L17", issues, "manifest, profile, durable version, and schema bindings are compatible")
    end

    def lint_l18(document, _attachment, _label, _seen)
      data = document.data
      read = hash(data.dig("memory", "read"))
      mode = read["mode"]
      selectors = array(read["selectors"])
      max_entries = read["max_entries"]
      issues = []
      if mode == "none"
        issues << "mode=none requires empty selectors" unless selectors.empty?
        issues << "mode=none requires max_entries=0" unless max_entries == 0
      else
        issues << "mode=#{mode} requires selectors" if selectors.empty?
        issues << "mode=#{mode} requires max_entries>=1" unless max_entries.is_a?(Integer) && max_entries >= 1
      end
      selectors.each do |selector|
        match = selector.to_s.match(/\A(task_id|branch|pr|risk_domain|keyword):(.+)\z/)
        unless match
          issues << "selector #{selector.inspect} has no allowed prefix/payload"
          next
        end
        prefix = match[1]
        payload = match[2]
        issues << "task_id selector must match task.id" if prefix == "task_id" && payload != data.dig("task", "id")
        branch = data.dig("repo", "task_branch").to_s
        issues << "branch selector must match task_branch" if prefix == "branch" && !branch.empty? && payload != branch
        pr_ref = data.dig("worktree", "pr_ref").to_s
        issues << "pr selector must match pr_ref" if prefix == "pr" && !pr_ref.empty? && payload != pr_ref
      end
      result("L18", issues, "memory retrieval is bounded and task-relevant")
    end

    def lint_l19(document, _attachment, _label, _seen)
      data = document.data
      write = hash(data.dig("memory", "write"))
      issues = []
      if write["mode"] == "allowed"
        issues << "memory write requires allowed_path" if write["allowed_path"].to_s.strip.empty?
        issues << "memory write requires purpose" if write["purpose"].to_s.strip.empty?
        issues << "memory write requires entry_schema=core_v1" unless write["entry_schema"] == "core_v1"
        issues << "memory write requires authorization.class != NONE" if data.dig("authorization", "class") == "NONE"
        issues << "memory write requires risk_class>=MEDIUM" if data.dig("task", "risk_class") == "LOW"
      end
      result("L19", issues, write["mode"] == "allowed" ? "memory write proposal is complete and authorization-gated" : "memory write is forbidden")
    end

    def lint_l20(document, attachment, _label, _seen)
      data = document.data
      issues = []
      required = array(data.dig("project_attachment", "required_context_files"))
      required.each do |path|
        issues << "A_CROSS_PROJECT: invalid context path #{path}" unless relative_under?(path, ".ai/")
      end

      selectors = array(data.dig("memory", "read", "selectors"))
      selectors.each do |selector|
        payload = selector.to_s.split(":", 2)[1].to_s
        if payload.start_with?("/") || payload.include?("..") || payload.match?(/[A-Za-z]:\\/)
          issues << "A_CROSS_PROJECT: selector #{selector.inspect} contains a filesystem escape"
        end
      end

      write_path = data.dig("memory", "write", "allowed_path").to_s
      if !write_path.empty? && !relative_under?(write_path, ".ai/ai-memory/")
        issues << "A_CROSS_PROJECT: memory write path must stay under .ai/ai-memory/"
      end

      if attachment.profile_data
        profile_repo = attachment.profile_data.dig("project", "canonical_repo")
        manifest_repo = data.dig("repo", "project_path")
        issues << "A_CROSS_PROJECT: profile canonical_repo differs from manifest repo.project_path" unless profile_repo == manifest_repo
        profile_context = array(attachment.profile_data.dig("project_attachment", "required_context_files"))
        issues << "A_CROSS_PROJECT: profile required context differs from manifest" unless profile_context == required
        profile_memory = attachment.profile_data.dig("project_attachment", "memory_log_path").to_s
        issues << "A_CROSS_PROJECT: profile memory_log_path escapes repo-local memory" unless relative_under?(profile_memory, ".ai/ai-memory/")
      end
      result("L20", issues, "profile, context, selectors, and memory paths remain project-local")
    end

    def lint_l21(document, _attachment, _label, _seen)
      data = document.data
      issues = []
      issues << "context.live_state_required must be true" unless data.dig("context", "live_state_required") == true
      issues << "context.head_binding_required must be true" unless data.dig("context", "head_binding_required") == true
      issues << "evidence.head_sha_binding must be true" unless data.dig("evidence", "head_sha_binding") == true
      issues << "invalid stale_evidence_policy" unless %w[mark_stale reject].include?(data.dig("context", "stale_evidence_policy"))
      result("L21", issues, "live-state and current-head evidence bindings are fixed true")
    end

    def lint_l22(document, attachment, label, _seen)
      projection = hash(document.data["active_task_projection"])
      enabled = projection["enabled"] == true
      issues = []
      if enabled
        issues << "enabled projection requires output_path" if projection["output_path"].to_s.strip.empty?
        issues << "enabled projection requires manifest_sha256_required=true" unless projection["manifest_sha256_required"] == true
        if attachment.view_path
          if attachment.view_exists
            drift_free, reason = projection_drift(document, attachment.view_raw, source_label: label)
            issues << reason unless drift_free
          else
            issues << "DRIFT: requested projection view does not exist"
          end
        end
      else
        issues << "disabled projection requires empty output_path" unless projection["output_path"].to_s.empty?
        issues << "disabled projection requires manifest_sha256_required=false" unless projection["manifest_sha256_required"] == false
        issues << "DRIFT: disabled projection must not have a view" if attachment.view_path && attachment.view_exists
      end
      message = attachment.view_path ? "projection structure and supplied view are drift-free" : "projection structure is valid; no existing view was supplied for comparison"
      result("L22", issues, message)
    end

    def result(id, issues, success_message)
      problems = array(issues).compact.reject { |entry| entry.to_s.empty? }
      LintResult.new(id: id, passed: problems.empty?, message: problems.empty? ? success_message : problems.join("; "))
    end

    def validate_profile_shape(profile)
      errors = []
      required = %w[project control_plane project_attachment]
      required.each { |key| errors << "profile.#{key} is required" unless profile.key?(key) }
      errors << "profile.project must be a mapping" if profile.key?("project") && !profile["project"].is_a?(Hash)
      errors << "profile.control_plane must be a mapping" if profile.key?("control_plane") && !profile["control_plane"].is_a?(Hash)
      errors << "profile.project_attachment must be a mapping" if profile.key?("project_attachment") && !profile["project_attachment"].is_a?(Hash)
      errors
    end

    def entry_check_shape?(data)
      data.dig("task", "type") == "entry_check" &&
        data.dig("task", "risk_class") == "LOW" &&
        data.dig("worktree", "mode") == "NOT_APPLICABLE" &&
        array(data.dig("scope", "allowed_files")).empty? &&
        data.dig("policies", "db") == "none" &&
        data.dig("policies", "external_side_effects") == "none" &&
        data.dig("policies", "pr") == "none" &&
        data.dig("policies", "merge") == "none"
    end

    def worker_replacements(document, label)
      data = document.data
      required_context = array(data.dig("project_attachment", "required_context_files")).map { |item| "- #{item}" }.join("\n   ")
      allowed = bullet_lines(array(data.dig("scope", "allowed_files")), empty: "(none; read-only)")
      protected_paths = bullet_lines(array(data.dig("scope", "protected_paths")), empty: "(none)")
      steps = numbered_lines(array(data["steps"]))
      success = array(data["success_criteria"]).join("\n- ")
      tests = array(data["tests"]).map do |test|
        item = hash(test)
        "- #{item['name']}: #{item['cmd']} [side_effects_allowed: #{item['side_effects_allowed']}]"
      end.join("\n")
      pins = array(data.dig("scope", "pins")).select { |pin| hash(pin)["type"] == "invariant" }.map { |pin| hash(pin)["path"] }

      {
        "authorization.token" => data.dig("authorization", "token"),
        "authorization.high_risk_reason" => data.dig("authorization", "high_risk_reason"),
        "task.id" => data.dig("task", "id"),
        "task.name" => data.dig("task", "name"),
        "task.type" => data.dig("task", "type"),
        "task.risk_class" => data.dig("task", "risk_class"),
        "task.routing_path" => data.dig("task", "routing_path"),
        "control_plane.version" => data.dig("control_plane", "version"),
        "control_plane.source_path" => data.dig("control_plane", "source_path"),
        "manifest_sha256" => document.sha256,
        "repo.project_path" => data.dig("repo", "project_path"),
        "repo.base_branch" => data.dig("repo", "base_branch"),
        "repo.base_commit" => data.dig("repo", "base_commit"),
        "repo.task_branch" => data.dig("repo", "task_branch"),
        "worktree.mode" => data.dig("worktree", "mode"),
        "worktree.pr_ref" => data.dig("worktree", "pr_ref"),
        "worktree.path" => data.dig("worktree", "path"),
        "project_attachment.required_context_files" => required_context,
        "project_attachment.missing_context_policy" => data.dig("project_attachment", "missing_context_policy"),
        "project_attachment.profile_path" => data.dig("project_attachment", "profile_path"),
        "memory.read.mode" => data.dig("memory", "read", "mode"),
        "memory.read.selectors" => array(data.dig("memory", "read", "selectors")).join(", "),
        "memory.read.max_entries" => data.dig("memory", "read", "max_entries"),
        "worktree_mode_block" => worktree_mode_block(data.dig("worktree", "mode"), data.dig("worktree", "reattach") == true),
        "goal" => data["goal"],
        "scope.allowed_files" => allowed,
        "scope.protected_paths" => protected_paths,
        "scope.forbidden_subsystems" => inline_list(array(data.dig("scope", "forbidden_subsystems"))),
        "policies.db" => data.dig("policies", "db"),
        "policies.runtime" => data.dig("policies", "runtime"),
        "policies.external_side_effects" => data.dig("policies", "external_side_effects"),
        "memory.write.allowed_path" => data.dig("memory", "write", "allowed_path"),
        "memory.write.purpose" => data.dig("memory", "write", "purpose"),
        "steps" => steps,
        "tests" => tests,
        "invariant_pins" => pins.empty? ? "(none)" : pins.join(", "),
        "success_criteria" => success,
        "manifest_yaml" => document.raw.dup.force_encoding(Encoding::UTF_8).sub(/\n*\z/, ""),
        "source_path" => label
      }
    end

    def render_active_task_unchecked(document, label)
      data = document.data
      scope = hash(data["scope"])
      worktree = hash(data["worktree"])
      repo = hash(data["repo"])
      tests = array(data["tests"]).map do |test|
        item = hash(test)
        "  - #{item['name']}: #{item['cmd']} [required=#{item['required']}; side_effects_allowed=#{item['side_effects_allowed']}]"
      end
      tests = ["  - (none)"] if tests.empty?
      repo_summary = "project=#{repo['project_path']}; base=#{repo['base_branch']}@#{repo['base_commit']}; branch=#{empty_label(repo['task_branch'])}"
      worktree_summary = "mode=#{worktree['mode']}; path=#{empty_label(worktree['path'])}; reattach=#{worktree['reattach']}; pr_ref=#{empty_label(worktree['pr_ref'])}"
      status = hash(data["status"])

      normalize_output([
        "<!-- AUTO-GENERATED COMPATIBILITY VIEW — DO NOT EDIT -->",
        "<!-- source: #{label} -->",
        "<!-- manifest_sha256: #{document.sha256} -->",
        "<!-- compiled_from: control_plane #{data.dig('control_plane', 'version')} -->",
        "",
        "# Active Task: #{data.dig('task', 'id')} — #{data.dig('task', 'name')}",
        "- Type / Risk / Path: #{data.dig('task', 'type')} / #{data.dig('task', 'risk_class')} / #{data.dig('task', 'routing_path')}",
        "- Goal: #{data['goal']}",
        "- Repo / Base / Branch: #{repo_summary}",
        "- Worktree: #{worktree_summary}",
        "- Allowed: #{inline_list(array(scope['allowed_files']))}",
        "- Protected: #{inline_list(array(scope['protected_paths']))}",
        "- Forbidden: #{inline_list(array(scope['forbidden_subsystems']))}",
        "- Tests:",
        *tests,
        "- Memory: read=#{data.dig('memory', 'read', 'mode')} write=#{data.dig('memory', 'write', 'mode')}",
        "- Authorization: #{data.dig('authorization', 'class')} — #{data.dig('authorization', 'token')}",
        "- Status: worker_final=#{status['worker_final']}; review_verdict=#{status['review_verdict']}; lifecycle_state=#{status['lifecycle_state']}"
      ].join("\n"))
    end

    def extract_authorization_block(template, klass)
      letter = { "NONE" => "A", "SINGLE_PROMPT" => "B", "STANDALONE" => "C" }.fetch(klass)
      match = template.match(/\*\*\[#{letter}\].*?\*\*\s*```\n(.*?)\n```/m)
      raise ArgumentError, "authorization block #{letter} not found" unless match

      match[1]
    end

    def extract_worker_body(template)
      section = extract_level_two_section(template, "§T")
      match = section.match(/### §T-BODY.*?```\n(.*?)\n```/m)
      raise ArgumentError, "§T-BODY fenced template not found" unless match

      match[1]
    end

    def resolve_conditionals(text, values)
      rendered = text.dup
      loop do
        opening = rendered.match(/\{\{if ([^}]+)\}\}/)
        break unless opening

        closing_index = rendered.index("{{end}}", opening.end(0))
        raise ArgumentError, "conditional has no matching {{end}}: #{opening[0]}" unless closing_index

        else_index = rendered.index("{{else}}", opening.end(0))
        else_index = nil if else_index && else_index > closing_index
        truthy = values.fetch(opening[1].strip, false)
        selected = if else_index
                     truthy ? rendered[opening.end(0)...else_index] : rendered[(else_index + "{{else}}".length)...closing_index]
                   else
                     truthy ? rendered[opening.end(0)...closing_index] : ""
                   end
        rendered = rendered[0...opening.begin(0)] + selected + rendered[(closing_index + "{{end}}".length)..-1].to_s
      end
      rendered
    end

    def worktree_mode_block(mode, reattach)
      routing = File.read(root.join("ROUTING_AND_LIFECYCLE.md"), encoding: "UTF-8")
      section = extract_level_two_section(routing, "§4")
      marker = "**Mode #{mode}**"
      start = section.index(marker)
      raise ArgumentError, "worktree mode block not found: #{mode}" unless start

      tail = section[start..-1]
      next_marker = tail.index(/\n\*\*(?:Mode |reattach:)/, marker.length)
      block = next_marker ? tail[0...next_marker] : tail
      if reattach
        reattach_start = section.index("**reattach: true**")
        raise ArgumentError, "reattach block not found" unless reattach_start
        block = [block, section[reattach_start..-1]].join("\n\n")
      end
      block.strip
    end

    def extract_level_two_section(text, key)
      lines = text.lines
      start = lines.index { |line| line.match?(/^## #{Regexp.escape(key)}(?:\s|$)/) }
      raise ArgumentError, "section #{key} not found" unless start

      finish = ((start + 1)...lines.length).find { |index| lines[index].start_with?("## ") } || lines.length
      lines[start...finish].join.strip
    end

    def source_section(source, content)
      "<!-- BEGIN SOURCE: #{source} -->\n#{content}\n<!-- END SOURCE: #{source} -->"
    end

    def safe_join(root_path, relative)
      return nil unless relative.is_a?(String) && relative_under?(relative, ".ai/")

      root_path.join(relative)
    end

    def relative_under?(path, prefix)
      return false unless path.is_a?(String) && !path.empty?
      candidate = Pathname.new(path)
      return false if candidate.absolute? || candidate.each_filename.to_a.include?("..")

      clean = candidate.cleanpath.to_s
      clean.start_with?(prefix) && clean != prefix.sub(/\/$/, "")
    rescue ArgumentError
      false
    end

    def nonempty_string_list?(value)
      value.is_a?(Array) && !value.empty? && value.all? { |item| item.is_a?(String) && !item.strip.empty? }
    end

    def normalized_token(value)
      value.to_s.downcase.gsub(/[^a-z0-9]+/, "_").gsub(/\A_+|_+\z/, "")
    end

    def hash(value)
      value.is_a?(Hash) ? value : {}
    end

    def array(value)
      value.is_a?(Array) ? value : []
    end

    def bullet_lines(values, empty:)
      return "- #{empty}" if values.empty?

      values.map { |item| "- #{item}" }.join("\n")
    end

    def numbered_lines(values)
      values.each_with_index.map { |item, index| "#{index + 1}. #{item}" }.join("\n")
    end

    def inline_list(values)
      values.empty? ? "(none)" : values.join(", ")
    end

    def empty_label(value)
      value.to_s.empty? ? "(none)" : value
    end

    def normalize_output(text)
      text.to_s.gsub("\r\n", "\n").sub(/\n*\z/, "") + "\n"
    end

    def byte_equal?(left, right)
      left.to_s.b == right.to_s.b
    end
  end

  class CLI
    def initialize(argv, stdout: $stdout, stderr: $stderr)
      @argv = argv.dup
      @stdout = stdout
      @stderr = stderr
      @toolchain = Toolchain.new
    end

    def run
      command = @argv.shift
      case command
      when "validate-schema" then validate_schema_command
      when "lint" then lint_command
      when "render-worker" then render_worker_command
      when "render-active-task" then render_active_task_command
      when "check-active-task" then check_active_task_command
      when "render-role" then render_role_command
      when "manifest-sha" then manifest_sha_command
      else
        @stderr.puts usage
        2
      end
    rescue ValidationFailure => error
      @stderr.puts(error.message)
      1
    rescue OptionParser::ParseError, ArgumentError, Errno::ENOENT => error
      @stderr.puts(error.message)
      2
    end

    private

    def validate_schema_command
      manifest = required_argument!("MANIFEST")
      ensure_no_extra_arguments!
      document = @toolchain.load_manifest(manifest)
      errors = @toolchain.validate_schema(document.data)
      @stdout.puts JSON.pretty_generate("status" => errors.empty? ? "PASS" : "FAIL", "errors" => errors)
      errors.empty? ? 0 : 1
    end

    def lint_command
      manifest = required_argument!("MANIFEST")
      options = parse_common_options(@argv)
      require_project_root!(options)
      document = @toolchain.load_manifest(manifest)
      report = @toolchain.lint(
        document,
        project_root: options[:project_root],
        profile_path: options[:profile],
        view_path: options[:view],
        source_label: options[:source_label],
        seen_task_ids: options.fetch(:seen_task_ids, [])
      )
      @stdout.puts JSON.pretty_generate(report.to_h)
      report.success? ? 0 : 1
    end

    def render_worker_command
      manifest = required_argument!("MANIFEST")
      options = parse_common_options(@argv, allow_view: false)
      require_project_root!(options)
      document = @toolchain.load_manifest(manifest)
      @stdout.write @toolchain.render_worker(
        document,
        project_root: options[:project_root],
        profile_path: options[:profile],
        source_label: options[:source_label],
        seen_task_ids: options.fetch(:seen_task_ids, [])
      )
      0
    end

    def render_active_task_command
      manifest = required_argument!("MANIFEST")
      options = parse_common_options(@argv, allow_view: false)
      require_project_root!(options)
      document = @toolchain.load_manifest(manifest)
      rendered = @toolchain.render_active_task(
        document,
        project_root: options[:project_root],
        profile_path: options[:profile],
        source_label: options[:source_label],
        seen_task_ids: options.fetch(:seen_task_ids, [])
      )
      @stdout.write(rendered) if rendered
      0
    end

    def check_active_task_command
      manifest = required_argument!("MANIFEST")
      view = required_argument!("VIEW")
      options = parse_common_options(@argv, allow_view: false)
      require_project_root!(options)
      document = @toolchain.load_manifest(manifest)
      report = @toolchain.lint(
        document,
        project_root: options[:project_root],
        profile_path: options[:profile],
        view_path: view,
        source_label: options[:source_label],
        seen_task_ids: options.fetch(:seen_task_ids, [])
      )
      l22 = report.result("L22")
      status = if report.success?
                 "PASS"
               elsif !l22.passed
                 "DRIFT"
               else
                 "FAIL"
               end
      @stdout.puts JSON.pretty_generate(
        "status" => status,
        "message" => l22.message,
        "manifest_sha256" => document.sha256,
        "lint_status" => report.success? ? "PASS" : "FAIL"
      )
      report.success? ? 0 : 1
    end

    def render_role_command
      role = required_argument!("ROLE")
      ensure_no_extra_arguments!
      @stdout.write @toolchain.render_compiled_role(role)
      0
    end

    def manifest_sha_command
      manifest = required_argument!("MANIFEST")
      ensure_no_extra_arguments!
      @stdout.puts @toolchain.load_manifest(manifest).sha256
      0
    end

    def parse_common_options(arguments, allow_view: true)
      options = {}
      parser = OptionParser.new do |flags|
        flags.on("--project-root PATH") { |value| options[:project_root] = value }
        flags.on("--profile PATH") { |value| options[:profile] = value }
        flags.on("--source-label LABEL") { |value| options[:source_label] = value }
        flags.on("--seen-task-id ID") { |value| (options[:seen_task_ids] ||= []) << value }
        flags.on("--view PATH") { |value| options[:view] = value } if allow_view
      end
      parser.parse!(arguments)
      raise OptionParser::InvalidArgument, "unexpected arguments: #{arguments.join(' ')}" unless arguments.empty?

      options
    end

    def require_project_root!(options)
      raise OptionParser::MissingArgument, "--project-root is required for full L1-L22 lint/render" unless options[:project_root]
    end

    def required_argument!(label)
      value = @argv.shift
      raise OptionParser::MissingArgument, label unless value

      value
    end

    def ensure_no_extra_arguments!
      raise OptionParser::InvalidArgument, "unexpected arguments: #{@argv.join(' ')}" unless @argv.empty?
    end

    def usage
      <<~TEXT
        Usage:
          ruby prototype/control_plane.rb validate-schema MANIFEST
          ruby prototype/control_plane.rb lint MANIFEST --project-root ROOT [--profile PATH] [--view PATH] [--source-label LABEL] [--seen-task-id ID]
          ruby prototype/control_plane.rb render-worker MANIFEST --project-root ROOT [--profile PATH] [--source-label LABEL] [--seen-task-id ID]
          ruby prototype/control_plane.rb render-active-task MANIFEST --project-root ROOT [--profile PATH] [--source-label LABEL] [--seen-task-id ID]
          ruby prototype/control_plane.rb check-active-task MANIFEST VIEW --project-root ROOT [--profile PATH] [--source-label LABEL] [--seen-task-id ID]
          ruby prototype/control_plane.rb render-role handoff|planner
          ruby prototype/control_plane.rb manifest-sha MANIFEST

        All commands are read-only. Rendered artifacts go to stdout; this prototype never writes a repo, project, DB, remote, or runtime target.
      TEXT
    end
  end
end

if $PROGRAM_NAME == __FILE__
  exit ControlPlanePrototype::CLI.new(ARGV).run
end
