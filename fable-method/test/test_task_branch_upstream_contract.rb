require 'minitest/autorun'
require 'open3'
require 'tmpdir'

class TaskBranchUpstreamContractTest < Minitest::Test
  OPERATIONAL_GATES = File.read(File.expand_path('../shared/references/operational-gates.md', __dir__))

  def test_first_publication_tracks_the_same_named_remote_branch
    publish_command = 'git push --set-upstream origin HEAD:refs/heads/<task-branch>'
    assert_includes OPERATIONAL_GATES, publish_command
    assert_includes OPERATIONAL_GATES, "returns\n`origin/<task-branch>`"

    Dir.mktmpdir('fable-task-branch-upstream-', ENV.fetch('TMPDIR')) do |scratch|
      remote = File.join(scratch, 'remote.git')
      repo = File.join(scratch, 'repo')
      task_branch = 'codex/test-task-branch'

      run_git!('init', '--bare', '--initial-branch=master', remote)
      run_git!('init', '--initial-branch=master', repo)
      run_git!('-C', repo, 'remote', 'add', 'origin', remote)
      run_git!('-C', repo, 'config', 'user.name', 'Fable Fixture')
      run_git!('-C', repo, 'config', 'user.email', 'fable-fixture@example.invalid')

      File.write(File.join(repo, 'history.txt'), "base\n")
      run_git!('-C', repo, 'add', 'history.txt')
      run_git!('-C', repo, 'commit', '-m', 'base')
      run_git!('-C', repo, 'push', '--set-upstream', 'origin', 'master')
      run_git!('-C', repo, 'fetch', 'origin')
      run_git!('-C', repo, 'switch', '-c', task_branch, 'origin/master')

      File.write(File.join(repo, 'history.txt'), "base\ntask\n")
      run_git!('-C', repo, 'add', 'history.txt')
      run_git!('-C', repo, 'commit', '-m', 'task')
      run_git!('-C', repo, 'push', 'origin', "HEAD:refs/heads/#{task_branch}")

      before_upstream = run_git!('-C', repo, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}').strip
      before_status = run_git!('-C', repo, 'status', '--short', '--branch')
      assert_equal 'origin/master', before_upstream
      assert_includes before_status, '[ahead 1]'

      run_git!('-C', repo, 'push', '--set-upstream', 'origin', "HEAD:refs/heads/#{task_branch}")

      after_upstream = run_git!('-C', repo, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}').strip
      after_status = run_git!('-C', repo, 'status', '--short', '--branch')
      assert_equal "origin/#{task_branch}", after_upstream
      assert_includes after_status, "#{task_branch}...origin/#{task_branch}"
      ref_ahead = run_git!('-C', repo, 'rev-list', '--count', '@{upstream}..HEAD').strip
      ref_behind = run_git!('-C', repo, 'rev-list', '--count', 'HEAD..@{upstream}').strip
      assert_equal '0', ref_ahead
      assert_equal '0', ref_behind
    end
  end

  private

  def run_git!(*args)
    git_environment = { 'GIT_CONFIG_NOSYSTEM' => '1', 'GIT_CONFIG_GLOBAL' => File::NULL }
    stdout, stderr, status = Open3.capture3(git_environment, 'git', *args)
    assert status.success?, "git #{args.join(' ')} failed: #{stderr}\n#{stdout}"
    stdout
  end
end
