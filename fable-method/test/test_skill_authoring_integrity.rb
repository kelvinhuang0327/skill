# frozen_string_literal: true
require 'fileutils'
require 'minitest/autorun'
require 'open3'
require 'pathname'
require 'tmpdir'
require_relative '../scripts/skill_authoring_integrity'

class SkillAuthoringIntegrityTest < Minitest::Test
  Integrity = Fable::SkillAuthoringIntegrity
  Validator = Integrity::Validator
  Error = Integrity::IntegrityViolation
  ROOT = Pathname.new(File.expand_path('../..', __dir__))
  SCRIPT = ROOT.join('fable-method/scripts/skill_authoring_integrity.rb').to_s

  def setup
    @registry = Validator.load_registry(ROOT.join('fable-method/platforms.yaml'))
  end

  Integrity::STRUCTURAL_CHECKS.each do |name, method|
    define_method("test_current_both_skills_#{name.downcase}") do
      %w[fable-method fable-judge].each do |skill|
        assert Validator.new(repository_root: ROOT, registry: @registry, skill: skill).public_send(method)
      end
    end
  end

  def test_current_full_check_and_cli
    outcome = Integrity.check(repository_root: ROOT)
    assert outcome.report.ok?, outcome.report.failures.join("\n")
    assert outcome.sync_result.ok
    stdout, stderr, status = Open3.capture3('ruby', SCRIPT, '--check')
    assert status.success?, stderr + stdout
    assert_includes stdout, 'SKILL_AUTHORING_INTEGRITY: PASS'
    assert_includes stdout, 'skill=fable-judge'
  end

  def test_cli_unknown_argument
    _, stderr, status = Open3.capture3('ruby', SCRIPT, '--bogus')
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'usage:'
  end

  def test_read_only_and_deterministic
    paths = Dir.glob(ROOT.join('{fable-method,fable-judge}/**/*').to_s).select { |p| File.file?(p) }
    before = paths.to_h { |p| [p, [File.binread(p), File.mtime(p)]] }
    first = Open3.capture3('ruby', SCRIPT, '--check')
    second = Open3.capture3('ruby', SCRIPT, '--check')
    assert first[2].success?
    assert_equal first[0..1], second[0..1]
    assert_equal before, paths.to_h { |p| [p, [File.binread(p), File.mtime(p)]] }
  end

  def fixture
    Dir.mktmpdir('fable-authoring-integrity-') do |dir|
      dir = File.realpath(dir)
      %w[fable-method fable-judge].each { |skill| FileUtils.cp_r(ROOT.join(skill), dir) }
      yield Pathname.new(dir)
    end
  end

  def validator(root, skill = 'fable-method')
    Validator.new(repository_root: root, registry: @registry, skill: skill)
  end

  def test_missing_or_second_canonical_entry
    fixture do |root|
      %w[fable-method fable-judge].each do |skill|
        canonical = root.join("#{skill}/shared/SKILL.md")
        original = canonical.binread
        canonical.delete
        assert_raises(Error) { validator(root, skill).validate_canonical_entry! }
        canonical.binwrite(original)
        second = root.join("#{skill}/shared/alternate/SKILL.md")
        FileUtils.mkdir_p(second.dirname)
        second.write('# second')
        assert_raises(Error) { validator(root, skill).validate_canonical_entry! }
      end
    end
  end

  def test_missing_reference_or_orphan
    fixture do |root|
      ref = root.join(@registry['skills']['fable-method']['shared']['references'].first)
      ref.delete
      assert_raises(Error) { validator(root).validate_reference_registry! }
      %w[fable-method fable-judge].each do |skill|
        orphan = root.join("#{skill}/shared/references/orphan.md")
        FileUtils.mkdir_p(orphan.dirname)
        orphan.write('# orphan')
        assert_raises(Error) { validator(root, skill).validate_no_orphan_references! }
      end
    end
  end

  def test_central_model_rejects_ambiguous_duplicate_unsafe_and_collision_records
    alterations = [
      ->(d) { d['skills']['fable-method']['shared']['skill'] = [] },
      ->(d) { d['skills']['fable-method']['shared']['skill'] = '/absolute' },
      ->(d) { d['skills']['fable-method']['shared']['references'] << '../../outside' },
      ->(d) { refs = d['skills']['fable-method']['shared']['references']; refs << refs.first },
      ->(d) { d['skills']['fable-method']['platforms'].first['frontmatter_source'] = 'fable-method/shared/SKILL.md' },
      ->(d) { d['skills']['fable-method']['platforms'].first['materialized_destination'] = 'fable-method/platforms/claude/fable-method' }
    ]
    alterations.each do |alter|
      data = Marshal.load(Marshal.dump(@registry))
      alter.call(data)
      assert_raises(Error) { Validator.new(repository_root: ROOT, registry: data) }
    end
  end

  def test_duplicate_yaml_key_is_rejected_via_shared_parser
    fixture do |root|
      path = root.join('fable-method/platforms.yaml')
      path.write("schema_version: 2\nschema_version: 2\n")
      error = assert_raises(Error) { Validator.load_registry(path) }
      assert_match(/duplicate YAML mapping key/, error.message)
    end
  end

  def test_method_external_links_and_fenced_examples_remain_valid
    fixture do |root|
      canonical = root.join('fable-method/shared/SKILL.md')
      canonical.open('a') { |f| f.write("\n[external](https://example.test/missing)\n[mail](mailto:test@example.test)\n```\n[example](missing.md)\n```\n") }
      assert validator(root).validate_markdown_links!
    end
  end

  def test_broken_inline_and_reference_links_and_path_escape_fail
    ['[bad](references/missing.md)', "[bad]: references/missing.md\n[use][bad]", '[bad](../../outside.md)'].each do |link|
      fixture do |root|
        root.join('fable-method/shared/SKILL.md').open('a') { |f| f.write("\n#{link}\n") }
        assert_raises(Error) { validator(root).validate_markdown_links! }
      end
    end
  end

  def test_managed_source_and_link_symlinks_fail_before_read
    fixture do |root|
      ref = root.join('fable-method/shared/references/reporting.md')
      ref.delete
      File.symlink(ROOT.join('fable-method/shared/references/reporting.md'), ref)
      assert_raises(Error) { validator(root).validate_source_ownership! }
      assert_raises(Error) { validator(root).validate_markdown_links! }
    end
  end

  def test_judge_virtual_projection_links_and_broken_fragments
    fixture do |root|
      assert validator(root, 'fable-judge').validate_markdown_links!
      source = root.join('fable-judge/shared/SKILL.md')
      source.open('a') { |f| f.write("\n[bad](references/judge-depth-contract.md#missing)\n") }
      assert_raises(Error) { validator(root, 'fable-judge').validate_markdown_links! }
    end
  end

  def test_missing_projection_source_and_invalid_frontmatter
    fixture do |root|
      path = root.join('fable-method/shared/references/judge-handoff.md')
      path.delete
      assert_raises(Error) { validator(root, 'fable-judge').validate_source_ownership! }
    end
    fixture do |root|
      root.join('fable-judge/shared/platforms/codex/frontmatter.md').write("---\nname: wrong\ndescription: example\n---\n")
      assert_raises(Error) { validator(root, 'fable-judge').validate_markdown_links! }
    end
  end

  def test_actual_sync_authority_detects_added_missing_modified_files_in_scratch
    %w[added missing modified].each do |kind|
      fixture do |root|
        dest = root.join('fable-method/platforms/claude/fable-method')
        case kind
        when 'added' then dest.join('extra.md').write('extra')
        when 'missing' then dest.join('references/reporting.md').delete
        when 'modified' then dest.join('references/reporting.md').write('corrupted')
        end
        stdout, _, status = Open3.capture3('ruby', ROOT.join('fable-method/scripts/platform_manifest.rb').to_s,
          '--sync', ROOT.join('fable-method/platforms.yaml').to_s, 'fable-method', root.to_s, '--check')
        refute status.success?
        assert_includes stdout, 'DRIFT: claude'
      end
    end
  end
end
