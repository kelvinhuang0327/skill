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

  def test_named_read_only_entry_scope_is_materialized_for_all_platforms
    paths = [ROOT.join('fable-method/shared/SKILL.md')] + %w[codex claude gemini antigravity].map do |platform|
      ROOT.join("fable-method/platforms/#{platform}/fable-method/SKILL.md")
    end
    fragments = [
      <<~'TEXT'.chomp,
        For a read-only task with an explicit named-source boundary, scope entry
        checks to the same boundary. Use exact-path Git tracking queries when only a
        named rule file's tracked identity is needed. Do not perform a repository-wide
        status or inventory merely as a generic workspace preflight.
      TEXT
      <<~'TEXT'.chomp
        At task entry, resolve applicable `AGENTS.md` in the Worker's own cwd and
        context within the Packet's allowed scope; actually read each applicable file
        before work.
      TEXT
    ]

    paths.each do |path|
      contents = File.read(path)
      fragments.each { |fragment| assert_includes contents, fragment, path.to_s }
    end
  end

  def test_git_storage_scope_contract_is_materialized_for_all_platforms
    paths = [ROOT.join('fable-method/shared/SKILL.md')] + %w[codex claude gemini antigravity].map do |platform|
      ROOT.join("fable-method/platforms/#{platform}/fable-method/SKILL.md")
    end
    fragments = [
      '`GIT_INDEX_FILE` selects',
      'shares the repository\'s common directory, object database, and ordinary',
      'worktree-specific refs are separate',
      'uses no object alternates',
      'do not require every',
      'task to create an independent repository.'
    ]

    paths.each do |path|
      contents = File.read(path)
      fragments.each { |fragment| assert_includes contents, fragment, path.to_s }
    end
  end

  def test_planner_task_local_evidence_identity_check_uses_local_helper_mode
    path = ROOT.join('planner-handoff/references/planner-handoff-v5.5.1.md')
    paragraph = File.read(path).lines.find { |line| line.start_with?('  - **條件式身份核對：**') }
    refute_nil paragraph
    task_local_command = paragraph.match(/`python3 -B <existing-helper-absolute-path> evidence-summary --task-id [^`]+`/)
    refute_nil task_local_command
    refute_includes task_local_command[0], '--repo'
    assert_includes paragraph, '具名 task-local 身份核對'
    assert_includes paragraph, '不帶 `--repo`'
    assert_includes paragraph, '只有 Packet 明確授權 repository-wide checkpoint/history inventory'
    assert_includes paragraph, '單加 `--task-id` 不會把它變成 task-local 查詢'
    assert_includes paragraph, '普通 Task 不執行此查詢。'
  end

  def test_alternate_index_still_writes_to_the_repository_object_database
    git_fixture do |root|
      repository = initialize_git_repo(root.join('alternate-index-repo'))
      alternate_index = root.join('alternate.index')
      repository.join('alternate.txt').write('blob staged through alternate index')

      git!(repository, 'add', '--', 'alternate.txt', env: { 'GIT_INDEX_FILE' => alternate_index.to_s })

      default_index = git!(repository, 'rev-parse', '--path-format=absolute', '--git-path', 'index')
      assert_equal '', git!(repository, 'ls-files')
      assert_equal 'alternate.txt', git!(repository, 'ls-files', env: { 'GIT_INDEX_FILE' => alternate_index.to_s })
      refute_equal default_index, alternate_index.to_s
      assert File.file?(alternate_index)

      blob = git!(repository, 'rev-parse', ':alternate.txt', env: { 'GIT_INDEX_FILE' => alternate_index.to_s })
      object_directory = git!(repository, 'rev-parse', '--path-format=absolute', '--git-path', 'objects')
      object_path = File.join(object_directory, blob[0, 2], blob[2..])
      assert File.file?(object_path), "expected blob in shared object database: #{object_path}"
      git!(repository, 'cat-file', '-e', blob)
    end
  end

  def test_linked_worktree_shares_common_directory_objects_and_ordinary_branch_refs
    git_fixture do |root|
      repository = initialize_git_repo(root.join('linked-main'))
      commit_file(repository, 'base.txt', 'base')
      linked = root.join('linked-feature')
      git!(repository, 'worktree', 'add', '-b', 'feature', linked.to_s, 'HEAD')

      common = git!(repository, 'rev-parse', '--path-format=absolute', '--git-common-dir')
      linked_common = git!(linked, 'rev-parse', '--path-format=absolute', '--git-common-dir')
      main_git_dir = git!(repository, 'rev-parse', '--path-format=absolute', '--git-dir')
      linked_git_dir = git!(linked, 'rev-parse', '--path-format=absolute', '--git-dir')
      assert_equal common, linked_common
      refute_equal main_git_dir, linked_git_dir
      assert_equal git!(repository, 'rev-parse', '--path-format=absolute', '--git-path', 'objects'),
        git!(linked, 'rev-parse', '--path-format=absolute', '--git-path', 'objects')

      feature_ref = git!(linked, 'rev-parse', '--path-format=absolute', '--git-path', 'refs/heads/feature')
      assert feature_ref.start_with?(common + File::SEPARATOR)
      assert File.file?(feature_ref)
      commit_file(linked, 'feature.txt', 'feature commit')
      feature_head = git!(linked, 'rev-parse', 'HEAD')
      assert_equal feature_head, git!(repository, 'rev-parse', 'feature')

      git!(repository, 'worktree', 'remove', linked.to_s)
      refute File.exist?(linked)
      assert_equal feature_head, git!(repository, 'rev-parse', 'feature')
      git!(repository, 'cat-file', '-e', "#{feature_head}^{commit}")
    end
  end

  def test_independent_repositories_without_alternates_have_independent_git_storage
    git_fixture do |root|
      first = initialize_git_repo(root.join('independent-first'))
      second = initialize_git_repo(root.join('independent-second'))
      first_head = commit_file(first, 'first.txt', 'first independent object set')
      commit_file(second, 'second.txt', 'second independent object set')

      first_common = git!(first, 'rev-parse', '--path-format=absolute', '--git-common-dir')
      second_common = git!(second, 'rev-parse', '--path-format=absolute', '--git-common-dir')
      first_objects = git!(first, 'rev-parse', '--path-format=absolute', '--git-path', 'objects')
      second_objects = git!(second, 'rev-parse', '--path-format=absolute', '--git-path', 'objects')
      first_index = git!(first, 'rev-parse', '--path-format=absolute', '--git-path', 'index')
      second_index = git!(second, 'rev-parse', '--path-format=absolute', '--git-path', 'index')
      first_main_ref = git!(first, 'rev-parse', '--path-format=absolute', '--git-path', 'refs/heads/main')
      second_main_ref = git!(second, 'rev-parse', '--path-format=absolute', '--git-path', 'refs/heads/main')

      refute_equal first_common, second_common
      refute_equal first_objects, second_objects
      refute_equal first_index, second_index
      refute_equal first_main_ref, second_main_ref
      [first, second].each do |repository|
        refute File.exist?(repository.join('.git/objects/info/alternates'))
        assert File.file?(git!(repository, 'rev-parse', '--path-format=absolute', '--git-path', 'index'))
        assert File.file?(git!(repository, 'rev-parse', '--path-format=absolute', '--git-path', 'refs/heads/main'))
      end
      _, _, status = git_capture(second, 'cat-file', '-e', "#{first_head}^{commit}")
      refute status.success?, 'an independent repository without alternates must not resolve the other repository commit'
    end
  end

  def test_cumulative_reports_reconcile_task_step_evidence
    shared = ROOT.join('fable-method/shared/references/reporting.md')
    materials = [shared] + %w[claude gemini antigravity codex].map do |platform|
      ROOT.join("fable-method/platforms/#{platform}/fable-method/references/reporting.md")
    end

    materials.each do |path|
      contents = File.read(path)
      assert_includes contents, 'When task-local step outputs exist, derive cumulative action and no-action'
      assert_includes contents, 'alone cannot establish whether an action occurred.'
      assert_includes contents, '`REUSED_EXACT_TREE_EVIDENCE`'
    end
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

  def git_fixture
    base = ENV.fetch('TMPDIR', Dir.tmpdir)
    FileUtils.mkdir_p(base)
    Dir.mktmpdir('fable-git-scope-', base) { |dir| yield Pathname.new(dir) }
  end

  def initialize_git_repo(path)
    FileUtils.mkdir_p(path.dirname)
    stdout, stderr, status = Open3.capture3(
      { 'GIT_CONFIG_NOSYSTEM' => '1', 'GIT_CONFIG_GLOBAL' => '/dev/null' },
      'git', 'init', '-q', '--initial-branch=main', path.to_s,
      chdir: path.dirname.to_s
    )
    assert status.success?, "git init failed: #{stderr}#{stdout}"
    path
  end

  def git_capture(repository, *args, env: {})
    Open3.capture3(
      { 'GIT_CONFIG_NOSYSTEM' => '1', 'GIT_CONFIG_GLOBAL' => '/dev/null' }.merge(env),
      'git', *args, chdir: repository.to_s
    )
  end

  def git!(repository, *args, env: {})
    stdout, stderr, status = git_capture(repository, *args, env: env)
    assert status.success?, "git #{args.join(' ')} failed: #{stderr}#{stdout}"
    stdout.strip
  end

  def commit_file(repository, filename, content)
    repository.join(filename).write(content)
    git!(repository, 'add', '--', filename)
    git!(repository, '-c', 'user.name=Fable Test', '-c', 'user.email=fable@example.invalid',
      'commit', '-m', "fixture: #{filename}")
    git!(repository, 'rev-parse', 'HEAD')
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
