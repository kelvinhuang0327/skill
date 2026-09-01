# frozen_string_literal: true

require 'fileutils'
require 'minitest/autorun'
require 'open3'
require 'pathname'
require 'tmpdir'
require_relative '../scripts/skill_authoring_integrity'

class SkillAuthoringIntegrityTest < Minitest::Test
  SCRIPT = File.expand_path('../scripts/skill_authoring_integrity.rb', __dir__)
  REPOSITORY_ROOT = Pathname.new(File.expand_path('../..', __dir__)).cleanpath
  MANIFEST_PATH = REPOSITORY_ROOT.join('fable-method/platforms.yaml')
  CANONICAL_SKILL = 'fable-method/shared/SKILL.md'

  Validator = Fable::SkillAuthoringIntegrity::Validator
  IntegrityViolation = Fable::SkillAuthoringIntegrity::IntegrityViolation

  def setup
    @registry = Validator.load_registry(MANIFEST_PATH)
    @validator = validator_for(@registry)
  end

  # --- Structural checks against the real, current tree --------------------

  def test_current_repository_has_one_canonical_shared_skill
    assert @validator.validate_canonical_entry!
  end

  def test_current_reference_registry_is_valid
    assert @validator.validate_reference_registry!
  end

  def test_current_repository_has_no_orphan_canonical_references
    assert @validator.validate_no_orphan_references!
  end

  def test_current_registry_has_no_incompatible_source_ownership
    assert @validator.validate_source_ownership!
  end

  def test_current_platforms_have_no_duplicate_destination_ownership
    assert @validator.validate_destination_ownership!
  end

  def test_current_managed_markdown_local_links_are_valid
    assert @validator.validate_markdown_links!
    assert_operator @validator.markdown_link_count, :>, 0
  end

  def test_current_tree_passes_full_check_with_no_false_positive
    outcome = Fable::SkillAuthoringIntegrity.check(repository_root: REPOSITORY_ROOT)
    assert outcome.report.ok?, "unexpected failures: #{outcome.report.failures.join('; ')}"
    refute_nil outcome.sync_result
    assert outcome.sync_result.ok, "sync-platforms.sh --check failed:\n#{outcome.sync_result.stdout}#{outcome.sync_result.stderr}"
  end

  # --- Negative controls: mechanical fixtures, never the real tree ---------

  def test_markdown_link_guard_ignores_external_and_fenced_examples
    with_minimal_fixture do |root, registry|
      write_fixture_file(
        root,
        CANONICAL_SKILL,
        <<~MARKDOWN
          [registered](references/registered.md)
          [http](http://example.test/missing.md)
          [https](https://example.test/missing.md)
          [mail](mailto:someone@example.test)

          ```markdown
          [fenced](references/missing.md)
          ```
        MARKDOWN
      )

      validator = validator_for(registry, repository_root: root)
      assert validator.validate_markdown_links!
      assert_equal 1, validator.markdown_link_count
    end
  end

  def test_negative_control_rejects_broken_markdown_local_link
    with_minimal_fixture do |root, registry|
      write_fixture_file(root, CANONICAL_SKILL, "[missing](references/missing.md)\n")

      error = assert_raises(IntegrityViolation) do
        validator_for(registry, repository_root: root).validate_markdown_links!
      end
      assert_match(/broken Markdown local link/, error.message)
      assert_match(%r{references/missing\.md}, error.message)
    end
  end

  def test_negative_control_rejects_ambiguous_or_second_shared_skill
    ambiguous = deep_copy(@registry)
    ambiguous.fetch('shared')['skill'] = [CANONICAL_SKILL, 'fable-method/shared/alternate/SKILL.md']
    error = assert_raises(IntegrityViolation) { validator_for(ambiguous).validate_canonical_entry! }
    assert_match(/one scalar string value/, error.message)

    with_minimal_fixture do |root, _registry|
      manifest = root.join('fable-method/platforms.yaml')
      FileUtils.mkdir_p(manifest.dirname)
      File.write(
        manifest,
        <<~YAML
          shared:
            skill: #{CANONICAL_SKILL}
            skill: fable-method/shared/alternate/SKILL.md
            references: []
          platforms: []
        YAML
      )
      error = assert_raises(IntegrityViolation) { Validator.load_registry(manifest) }
      assert_match(/duplicate YAML mapping key at platforms\.yaml\.shared\.skill/, error.message)
    end

    with_minimal_fixture do |root, registry|
      write_fixture_file(root, 'fable-method/shared/alternate/SKILL.md')
      error = assert_raises(IntegrityViolation) do
        validator_for(registry, repository_root: root).validate_canonical_entry!
      end
      assert_match(/canonical shared SKILL\.md set mismatch/, error.message)
    end
  end

  def test_negative_control_rejects_duplicate_canonical_reference_entry
    duplicate = deep_copy(@registry)
    duplicate.fetch('shared').fetch('references') << 'fable-method/shared/references/domains/../examples.md'

    error = assert_raises(IntegrityViolation) { validator_for(duplicate).validate_reference_registry! }
    assert_match(/entries must be unique/, error.message)
  end

  def test_negative_control_rejects_absolute_path_as_path_safety_violation
    absolute_path = deep_copy(@registry)
    absolute_path.fetch('shared')['skill'] = '/absolute/path'

    error = assert_raises(IntegrityViolation) do
      validator_for(absolute_path).validate_canonical_entry!
    end
    assert_match(%r{shared\.skill must be repo-relative: /absolute/path}, error.message)
    refute_match(/ownership|canonical shared/, error.message)
  end

  def test_negative_control_rejects_parent_escape_as_path_safety_violation
    parent_escape = deep_copy(@registry)
    parent_escape.fetch('shared').fetch('references')[0] = '../../outside'

    error = assert_raises(IntegrityViolation) do
      validator_for(parent_escape).validate_reference_registry!
    end
    assert_match(
      %r{shared\.references\[0\] escapes the repository: \.\./\.\./outside},
      error.message
    )
    refute_match(/ownership|missing/, error.message)
  end

  def test_negative_control_rejects_missing_declared_reference
    with_minimal_fixture do |root, registry|
      registry.fetch('shared')['references'] = ['fable-method/shared/references/missing.md']
      error = assert_raises(IntegrityViolation) do
        validator_for(registry, repository_root: root).validate_reference_registry!
      end
      assert_match(/registered canonical reference is missing/, error.message)
    end
  end

  def test_negative_control_rejects_unregistered_orphan_reference
    with_minimal_fixture do |root, registry|
      write_fixture_file(root, 'fable-method/shared/references/orphan.md')
      error = assert_raises(IntegrityViolation) do
        validator_for(registry, repository_root: root).validate_no_orphan_references!
      end
      assert_match(/orphans=.*orphan\.md/, error.message)
    end
  end

  def test_negative_control_rejects_incompatible_source_roles
    duplicate_owner = deep_copy(@registry)
    duplicate_owner.fetch('platforms').first['frontmatter_source'] = CANONICAL_SKILL

    error = assert_raises(IntegrityViolation) { validator_for(duplicate_owner).validate_source_ownership! }
    assert_match(/incompatible canonical source ownership/, error.message)
  end

  def test_negative_control_rejects_duplicate_platform_destination
    duplicate_destination = deep_copy(@registry)
    duplicate_destination.fetch('platforms').first['reference_overrides'] = [
      {
        'source' => 'fable-method/shared/platforms/test/references/first.md',
        'destination' => 'references/duplicate.md'
      },
      {
        'source' => 'fable-method/shared/platforms/test/references/second.md',
        'destination' => 'references/nested/../duplicate.md'
      }
    ]

    error = assert_raises(IntegrityViolation) do
      validator_for(duplicate_destination).validate_destination_ownership!
    end
    assert_match(/duplicate platforms\[0\] destination ownership/, error.message)
  end

  # --- Materialization drift: delegated to the existing sync authority -----

  def test_sync_authority_reports_no_drift_on_the_current_tree
    result = Fable::SkillAuthoringIntegrity::SyncPlatformsCheck.run(repository_root: REPOSITORY_ROOT)
    assert result.ok, "expected NO_DRIFT, got:\n#{result.stdout}#{result.stderr}"
    assert_includes result.stdout, 'NO_DRIFT: claude'
  end

  def test_materialization_drift_is_detected_via_the_existing_sync_authority
    target = REPOSITORY_ROOT.join('fable-method/platforms/claude/fable-method/references/reporting.md')
    original = File.binread(target)
    begin
      File.write(target, "#{original}\ncorrupted by test_skill_authoring_integrity\n")

      outcome = Fable::SkillAuthoringIntegrity.check(repository_root: REPOSITORY_ROOT)
      refute outcome.report.ok?
      drift_failure = outcome.report.failures.find { |failure| failure.start_with?('MATERIALIZATION_DRIFT') }
      refute_nil drift_failure, "expected a MATERIALIZATION_DRIFT failure, got: #{outcome.report.failures.join('; ')}"
      assert_match(%r{CHANGED: claude/references/reporting\.md}, drift_failure)
    ensure
      File.binwrite(target, original)
    end
  end

  # --- CLI contract ----------------------------------------------------------

  def test_cli_check_passes_on_the_current_tree
    stdout, stderr, status = run_cli('--check')
    assert status.success?, "stdout=#{stdout}\nstderr=#{stderr}"
    assert_includes stdout, 'SKILL_AUTHORING_INTEGRITY: PASS'
  end

  def test_cli_rejects_unknown_arguments
    _stdout, stderr, status = run_cli('--bogus')
    refute status.success?
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'usage:'
  end

  def test_cli_check_does_not_mutate_the_tree
    before = tree_fingerprint
    run_cli('--check')
    after = tree_fingerprint
    assert_equal before, after
  end

  def test_cli_check_is_deterministic_across_repeated_runs
    first_stdout, _stderr, first_status = run_cli('--check')
    second_stdout, _stderr, second_status = run_cli('--check')
    assert_equal first_status.exitstatus, second_status.exitstatus
    assert_equal first_stdout, second_stdout
  end

  private

  def run_cli(*args)
    Open3.capture3('ruby', SCRIPT, *args, chdir: REPOSITORY_ROOT.to_s)
  end

  def tree_fingerprint
    Dir.glob(REPOSITORY_ROOT.join('fable-method/**/*').to_s, File::FNM_DOTMATCH)
       .select { |path| File.file?(path) && !File.symlink?(path) }
       .sort
       .each_with_object({}) { |path, fingerprint| fingerprint[path] = File.mtime(path).to_f }
  end

  def validator_for(registry, repository_root: REPOSITORY_ROOT)
    Validator.new(repository_root: repository_root, registry: registry)
  end

  def deep_copy(value)
    Marshal.load(Marshal.dump(value))
  end

  def with_minimal_fixture
    Dir.mktmpdir('fable-authoring-integrity-') do |directory|
      root = Pathname.new(directory)
      reference = 'fable-method/shared/references/registered.md'
      write_fixture_file(root, CANONICAL_SKILL)
      write_fixture_file(root, reference)
      registry = {
        'shared' => { 'skill' => CANONICAL_SKILL, 'references' => [reference] },
        'platforms' => []
      }
      yield root, registry
    end
  end

  def write_fixture_file(root, relative_path, contents = "# fixture\n")
    path = root.join(relative_path)
    FileUtils.mkdir_p(path.dirname)
    File.write(path, contents)
  end
end
