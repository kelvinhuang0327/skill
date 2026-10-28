# frozen_string_literal: true
require 'minitest/autorun'
require 'tmpdir'
require 'fileutils'
require 'stringio'
require_relative '../scripts/platform_manifest'

class PlatformManifestTest < Minitest::Test
  Model = Fable::PlatformManifest
  ROOT = File.expand_path('../..', __dir__)
  MANIFEST = File.join(ROOT, 'fable-method/platforms.yaml')

  def setup
    @model = Model.load(MANIFEST)
    @data = Marshal.load(Marshal.dump(@model.data))
  end

  def method_shared
    @data['skills']['fable-method']['shared']
  end

  def judge_shared
    @data['skills']['fable-judge']['shared']
  end

  def judge_platform
    @data['skills']['fable-judge']['platforms'].first
  end

  def method_codex_platform
    @data['skills']['fable-method']['platforms'].first
  end

  def rejects
    yield
    assert_raises(Model::Error) { Model.new(@data) }
  end

  def test_v3_has_eight_pairs_and_codex_only_runtime_assets
    assert_equal 3, @data['schema_version']
    assert_equal 4, @model.platforms('fable-method').length
    assert_equal 4, @model.platforms('fable-judge').length
    assert_equal '/Users/kelvin/.gemini/skills/fable-judge', @model.pair('fable-judge', 'gemini')['live_installation_path']
    assert_equal '/Users/kelvin/.gemini/config/skills/fable-judge', @model.pair('fable-judge', 'antigravity')['live_installation_path']
    assert_equal %w[task_checkpoint paused_run_continuation artifact_controls], @model.pair('fable-method', 'codex').fetch('runtime_assets').map { |asset| asset['id'] }
    @model.platforms('fable-method').reject { |platform| platform['name'] == 'codex' }.each do |platform|
      refute platform.key?('runtime_assets')
    end
    assert_raises(Model::Error) { @model.pair('fable-judge', 'unknown') }
    assert_raises(Model::Error) { @model.skill('all') }
  end

  [1, 2, 4, '3', nil].each do |version|
    define_method("test_rejects_schema_#{version.inspect}") { rejects { @data['schema_version'] = version } }
  end

  def test_codex_runtime_assets_and_materialized_bytes
    launcher, companion, artifact_controls = @model.pair('fable-method', 'codex').fetch('runtime_assets')
    assert_equal '0d537d193adcd9ec188575a7666cafcbf63d1237', launcher['source_commit']
    assert_equal '8683aa28bfaada3f4b196036ab9a5b4fa39b841a', launcher['git_blob']
    assert_equal 'c5bbcbbee70e575c91ef84d63cd5db3d698a0840d9da92c44de07c49b5b7f9e9', launcher['sha256']
    assert_equal 'e6f8581cdabe21f3683b8a5d8effb6855d98a041', companion['source_commit']
    assert_equal '91592132733be6b697bdbef667b97d9065c829c1', companion['git_blob']
    assert_equal 'f84247658a111c433252d0dbe6336fd12ac22bbd7ce9aaae8425a7154fa1b1a0', companion['sha256']
    assert_equal 'baf902cac6252fb35f62eabc3d80863eb3b51742bca53d4f75a2cad12bbf9d49', artifact_controls['sha256']

    rendered = @model.render(ROOT, 'fable-method', 'codex')
    launcher_bytes = File.binread(File.join(ROOT, launcher['source']))
    companion_bytes = File.binread(File.join(ROOT, companion['source']))
    artifact_controls_bytes = File.binread(File.join(ROOT, artifact_controls['source']))
    assert_equal launcher_bytes, rendered.fetch(launcher['destination'])
    assert_equal companion_bytes, rendered.fetch(companion['destination'])
    assert_equal artifact_controls_bytes, rendered.fetch(artifact_controls['destination'])
    assert_equal 0o755, File.stat(File.join(ROOT, launcher['source'])).mode & 0o777
    assert_equal 0o644, File.stat(File.join(ROOT, companion['source'])).mode & 0o777
    assert_equal 0o644, File.stat(File.join(ROOT, artifact_controls['source'])).mode & 0o777
    assert_match(/elsif mode == :continue_paused_run\s+require_relative ['"]paused_run_continuation['"]/, launcher_bytes)
    assert_includes rendered.fetch('SKILL.md'), "Git blob `#{launcher['git_blob']}`"
    assert_includes rendered.fetch('SKILL.md'), 'loaded only for `--continue-paused-run`'
    assert_includes rendered.fetch('SKILL.md'), '**Installed Runtime:**'
    assert_includes rendered.fetch('SKILL.md'), '**Live Activation:** NOT RUN'
    assert_includes rendered.fetch('SKILL.md'), 'A documentation-only bundle is not Runtime Distribution Ready'
    assert_includes rendered.fetch('SKILL.md'), "SHA-256 `#{artifact_controls['sha256']}`"
    assert_includes rendered.fetch('SKILL.md'), 'Artifact budgets and worktree lifecycle'

    claude = @model.render(ROOT, 'fable-method', 'claude')
    refute claude.key?('scripts/task_checkpoint.rb')
    refute claude.key?('scripts/fable_artifact_controls.py')
    refute_includes claude.fetch('SKILL.md'), 'Artifact budgets and worktree lifecycle'
    refute_includes claude.fetch('SKILL.md'), 'Codex Runtime Materialization'
  end

  def test_codex_runtime_asset_contract_is_closed
    rejects { method_codex_platform['runtime_assets'].pop }
    rejects { method_codex_platform['runtime_assets'].first['git_blob'] = 'bad' }
    rejects { method_codex_platform['runtime_assets'].last['destination'] = '../outside.py' }
    rejects { method_codex_platform['runtime_assets'].first['mode'] = '100644' }
  end

  def test_duplicate_yaml_keys_at_any_level_and_multiple_documents_fail
    text = File.read(MANIFEST)
    assert_raises(Model::Error) { Model.yaml(text.sub('schema_version: 3', "schema_version: 3\nschema_version: 3")) }
    assert_raises(Model::Error) { Model.yaml("skills:\n  fable-judge: {}\n  fable-judge: {}\n") }
    assert_raises(Model::Error) { Model.yaml("---\na: 1\n---\na: 2\n") }
  end

  def test_unknown_skill
    rejects { @data['skills']['other'] = @data['skills']['fable-judge'] }
  end

  def test_duplicate_logical_platform
    rejects { @data['skills']['fable-judge']['platforms'] << judge_platform.dup }
  end

  def test_unknown_platform
    rejects { judge_platform['name'] = 'other' }
  end

  def test_unsupported_pair
    rejects { judge_platform['name'] = 'unsupported' }
  end

  def test_missing_field
    rejects { judge_platform.delete('adapter_sources') }
  end

  def test_unknown_fields_are_closed_at_each_level
    [@data, @data['skills']['fable-judge'], judge_shared, judge_platform, judge_shared['projections'].first,
     method_codex_platform, method_codex_platform['runtime_assets'].first].each do |record|
      record['unknown'] = true
      assert_raises(Model::Error) { Model.new(@data) }
      record.delete('unknown')
    end
  end

  def test_non_string_mapping_keys_fail_with_model_error
    [@data, judge_platform].each do |record|
      [7, true, nil].each do |key|
        record[key] = 'bad'
        assert_raises(Model::Error) { Model.new(@data) }
        record.delete(key)
      end
    end
  end

  def test_cross_skill_reference_validation_is_order_independent
    @data['skills'] = @data['skills'].to_a.reverse.to_h
    @data['skills']['fable-method']['shared'] = 'bad'
    assert_raises(Model::Error) { Model.new(@data) }
  end

  def test_incorrect_collection_and_scalar_types
    rejects { judge_platform['adapter_sources'] = {} }
  end

  def test_unsafe_relative_paths
    ['../outside', '/absolute', 'x//y', 'x/./y', 'x/../y', "x\ny", 'x\\y'].each do |path|
      judge_platform['frontmatter_source'] = path
      assert_raises(Model::Error, path) { Model.new(@data) }
    end
  end

  def test_live_path_must_be_exact_absolute_pair_metadata
    rejects { judge_platform['live_installation_path'] = 'relative' }
  end

  def test_materialized_destination_collision
    rejects { judge_platform['materialized_destination'] = @model.pair('fable-method', 'codex')['materialized_destination'] }
  end

  def test_overlapping_destination
    rejects { judge_platform['materialized_destination'] += '/nested' }
  end

  def test_reference_source_ownership_and_collision
    rejects { judge_shared['references'] << Model::DEPTH_SOURCE }
  end

  def test_duplicate_reference
    rejects { method_shared['references'] << method_shared['references'].first }
  end

  def test_projection_reference_must_be_declared_by_method
    rejects { method_shared['references'].delete(Model::DEPTH_SOURCE) }
  end

  def test_projection_destination_collision
    rejects do
      judge_platform['reference_overrides'] << {
        'source' => 'fable-judge/shared/platforms/codex/reference.md',
        'destination' => Model::DEPTH_DESTINATION
      }
    end
  end

  def test_duplicate_and_unsupported_projection_selectors
    selectors = judge_shared['projections'].first['sections']
    rejects { selectors << selectors.first }
    selectors.pop
    rejects { selectors[0] = 'Other' }
  end

  def projection_source
    "# Title\n\n## Unselected\nunrelated\n\n## Depth and evidence reuse\nfirst\n### Nested\nchild\n```md\n## Remediation limit\n```\n~~~\n## Depth reconciliation\n~~~\n\n## Depth reconciliation\nsecond\n\n## Remediation limit\nlast\n\n## Tail\ntail\n"
  end

  def project(content = projection_source, selectors = Model::DEPTH_SECTIONS)
    Model.project(content, selectors)
  end

  def test_projection_is_byte_faithful_ordered_and_ignores_fenced_headings
    source = projection_source
    assert_equal source[source.index('## Depth and evidence reuse')...source.index('## Tail')], project
    assert_includes project, "### Nested\nchild\n"
    assert_equal project, project(source.sub('unrelated', 'changed').sub('tail\n', 'other\n'))
  end

  def test_missing_duplicate_ordering_and_unsupported_heading_fail
    assert_raises(Model::Error) { project(projection_source.sub('## Depth and evidence reuse', '## Missing')) }
    assert_raises(Model::Error) { project(projection_source + "\n## Remediation limit\nagain\n") }
    reordered = "## Depth reconciliation\nsecond\n## Depth and evidence reuse\nfirst\n## Remediation limit\nlast\n"
    error = assert_raises(Model::Error) { project(reordered) }
    assert_match(/projection source ordering conflict/, error.message)
    error = assert_raises(Model::Error) { project(projection_source.sub("## Depth reconciliation\nsecond", "# Another parent\n## Depth reconciliation\nsecond")) }
    assert_match(/heading structure/, error.message)
    assert_raises(Model::Error) { project(projection_source, Model::DEPTH_SECTIONS.reverse) }
    assert_raises(Model::Error) { project(projection_source, ['Other']) }
  end

  def test_projection_links_and_fragments_cannot_escape_bundle
    files = {'SKILL.md' => '[depth](references/depth.md#heading)', 'references/depth.md' => "## Heading\n[fake](https://example.test)\n"}
    Model.validate_links!(files)
    ['missing.md', '../escape.md', '/absolute', 'references/depth.md#missing'].each do |target|
      files['SKILL.md'] = "[bad](#{target})"
      assert_raises(Model::Error, target) { Model.validate_links!(files) }
    end
    files['SKILL.md'] = "[bad]: references/depth.md#missing\n[use][bad]"
    assert_raises(Model::Error) { Model.validate_links!(files) }
  end

  def test_actual_projection_only_imports_approved_sections
    text = File.binread(File.join(ROOT, Model::DEPTH_SOURCE))
    rendered = @model.render(ROOT, 'fable-judge', 'codex').fetch(Model::DEPTH_DESTINATION)
    assert_equal Model.project(text, Model::DEPTH_SECTIONS), rendered
    assert_includes rendered, '### Verification temp isolation'
    refute_includes rendered, '## Handoff payload'
  end

  def test_source_and_destination_type_gates_precede_content_access
    Dir.mktmpdir('manifest-types-') do |tmp|
      tmp = File.realpath(tmp)
      File.write(File.join(tmp, 'plain'), 'sentinel')
      File.symlink(File.join(tmp, 'plain'), File.join(tmp, 'link'))
      assert_raises(Model::Error) { Model.safe_file!(File.join(tmp, 'link')) }
      assert_raises(Model::Error) { Model.safe_file!(tmp) }
      assert_raises(Model::Error) { Model.inventory(tmp) }
      File.unlink(File.join(tmp, 'link'))
      File.chmod(0, File.join(tmp, 'plain'))
      assert_raises(Model::Error) { Model.safe_file!(File.join(tmp, 'plain')) }
      File.chmod(0o600, File.join(tmp, 'plain'))
    end
  end
end
