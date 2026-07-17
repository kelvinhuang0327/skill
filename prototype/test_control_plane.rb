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

  def test_compiled_handoff_and_planner_are_deterministic_durable_source_extractions
    %w[handoff planner].each do |role|
      first = @toolchain.render_compiled_role(role)
      second = @toolchain.render_compiled_role(role)
      assert_equal first.b, second.b
      assert_includes first, "DRAFT_FOR_OWNER_REVIEW — PROTOTYPE ONLY; NOT ACTIVATED"
      assert_includes first, "contains no project memory"
      ControlPlanePrototype::DURABLE_SOURCE_FILES.each do |source|
        digest = Digest::SHA256.hexdigest(File.binread(ROOT.join(source)))
        assert_includes first, "durable_source_sha256: #{source}=#{digest}"
      end
      expected_role_section = role == "handoff" ? "## R1 HANDOFF_REPORTER" : "## R4 PLANNER_COMPILER"
      assert_includes first, expected_role_section
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
      assert_includes after, "durable_source_sha256: ROLE_PROFILES.md=#{changed_digest}"
    end
  end

  def test_cli_render_commands_do_not_modify_repository_files
    before = repository_file_hashes
    output = StringIO.new
    error = StringIO.new
    exit_code = ControlPlanePrototype::CLI.new(["render-role", "planner"], stdout: output, stderr: error).run
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
