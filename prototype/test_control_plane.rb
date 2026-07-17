# frozen_string_literal: true

require "digest"
require "fileutils"
require "minitest/autorun"
require "open3"
require "pathname"
require "rbconfig"
require "stringio"
require "tmpdir"
require "yaml"

require_relative "control_plane"

class ControlPlanePrototypeTest < Minitest::Test
  ROOT = ControlPlanePrototype::ROOT
  FIXTURES = ROOT.join("prototype/fixtures")
  EXAMPLES = {
    "low" => ROOT.join("examples/manifest-low.yaml"),
    "medium" => ROOT.join("examples/manifest-medium.yaml"),
    "high" => ROOT.join("examples/manifest-high.yaml")
  }.freeze

  ROLE_SECTIONS = {
    "handoff" => "R1",
    "ceo" => "R3",
    "cto" => "R2",
    "planner" => "R4"
  }.freeze

  LEGACY_PROMPTS = {
    "handoff" => {
      path: ROOT.parent.join("[對話交接報告]— ChatGPT 對話總結.md"),
      bytes: 11_830,
      lines: 487,
      sha256: "11d7bbd9138caf19c41f2f94f250818a21e1b15a050610e254dbd0f9ea38f846"
    },
    "ceo" => {
      path: ROOT.parent.join("[CEO] Decision Review Prompt .md"),
      bytes: 16_679,
      lines: 709,
      sha256: "bbedaa3dc4652eae5b376a042d6cac99ac50182de025caf934f69afe9da1a695"
    },
    "cto" => {
      path: ROOT.parent.join("[CTO] Technical Review Prompt .md"),
      bytes: 14_041,
      lines: 593,
      sha256: "5611f4eda4b184a506f661581c6563f45e110b47cb7ff3fe97935e29bf014dae"
    },
    "planner" => {
      path: ROOT.parent.join("Personal Planner Handoff Prompt.md"),
      bytes: 29_226,
      lines: 1_090,
      sha256: "9bb4bd4682e30106ac1822f159742b25dcd9b9c5855c9b7414839b063a4f2743"
    }
  }.freeze

  LEGACY_MAPPING_IDS = {
    "handoff" => (1..24).map { |number| format("H%02d", number) },
    "ceo" => (1..35).map { |number| format("C%02d", number) },
    "cto" => (1..31).map { |number| format("T%02d", number) },
    "planner" => (1..32).map { |number| format("P%02d", number) }
  }.freeze

  LEGACY_MAPPING_CLASSIFICATIONS = %w[
    RETAINED_IN_ROLE MOVED_TO_SHARED_CORE MOVED_TO_ROUTING MOVED_TO_MANIFEST
    REMOVED_AS_DUPLICATE REMOVED_AS_ROLE_VIOLATION DEFERRED_FOR_COMPATIBILITY UNKNOWN
  ].freeze

  def setup
    @toolchain = ControlPlanePrototype::Toolchain.new
  end

  def test_three_examples_pass_schema_and_all_lints_with_read_only_attachment_fixtures
    EXAMPLES.each do |name, path|
      document = @toolchain.load_manifest(path)
      assert_empty @toolchain.validate_schema(document.data), "#{name} schema"

      Dir.mktmpdir("control-plane-positive-") do |tmpdir|
        project_root, profile_path = materialize_attachment(tmpdir, document.data)
        report = @toolchain.lint(
          document,
          project_root: project_root,
          profile_path: profile_path,
          source_label: "examples/manifest-#{name}.yaml"
        )
        assert report.success?, "#{name} lint failed: #{report.to_h}"
        assert_equal (1..22).map { |number| "L#{number}" }, report.results.map(&:id)
      end
    end
  end

  def test_each_l1_through_l22_negative_fixture_is_rejected_by_its_named_rule
    fixture = load_yaml(FIXTURES.join("lint_negative_cases.yaml"))
    cases = fixture.fetch("cases")
    assert_equal 22, cases.length
    assert_equal (1..22).map { |number| "L#{number}" }, cases.map { |entry| entry.fetch("rule") }

    cases.each do |entry|
      base_path = EXAMPLES.fetch(entry.fetch("base"))
      data = deep_copy(load_yaml(base_path))
      apply_mutations(data, entry["manifest_mutations"] || {})

      Dir.mktmpdir("control-plane-negative-") do |tmpdir|
        manifest_path = Pathname.new(tmpdir).join("manifest.yaml")
        File.write(manifest_path, YAML.dump(data), mode: "w:UTF-8")
        project_root, profile_path = materialize_attachment(
          tmpdir,
          data,
          attachment: entry.fetch("attachment", "valid"),
          profile_location: entry["profile_location"],
          profile_mutations: entry["profile_mutations"] || {}
        )
        view_path = nil
        if entry["view"] == "tampered"
          view_path = Pathname.new(tmpdir).join("active_task.md")
          FileUtils.cp(FIXTURES.join("tampered_active_task.md"), view_path)
        end

        document = @toolchain.load_manifest(manifest_path)
        report = @toolchain.lint(
          document,
          project_root: project_root,
          profile_path: profile_path,
          view_path: view_path,
          source_label: "fixture/#{entry.fetch('rule')}.yaml"
        )
        named_result = report.result(entry.fetch("rule"))
        refute named_result.passed, "#{entry.fetch('rule')} fixture was not rejected: #{report.to_h}"
        refute_includes named_result.message, "cannot evaluate safely", "#{entry.fetch('rule')} failed through an evaluator exception"
      end
    end
  end

  def test_profile_shared_core_redefinition_is_ignored_and_reported_as_risk
    data = deep_copy(load_yaml(EXAMPLES.fetch("low")))
    Dir.mktmpdir("control-plane-profile-risk-") do |tmpdir|
      project_root, profile_path = materialize_attachment(
        tmpdir,
        data,
        profile_mutations: { "authorization" => { "matrix" => "overridden" } }
      )
      report = @toolchain.lint(
        @toolchain.load_manifest(EXAMPLES.fetch("low")),
        project_root: project_root,
        profile_path: profile_path
      )
      l15 = report.result("L15")
      refute l15.passed
      assert_includes l15.message, "[Risk]"
      assert_includes l15.message, "ignored"
    end
  end

  def test_schema_negative_fixture_removes_required_field_and_is_rejected
    fixture = load_yaml(FIXTURES.join("schema_negative_case.yaml"))
    data = deep_copy(load_yaml(EXAMPLES.fetch(fixture.fetch("base"))))
    fixture.fetch("delete_paths").each { |path| delete_path(data, path) }
    errors = @toolchain.validate_schema(data)
    refute_empty errors
    assert errors.any? { |error| error.include?("$.task.name is required") }, errors.inspect
  end

  def test_worker_render_is_byte_deterministic_slot_complete_and_mode_specific
    document = @toolchain.load_manifest(EXAMPLES.fetch("medium"))
    Dir.mktmpdir("control-plane-worker-") do |tmpdir|
      project_root, profile_path = materialize_attachment(tmpdir, document.data)
      arguments = {
        project_root: project_root,
        profile_path: profile_path,
        source_label: "examples/manifest-medium.yaml"
      }
      first = @toolchain.render_worker(document, **arguments)
      second = @toolchain.render_worker(document, **arguments)
      assert_equal first.b, second.b
      refute_match(/\{\{.*?\}\}/m, first)
      assert_includes first, "manifest_sha256: #{document.sha256}"
      assert_includes first, "PENDING_OWNER_TOKEN"
      assert_includes first, "WORKER_WAITING_OWNER"
      assert_includes first, "**Mode REUSABLE**"
      refute_includes first, "**Mode NOT_APPLICABLE**"
      refute_includes first, "**Mode EPHEMERAL**"
      assert_equal 1, first.scan("Post-Merge Branch Cleanup Gate").length
      assert_equal 1, first.scan("=== 附錄:Task Manifest 原文").length
    end
  end

  def test_all_authorization_and_mode_variants_render_only_the_selected_blocks
    expectations = {
      "low" => ["No Owner Authorization required", "**Mode NOT_APPLICABLE**"],
      "medium" => ["Owner Authorization: PENDING_OWNER_TOKEN", "**Mode REUSABLE**"],
      "high" => ["Standalone Owner Authorization required", "**Mode REUSABLE**"]
    }
    EXAMPLES.each do |name, path|
      document = @toolchain.load_manifest(path)
      Dir.mktmpdir("control-plane-variant-") do |tmpdir|
        project_root, profile_path = materialize_attachment(tmpdir, document.data)
        rendered = @toolchain.render_worker(
          document,
          project_root: project_root,
          profile_path: profile_path,
          source_label: "examples/manifest-#{name}.yaml"
        )
        expectations.fetch(name).each { |expected| assert_includes rendered, expected }
      end
    end
  end

  def test_failed_lint_blocks_worker_render
    data = deep_copy(load_yaml(EXAMPLES.fetch("medium")))
    data["worktree"]["path"] = ""
    Dir.mktmpdir("control-plane-render-block-") do |tmpdir|
      manifest_path = Pathname.new(tmpdir).join("invalid.yaml")
      File.write(manifest_path, YAML.dump(data), mode: "w:UTF-8")
      project_root, profile_path = materialize_attachment(tmpdir, data)
      error = assert_raises(ControlPlanePrototype::ValidationFailure) do
        @toolchain.render_worker(
          @toolchain.load_manifest(manifest_path),
          project_root: project_root,
          profile_path: profile_path
        )
      end
      assert_includes error.message, '"id": "L5"'
      assert_includes error.message, '"status": "FAIL"'
    end
  end

  def test_active_task_projection_is_deterministic_and_detects_content_and_sha_drift
    document = @toolchain.load_manifest(EXAMPLES.fetch("medium"))
    Dir.mktmpdir("control-plane-projection-") do |tmpdir|
      project_root, profile_path = materialize_attachment(tmpdir, document.data)
      arguments = {
        project_root: project_root,
        profile_path: profile_path,
        source_label: "examples/manifest-medium.yaml"
      }
      first = @toolchain.render_active_task(document, **arguments)
      second = @toolchain.render_active_task(document, **arguments)
      assert_equal first.b, second.b
      assert_includes first, "<!-- manifest_sha256: #{document.sha256} -->"

      drift_free, message = @toolchain.projection_drift(document, first, source_label: arguments[:source_label])
      assert drift_free, message

      tampered = first.sub("- Goal:", "- Goal: TAMPERED ")
      drift_free, message = @toolchain.projection_drift(document, tampered, source_label: arguments[:source_label])
      refute drift_free
      assert_includes message, "DRIFT"

      changed_raw = document.raw + "\n# hash-only fixture change\n"
      changed_path = Pathname.new(tmpdir).join("changed-manifest.yaml")
      File.binwrite(changed_path, changed_raw)
      changed_document = @toolchain.load_manifest(changed_path)
      drift_free, message = @toolchain.projection_drift(changed_document, first, source_label: arguments[:source_label])
      refute drift_free
      assert_includes message, "manifest_sha256"
    end
  end

  def test_disabled_projection_returns_no_view_and_rejects_an_existing_view
    document = @toolchain.load_manifest(EXAMPLES.fetch("low"))
    Dir.mktmpdir("control-plane-disabled-projection-") do |tmpdir|
      project_root, profile_path = materialize_attachment(tmpdir, document.data)
      assert_nil @toolchain.render_active_task(document, project_root: project_root, profile_path: profile_path)

      view_path = Pathname.new(tmpdir).join("unexpected-active-task.md")
      File.write(view_path, "unexpected\n", mode: "w:UTF-8")
      report = @toolchain.lint(
        document,
        project_root: project_root,
        profile_path: profile_path,
        view_path: view_path
      )
      refute report.result("L22").passed
      assert_includes report.result("L22").message, "DRIFT"
    end
  end

  def test_all_four_compiled_roles_are_deterministic_identified_durable_source_extractions
    ControlPlanePrototype::COMPILED_ROLE_FILES.each_key do |role|
      first = @toolchain.render_compiled_role(role)
      second = @toolchain.render_compiled_role(role)
      assert_equal first.b, second.b
      assert_includes first, "GENERATED_BUILD_OUTPUT"
      assert_includes first, "DRAFT_FOR_OWNER_REVIEW"
      assert_includes first, "DO_NOT_EDIT"
      assert_includes first, "control_plane_version: #{@toolchain.control_plane_version}"
      assert_includes first, "schema_version: #{@toolchain.schema_version}"
      assert_includes first, "durable_source_fingerprint: #{@toolchain.durable_source_fingerprint}"
      assert_includes first, "contains no project memory"
      @toolchain.durable_source_identities.each do |identity|
        marker = "source_file_identity: #{identity.fetch('name')} bytes=#{identity.fetch('bytes')} sha256=#{identity.fetch('sha256')}"
        assert_includes first, marker
      end
      assert_includes first, "BEGIN SOURCE: ROLE_PROFILES.md SECTION: #{ROLE_SECTIONS.fetch(role)}"
      assert @toolchain.validate_compiled_role(role, first).success?
    end
  end

  def test_role_renderer_needs_no_documentary_compiled_files_and_changes_with_durable_bytes
    Dir.mktmpdir("control-plane-role-sources-") do |tmpdir|
      isolated_root = Pathname.new(tmpdir).join("control-plane")
      FileUtils.mkdir_p(isolated_root)
      ControlPlanePrototype::DURABLE_SOURCE_FILES.each do |source|
        FileUtils.cp(ROOT.join(source), isolated_root.join(source))
      end
      refute isolated_root.join("compiled").exist?

      before = ControlPlanePrototype::Toolchain.new(isolated_root).render_compiled_role("planner")
      role_path = isolated_root.join("ROLE_PROFILES.md")
      File.open(role_path, "a:UTF-8") { |file| file.write("\n<!-- durable-byte-change-fixture -->\n") }
      after_toolchain = ControlPlanePrototype::Toolchain.new(isolated_root)
      after = after_toolchain.render_compiled_role("planner")

      refute_equal before.b, after.b
      changed_digest = Digest::SHA256.file(role_path).hexdigest
      assert_match(/source_file_identity: ROLE_PROFILES\.md bytes=\d+ sha256=#{changed_digest}/, after)
    end
  end

  def test_fresh_role_builds_equal_the_committed_generated_outputs
    ControlPlanePrototype::COMPILED_ROLE_FILES.each do |role, relative_path|
      path = ROOT.join(relative_path)
      assert path.file?, "missing generated output #{relative_path}"
      actual = File.binread(path)
      expected = @toolchain.render_compiled_role(role)
      assert_equal expected.b, actual.b, "#{role} committed output is stale"
      assert @toolchain.validate_compiled_role(role, actual).success?, "#{role} semantic validation"
    end
  end

  def test_role_boundaries_keep_planner_as_the_only_manifest_and_complete_worker_prompt_compiler
    outputs = ControlPlanePrototype::COMPILED_ROLE_FILES.keys.to_h do |role|
      [role, @toolchain.render_compiled_role(role)]
    end
    worker_template_marker = "BEGIN SOURCE: WORKER_TASK_TEMPLATE.md SECTION: §T"
    manifest_marker = "BEGIN SOURCE: TASK_MANIFEST.schema.yaml SECTION: WHOLE_FILE"
    lifecycle_mode_marker = "BEGIN SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §4"

    %w[handoff ceo cto].each do |role|
      refute_includes outputs.fetch(role), worker_template_marker
      refute_includes outputs.fetch(role), manifest_marker
      refute_includes outputs.fetch(role), lifecycle_mode_marker
    end

    assert_includes outputs.fetch("handoff"), "產生完整 Worker prompt 或 task manifest"
    assert_includes outputs.fetch("handoff"), "簽發、填入或暗示 Owner authorization token"
    assert_includes outputs.fetch("ceo"), "實作、merge / push、DB、deployment、registry、cleanup"
    assert_includes outputs.fetch("ceo"), "寫 `active_task.md`(一律禁止)"
    assert_includes outputs.fetch("cto"), "實作、修 bug、merge / push / branch 操作、DB write、cleanup 執行"
    assert_includes outputs.fetch("cto"), "Handoff Reporter / CTO / CEO / Reviewer 不得簽發、填入或代轉 token"

    planner = outputs.fetch("planner")
    assert_includes planner, worker_template_marker
    assert_includes planner, manifest_marker
    assert_includes planner, lifecycle_mode_marker
    assert_includes planner, "PENDING_OWNER_TOKEN"
    assert_includes planner, "**唯一** manifest 作者"
    assert_includes planner, "**唯一** Worker prompt 渲染者"
    outputs.each_value { |output| refute_match(/Owner Authorization:\s+AUTHORIZE_[A-Z0-9_]+/, output) }
  end

  def test_all_roles_share_byte_identical_evidence_risk_authorization_precedence_memory_and_lifecycle_blocks
    outputs = ControlPlanePrototype::COMPILED_ROLE_FILES.keys.to_h do |role|
      [role, @toolchain.render_compiled_role(role)]
    end
    blocks = [
      ["AGENT_CORE.md", "§1"],
      ["AGENT_CORE.md", "§2"],
      ["AGENT_CORE.md", "§6"],
      ["AGENT_CORE.md", "§7"],
      ["AGENT_CORE.md", "§13"],
      ["AGENT_CORE.md", "§14"],
      ["ROUTING_AND_LIFECYCLE.md", "§6"]
    ]
    blocks.each do |source, section|
      values = outputs.transform_values { |raw| compiled_section(raw, source, section) }
      refute values.value?(nil), "missing shared block #{source} #{section}: #{values.inspect}"
      assert_equal 1, values.values.map(&:b).uniq.length, "shared block diverged: #{source} #{section}"
    end
  end

  def test_twelve_negative_role_semantic_fixtures_fail_with_exact_codes_and_no_source_mutation
    fixture = load_yaml(FIXTURES.join("role_semantic_negative_cases.yaml"))
    cases = fixture.fetch("cases")
    assert_equal 12, cases.length

    Dir.mktmpdir("control-plane-role-negative-") do |tmpdir|
      cases.each do |entry|
        before = repository_file_hashes
        role = entry.fetch("role")
        valid = @toolchain.render_compiled_role(role)
        block = compiled_section(valid, entry.fetch("source"), entry.fetch("section"))
        refute_nil block, "#{entry.fetch('name')} source block is missing"
        assert_includes block, entry.fetch("find"), "#{entry.fetch('name')} mutation target is missing"
        mutated_block = block.sub(entry.fetch("find"), entry.fetch("replace"))
        refute_equal block, mutated_block, "#{entry.fetch('name')} mutation did not change bytes"
        invalid = valid.sub(block, mutated_block)
        path = Pathname.new(tmpdir).join("#{entry.fetch('name')}.compiled.md")
        File.binwrite(path, invalid)
        output = StringIO.new
        error = StringIO.new
        exit_code = ControlPlanePrototype::CLI.new(
          ["validate-role", role, path.to_s],
          stdout: output,
          stderr: error
        ).run

        assert_equal 1, exit_code, "#{entry.fetch('name')} must return non-zero: #{error.string}"
        parsed = JSON.parse(output.string)
        assert_equal "FAIL", parsed.fetch("status")
        assert_equal [entry.fetch("expected_error")], parsed.fetch("errors").map { |item| item.fetch("code") }
        assert_equal before, repository_file_hashes, "#{entry.fetch('name')} mutated a source file"
      end
    end
  end

  def test_legacy_prompt_identities_match_the_authorized_pre_task_baseline
    LEGACY_PROMPTS.each do |name, identity|
      path = identity.fetch(:path)
      assert path.file?, "missing legacy prompt #{name}: #{path}"
      raw = File.binread(path)
      assert_equal identity.fetch(:bytes), raw.bytesize, "#{name} byte count"
      assert_equal identity.fetch(:lines), raw.count("\n"), "#{name} line count"
      assert_equal identity.fetch(:sha256), Digest::SHA256.hexdigest(raw), "#{name} SHA-256"
    end
  end

  def test_legacy_major_section_mapping_has_exactly_one_allowed_classification_per_section
    spec = File.read(ROOT.join("R2_SPEC.md"), encoding: "UTF-8")
    mapping = spec[/<!-- BEGIN FOUR-ROLE LEGACY MAPPING -->(.*?)<!-- END FOUR-ROLE LEGACY MAPPING -->/m, 1]
    refute_nil mapping, "R2_SPEC.md lacks the four-role mapping block"
    rows = mapping.lines.each_with_object([]) do |line, memo|
      parts = line.split("|", -1).map(&:strip)
      next unless parts[1] && parts[1].match?(/\A[HCTP]\d{2}\z/)

      memo << { id: parts[1], section: parts[2], classification: parts[3], destination: parts[4] }
    end
    expected_ids = LEGACY_MAPPING_IDS.values.flatten
    assert_equal expected_ids.sort, rows.map { |row| row.fetch(:id) }.sort
    assert_equal rows.length, rows.map { |row| row.fetch(:id) }.uniq.length
    rows.each do |row|
      assert_includes LEGACY_MAPPING_CLASSIFICATIONS, row.fetch(:classification), row.inspect
      refute_empty row.fetch(:section), row.inspect
      refute_empty row.fetch(:destination), row.inspect
    end
    refute_includes rows.map { |row| row.fetch(:classification) }, "UNKNOWN"
  end

  def test_compiled_outputs_have_balanced_fences_and_a_valid_heading_hierarchy
    ControlPlanePrototype::COMPILED_ROLE_FILES.each_key do |role|
      raw = @toolchain.render_compiled_role(role)
      assert_equal 0, raw.lines.count { |line| line.start_with?("```") } % 2, "#{role} unbalanced fences"
      levels = raw.lines.each_with_object([]) do |line, memo|
        marker = line[/\A(#+)\s+/, 1]
        memo << marker.length if marker
      end
      assert_equal 1, levels.first, "#{role} must start its heading hierarchy at H1"
      levels.each_cons(2) do |left, right|
        assert_operator right, :<=, left + 1, "#{role} heading level jumps from H#{left} to H#{right}"
      end
    end
  end

  def test_cli_render_commands_do_not_modify_repository_files
    before = repository_file_hashes
    output = StringIO.new
    error = StringIO.new
    exit_code = ControlPlanePrototype::CLI.new(["compile-role", "planner"], stdout: output, stderr: error).run
    assert_equal 0, exit_code, error.string
    refute_empty output.string
    assert_equal before, repository_file_hashes
  end

  def test_cli_lint_outputs_machine_readable_l1_through_l22_results
    document = @toolchain.load_manifest(EXAMPLES.fetch("low"))
    Dir.mktmpdir("control-plane-cli-") do |tmpdir|
      project_root, profile_path = materialize_attachment(tmpdir, document.data)
      output = StringIO.new
      error = StringIO.new
      args = [
        "lint",
        EXAMPLES.fetch("low").to_s,
        "--project-root", project_root.to_s,
        "--profile", profile_path.to_s,
        "--source-label", "examples/manifest-low.yaml"
      ]
      exit_code = ControlPlanePrototype::CLI.new(args, stdout: output, stderr: error).run
      assert_equal 0, exit_code, error.string
      parsed = JSON.parse(output.string)
      assert_equal "PASS", parsed.fetch("status")
      assert_equal 22, parsed.fetch("lint").length
    end
  end

  def test_worker_cli_is_byte_deterministic_across_fresh_processes
    document = @toolchain.load_manifest(EXAMPLES.fetch("medium"))
    Dir.mktmpdir("control-plane-cli-determinism-") do |tmpdir|
      project_root, profile_path = materialize_attachment(tmpdir, document.data)
      command = [
        RbConfig.ruby,
        ROOT.join("prototype/control_plane.rb").to_s,
        "render-worker",
        EXAMPLES.fetch("medium").to_s,
        "--project-root", project_root.to_s,
        "--profile", profile_path.to_s,
        "--source-label", "examples/manifest-medium.yaml"
      ]
      first, first_error, first_status = Open3.capture3(*command)
      second, second_error, second_status = Open3.capture3(*command)
      assert first_status.success?, first_error
      assert second_status.success?, second_error
      assert_equal first.b, second.b
      assert_equal document.sha256, first[/manifest_sha256: ([0-9a-f]{64})/, 1]
    end
  end

  def test_l1_duplicate_task_id_is_detected_when_a_seen_set_is_supplied
    document = @toolchain.load_manifest(EXAMPLES.fetch("low"))
    Dir.mktmpdir("control-plane-duplicate-") do |tmpdir|
      project_root, profile_path = materialize_attachment(tmpdir, document.data)
      report = @toolchain.lint(
        document,
        project_root: project_root,
        profile_path: profile_path,
        seen_task_ids: [document.data.dig("task", "id")]
      )
      refute report.result("L1").passed
      assert_includes report.result("L1").message, "duplicate task.id"
    end
  end

  private

  def compiled_section(raw, source, section)
    start_marker = Regexp.escape("<!-- BEGIN SOURCE: #{source} SECTION: #{section} -->")
    end_marker = Regexp.escape("<!-- END SOURCE: #{source} SECTION: #{section} -->")
    raw.match(/#{start_marker}\n(.*?)\n#{end_marker}/m)&.[](1)
  end

  def load_yaml(path)
    YAML.safe_load(File.read(path, encoding: "UTF-8"))
  end

  def deep_copy(value)
    Marshal.load(Marshal.dump(value))
  end

  def apply_mutations(target, mutations)
    mutations.each { |path, value| set_path(target, path, value) }
  end

  def set_path(target, dotted_path, value)
    parts = dotted_path.split(".")
    cursor = target
    parts[0...-1].each do |part|
      cursor = cursor.is_a?(Array) ? cursor.fetch(Integer(part, 10)) : cursor.fetch(part)
    end
    last = parts.last
    if cursor.is_a?(Array)
      cursor[Integer(last, 10)] = value
    else
      cursor[last] = value
    end
  end

  def delete_path(target, dotted_path)
    parts = dotted_path.split(".")
    cursor = target
    parts[0...-1].each do |part|
      cursor = cursor.is_a?(Array) ? cursor.fetch(Integer(part, 10)) : cursor.fetch(part)
    end
    cursor.is_a?(Array) ? cursor.delete_at(Integer(parts.last, 10)) : cursor.delete(parts.last)
  end

  def materialize_attachment(tmpdir, manifest, attachment: "valid", profile_location: nil, profile_mutations: {})
    project_root = Pathname.new(tmpdir).join("project")
    FileUtils.mkdir_p(project_root)
    profile = profile_for(manifest)
    apply_mutations(profile, profile_mutations)
    explicit_profile = nil

    if attachment == "valid"
      manifest.dig("project_attachment", "required_context_files").each do |relative|
        path = project_root.join(relative)
        FileUtils.mkdir_p(path.dirname)
        File.write(path, "fixture only\n", mode: "w:UTF-8")
      end
      profile_path = project_root.join(manifest.dig("project_attachment", "profile_path"))
      FileUtils.mkdir_p(profile_path.dirname)
      File.write(profile_path, YAML.dump(profile), mode: "w:UTF-8")
      explicit_profile = profile_path
    elsif profile_location == "external"
      external_profile = Pathname.new(tmpdir).join("external-profile.yaml")
      File.write(external_profile, YAML.dump(profile), mode: "w:UTF-8")
      explicit_profile = external_profile
    end

    [project_root, explicit_profile]
  end

  def profile_for(manifest)
    {
      "project" => {
        "name" => "fixture-#{manifest.dig('task', 'id')}",
        "canonical_repo" => manifest.dig("repo", "project_path"),
        "canonical_branch" => manifest.dig("repo", "base_branch")
      },
      "control_plane" => deep_copy(manifest.fetch("control_plane")),
      "project_attachment" => {
        "required_context_files" => deep_copy(manifest.dig("project_attachment", "required_context_files")),
        "memory_log_path" => ".ai/ai-memory/MEMORY_LOG.md"
      },
      "risk_domain_overrides" => [],
      "protected_paths" => [],
      "restrictions" => { "db" => [], "data" => [], "runtime" => [] },
      "test_aliases" => { "focused_unit" => "", "smoke" => "" },
      "worktree_defaults" => { "reusable_path" => "", "ephemeral_root" => "" },
      "publication_restrictions" => []
    }
  end

  def repository_file_hashes
    Dir.glob(ROOT.join("**/*").to_s, File::FNM_DOTMATCH).each_with_object({}) do |path, memo|
      next unless File.file?(path)
      next if path.include?("/.git/")

      relative = Pathname.new(path).relative_path_from(ROOT).to_s
      memo[relative] = Digest::SHA256.file(path).hexdigest
    end
  end
end
