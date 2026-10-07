require 'minitest/autorun'
require_relative '../scripts/platform_manifest'

class TaskCheckpointReferenceSelfContainmentTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  FABLE_ROOT = File.join(ROOT, 'fable-method')
  PLATFORMS = %w[claude codex gemini antigravity].freeze
  REFERENCES = %w[operational-gates.md task-checkpoint.md judge-handoff.md].freeze
  HELPER_RELATIVE_PATH = 'scripts/task_checkpoint.rb'

  def platform_root(platform)
    File.join(FABLE_ROOT, 'platforms', platform, 'fable-method')
  end

  def test_installed_references_are_self_contained_without_the_optional_helper
    built_references = PLATFORMS.flat_map do |platform|
      Dir.glob(File.join(platform_root(platform), '**', '*.md')).map do |path|
        [path, File.read(path, encoding: 'UTF-8')]
      end
    end
    helper_references = built_references.select do |_path, content|
      content.include?('fable-method/scripts/task_checkpoint.rb')
    end

    refute_empty helper_references
    helper_references.each do |path, content|
      assert_contract(/optional/i, content, "#{path} must mark the helper optional")
      assert_contract(/absence (?:is )?never (?:a )?blocker|absence alone never blocks/i,
                      content, "#{path} must say helper absence does not block")
      assert_contract(/fallback/i, content, "#{path} must contain a fallback")
    end

    writer_references = built_references.select do |_path, content|
      content.include?('TaskCheckpoint.scope_qualified_active_writer?')
    end
    refute_empty writer_references
    writer_references.each do |path, content|
      assert_contract(/snapshots?\s+are\s+authoritative/i, content, "#{path} must make snapshots authoritative")
      assert_contract(/Any difference is active mutation/i, content, "#{path} must treat snapshot drift as mutation")
      assert_contract(/process name alone is insufficient/i, content, "#{path} must require observed path overlap")
      assert_contract(/unresolved[^.]{0,80}fail[- ]closed/i, content,
                      "#{path} must fail closed on unresolved ownership")
      assert_contract(/exact designated worktree/i, content, "#{path} must retain worktree authority")
    end
  end

  def test_platform_materializations_match_the_canonical_build
    manifest = Fable::PlatformManifest.load(File.join(FABLE_ROOT, 'platforms.yaml'))
    assert manifest.sync(ROOT, 'fable-method', write: false)

    %w[claude codex].each do |platform|
      REFERENCES.each do |reference|
        source = File.join(FABLE_ROOT, 'shared', 'references', reference)
        built = File.join(platform_root(platform), 'references', reference)
        assert_equal File.binread(source), File.binread(built), "#{platform}/#{reference} drifted"
      end
    end
  end

  def test_no_duplicate_helper_implementation_and_exact_source_bytes_if_shipped
    helper_source = File.binread(File.join(FABLE_ROOT, HELPER_RELATIVE_PATH))
    assert_equal 1, helper_source.scan(/^class TaskCheckpoint(?:\s|$)/).length

    built_helpers = PLATFORMS.map do |platform|
      File.join(platform_root(platform), HELPER_RELATIVE_PATH)
    end.select { |path| File.file?(path) }
    built_helpers.each do |path|
      assert_equal helper_source, File.binread(path), "#{path} must reuse canonical helper bytes"
    end

    built_ruby = PLATFORMS.flat_map do |platform|
      Dir.glob(File.join(platform_root(platform), '**', '*.rb'))
    end
    built_ruby.each do |path|
      next unless File.read(path, encoding: 'UTF-8').match?(/^class TaskCheckpoint(?:\s|$)/)

      assert_equal helper_source, File.binread(path), "#{path} duplicates the canonical implementation"
    end
  end

  private

  def assert_contract(pattern, content, message)
    assert pattern.match?(content), message
  end
end
