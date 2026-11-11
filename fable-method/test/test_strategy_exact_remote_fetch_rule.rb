# frozen_string_literal: true

require 'minitest/autorun'
require 'open3'
require 'tmpdir'

class StrategyExactRemoteFetchRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  RULE = <<~TEXT.chomp
    - When an exact source pin is absent locally, probe a remote only if the
      Packet explicitly allows remote source lookup and pins one exact commit or
      blob. Fetch only that object into an isolated disposable bare probe (for
      Git, `git fetch --no-tags --no-write-fetch-head <allowed-remote>
      <exact-object>`); do not fetch tags, branches, all refs, or history, and do
      not change the destination project's refs. Record exact fetch result
      separately from local presence; keep credential-bearing URLs and command
      output out of evidence. If that exact fetch is unavailable, leave identity
      unresolved and source semantics `UNKNOWN`; never use a nearby ref or proxy
      evidence.
  TEXT

  def test_rule_is_exactly_synchronized_across_shared_and_platform_skills
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      execution = skill.index("## Execution\n")
      verification = skill.index("## Verification\n")

      refute_nil execution, "#{relative_path} is missing ## Execution"
      refute_nil verification, "#{relative_path} is missing ## Verification"
      assert_operator execution, :<, verification, relative_path
      section = skill[execution...verification]
      assert_equal 1, section.scan(Regexp.new(Regexp.escape(RULE))).length,
                   "#{relative_path} must contain the exact remote-fetch rule once"
    end
  end

  def test_exact_probe_distinguishes_local_fetchable_unavailable_and_denied
    fixture_root_path = nil
    probe_paths = []
    fetch_calls = []

    Dir.mktmpdir('fable-exact-remote-source-') do |fixture_root|
      fixture_root_path = fixture_root
      temp_root = File.realpath(Dir.tmpdir)
      assert File.realpath(fixture_root).start_with?("#{temp_root}/"),
             'Git fixtures must live in system temporary storage'

      source_work = create_repository(fixture_root, 'source', 'source-v1')
      exact_commit = git_output('-C', source_work, 'rev-parse', 'HEAD')
      git_expect('-C', source_work, 'tag', 'fixture-release')
      remote = File.join(fixture_root, 'source.git')
      git_expect('clone', '--quiet', '--bare', source_work, remote)

      local_project = File.join(fixture_root, 'project-local.git')
      git_expect('clone', '--quiet', '--bare', remote, local_project)
      seed_project = create_repository(fixture_root, 'project-seed', 'project-sentinel')
      fetchable_project = File.join(fixture_root, 'project-fetchable.git')
      unavailable_project = File.join(fixture_root, 'project-unavailable.git')
      git_expect('clone', '--quiet', '--bare', seed_project, fetchable_project)
      git_expect('clone', '--quiet', '--bare', seed_project, unavailable_project)
      unavailable_work = create_repository(fixture_root, 'unavailable-source', 'unpublished-object')
      unavailable_commit = git_output('-C', unavailable_work, 'rev-parse', 'HEAD')

      local = probe_without_project_ref_changes(
        local_project, remote, exact_commit, true, fetch_calls, probe_paths
      )
      denied = probe_without_project_ref_changes(
        fetchable_project, remote, exact_commit, false, fetch_calls, probe_paths
      )
      fetchable = probe_without_project_ref_changes(
        fetchable_project, remote, exact_commit, true, fetch_calls, probe_paths
      )
      unavailable = probe_without_project_ref_changes(
        unavailable_project, remote, unavailable_commit, true, fetch_calls, probe_paths
      )

      assert_equal true, local[:local_presence]
      assert_equal 'NOT_ATTEMPTED', local[:exact_fetch_result]
      assert_equal 'RESOLVED_LOCAL_OBJECT', local[:identity]
      assert_equal 'PINNED_SOURCE_AVAILABLE', local[:source_semantics]

      assert_equal false, denied[:local_presence]
      assert_equal 'NOT_AUTHORIZED', denied[:exact_fetch_result]
      assert_equal 'UNRESOLVED', denied[:identity]
      assert_equal 'UNKNOWN', denied[:source_semantics]

      assert_equal false, fetchable[:local_presence]
      assert_equal 'FETCHED_EXACT_OBJECT', fetchable[:exact_fetch_result]
      assert_equal 'RESOLVED_REMOTE_OBJECT', fetchable[:identity]
      assert_equal 'PINNED_SOURCE_AVAILABLE', fetchable[:source_semantics]
      assert_equal true, fetchable[:probe_refs_empty]

      assert_equal false, unavailable[:local_presence]
      assert_equal 'UNAVAILABLE', unavailable[:exact_fetch_result]
      assert_equal 'UNRESOLVED', unavailable[:identity]
      assert_equal 'UNKNOWN', unavailable[:source_semantics]
      assert_equal true, unavailable[:probe_refs_empty]

      assert_equal 2, fetch_calls.length, 'only authorized absent-object cases may fetch'
      assert_equal 2, probe_paths.length
      assert_equal [exact_commit, unavailable_commit], fetch_calls.map(&:last)
      fetch_calls.each do |arguments|
        assert_equal ['--git-dir', arguments[1], 'fetch', '--no-tags', '--no-write-fetch-head', remote,
                      arguments.last], arguments
        assert_equal 'fetch', arguments[2]
        assert_includes arguments, '--no-tags'
        assert_includes arguments, '--no-write-fetch-head'
        refute_includes arguments, '--all'
        refute_includes arguments, '--tags'
        refute_includes arguments, '--branches'
        refute_includes arguments, '--mirror'
      end

      [local, denied, fetchable, unavailable].each do |result|
        refute result.key?(:remote_url)
        refute result.key?(:fetch_output)
        refute_match(%r{https?://|password|token|stdout|stderr}i, result.inspect)
      end
    end

    refute File.exist?(fixture_root_path), 'remove only the newly created Git fixture tree'
    probe_paths.each do |probe_path|
      refute File.exist?(probe_path), 'each isolated bare probe must be disposable'
    end
  end

  private

  def probe_without_project_ref_changes(project, remote, exact_commit, remote_allowed, fetch_calls, probe_paths)
    before = git_output('--git-dir', project, 'for-each-ref', '--format=%(refname) %(objectname)')
    result = exact_remote_probe(project, remote, exact_commit, remote_allowed, fetch_calls, probe_paths)
    after = git_output('--git-dir', project, 'for-each-ref', '--format=%(refname) %(objectname)')
    assert_equal before, after, 'the destination project refs must stay unchanged'
    result
  end

  def exact_remote_probe(project, remote, exact_commit, remote_allowed, fetch_calls, probe_paths)
    local_present = git_capture(
      '--git-dir', project, 'cat-file', '-e', "#{exact_commit}^{commit}"
    )[2].success?
    base = { local_presence: local_present, exact_fetch_result: 'NOT_ATTEMPTED' }

    if local_present
      return base.merge(identity: 'RESOLVED_LOCAL_OBJECT', source_semantics: 'PINNED_SOURCE_AVAILABLE')
    end
    unless remote_allowed
      return base.merge(exact_fetch_result: 'NOT_AUTHORIZED', identity: 'UNRESOLVED', source_semantics: 'UNKNOWN')
    end

    Dir.mktmpdir('fable-exact-source-probe-') do |probe|
      probe_paths << probe
      git_expect('init', '--bare', '--quiet', probe)
      arguments = [
        '--git-dir', probe, 'fetch', '--no-tags', '--no-write-fetch-head', remote, exact_commit
      ]
      fetch_calls << arguments
      _stdout, _stderr, fetch_status = git_capture(*arguments)
      fetched = fetch_status.success? && git_capture(
        '--git-dir', probe, 'cat-file', '-e', "#{exact_commit}^{commit}"
      )[2].success?
      probe_refs = git_output('--git-dir', probe, 'for-each-ref', '--format=%(refname)')

      if fetched
        base.merge(
          exact_fetch_result: 'FETCHED_EXACT_OBJECT',
          identity: 'RESOLVED_REMOTE_OBJECT',
          source_semantics: 'PINNED_SOURCE_AVAILABLE',
          probe_refs_empty: probe_refs.empty?
        )
      else
        base.merge(
          exact_fetch_result: 'UNAVAILABLE',
          identity: 'UNRESOLVED',
          source_semantics: 'UNKNOWN',
          probe_refs_empty: probe_refs.empty?
        )
      end
    end
  end

  def create_repository(root, name, contents)
    path = File.join(root, name)
    git_expect('init', '--quiet', path)
    git_expect('-C', path, 'config', 'user.name', 'Fable fixture')
    git_expect('-C', path, 'config', 'user.email', 'fable-fixture@example.invalid')
    File.write(File.join(path, 'source.txt'), "#{contents}\n")
    git_expect('-C', path, 'add', 'source.txt')
    git_expect('-C', path, 'commit', '--quiet', '-m', name)
    path
  end

  def git_expect(*arguments)
    stdout, stderr, status = git_capture(*arguments)
    assert_predicate status, :success?, "git #{arguments.join(' ')} failed: #{stderr}"
    stdout
  end

  def git_output(*arguments)
    git_expect(*arguments).strip
  end

  def git_capture(*arguments)
    Open3.capture3('git', '--no-optional-locks', '-c', 'core.fsmonitor=false', *arguments)
  end
end
