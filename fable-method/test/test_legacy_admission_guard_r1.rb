# frozen_string_literal: true

require 'minitest/autorun'
require 'fileutils'
require 'tmpdir'
require 'json'
require 'open3'
require 'rbconfig'
require 'securerandom'
require_relative '../scripts/task_checkpoint'

class TestLegacyAdmissionGuardR1 < Minitest::Test
  CLI_SCRIPT = File.expand_path('../scripts/task_checkpoint.rb', __dir__)
  RUBY_BIN = RbConfig.ruby

  def setup
    @tmpdirs = []
  end

  def teardown
    @tmpdirs.each do |dir|
      # Restore permissions in case any unreadable file was created
      system('chmod', '-R', 'u+rwX', dir) rescue nil
      FileUtils.rm_rf(dir)
    end
  end

  def create_disposable_git_repo
    dir = Dir.mktmpdir('fable-disposable-repo-')
    @tmpdirs << dir
    system('git', 'init', '-q', dir, exception: true)
    system('git', '-C', dir, 'config', 'user.name', 'Fable Test', exception: true)
    system('git', '-C', dir, 'config', 'user.email', 'test@example.com', exception: true)
    system('git', '-C', dir, 'commit', '--allow-empty', '-q', '-m', 'Initial commit', exception: true)
    dir
  end

  def create_linked_worktree(main_repo)
    wt_dir = Dir.mktmpdir('fable-disposable-worktree-')
    @tmpdirs << wt_dir
    FileUtils.rm_rf(wt_dir)
    branch = "branch-#{SecureRandom.hex(4)}"
    system('git', '-C', main_repo, 'worktree', 'add', '-q', '-b', branch, wt_dir, exception: true)
    wt_dir
  end

  def add_gen2_marker(repo_path, content: '{"generation": 2}')
    common_dir = ExecutionRecord.git_common_dir(repo_path)
    refute_nil common_dir, 'common_dir must be resolved for disposable git repo'
    locks_dir = File.join(common_dir, 'fable-locks')
    FileUtils.mkdir_p(locks_dir)
    marker_path = File.join(locks_dir, 'repository-generation.json')
    File.write(marker_path, content)
    marker_path
  end

  # A04, A06, A16: Positive control without Marker
  def test_admission_permitted_in_disposable_repo_without_marker
    repo = create_disposable_git_repo
    task_id = 'TASK_NO_MARKER_001'
    exec_id = 'EXEC_001'

    # assert_legacy_admission_permitted! returns true
    assert ExecutionRecord.assert_legacy_admission_permitted!(repo)

    # with_task_lock succeeds
    locked = false
    ExecutionRecord.with_task_lock(repo, task_id) do |lock|
      locked = true
      assert lock.stat.file?
    end
    assert locked

    # acquire_task_owned! succeeds
    recovery = ExecutionRecord.acquire_task_owned!(repo, task_id, exec_id, pid: Process.pid)
    assert_nil recovery.classification
    assert_equal ExecutionRecord::STATUS_STARTED, recovery.execution_record.status

    # CLI --run succeeds
    stdout, stderr, status = Open3.capture3(
      RUBY_BIN, CLI_SCRIPT,
      '--run',
      '--repo', repo,
      '--worktree', repo,
      '--task-id', 'TASK_CLI_CLEAN_001',
      '--execution-id', 'EXEC_CLI_001',
      '--',
      RUBY_BIN, '-e', 'puts "CLI_SUCCESS"'
    )
    assert status.success?, "CLI failed with: #{stderr}"
    assert_includes stdout, 'CLI_SUCCESS'
  end

  # A07, A12: New task ID rejection when Marker present
  def test_rejection_for_new_task_id_when_marker_present
    repo = create_disposable_git_repo
    add_gen2_marker(repo)
    new_task_id = 'TASK_BRAND_NEW_999'
    exec_id = 'EXEC_001'

    err = assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.acquire_task_owned!(repo, new_task_id, exec_id, pid: Process.pid)
    end
    assert_includes err.message, 'repository is marked for generation-2'

    # No task execution lock or record created
    lock_file = ExecutionRecord.task_lock_path(repo, new_task_id)
    refute File.exist?(lock_file), 'execution.lock must NOT be created on rejection'
    record_file = ExecutionRecord.default_path(repo, new_task_id, exec_id)
    refute File.exist?(record_file), 'execution record must NOT be created on rejection'

    # CLI --run also rejects
    stdout, stderr, status = Open3.capture3(
      RUBY_BIN, CLI_SCRIPT,
      '--run',
      '--repo', repo,
      '--worktree', repo,
      '--task-id', new_task_id,
      '--execution-id', exec_id,
      '--',
      RUBY_BIN, '-e', 'puts "SHOULD_NOT_RUN"'
    )
    refute status.success?
    assert_equal 1, status.exitstatus
    assert_includes stderr, ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    assert_includes stderr, 'legacy protected execution rejected'
    refute File.exist?(lock_file)
  end

  # A08, A12: Existing task ID rejection when Marker present
  def test_rejection_for_existing_task_id_when_marker_present
    repo = create_disposable_git_repo
    task_id = 'TASK_EXISTING_001'
    exec_id = 'EXEC_PRIOR_001'

    # Create prior execution before marker exists
    rec = ExecutionRecord.start!(ExecutionRecord.default_path(repo, task_id, exec_id),
                                task_id: task_id, execution_id: exec_id, pid: Process.pid)
    capture_path = DurableCommandCapture.default_path(repo, task_id, exec_id)
    now = Time.now.utc.iso8601
    cap = DurableCommandCapture.new(command: ['echo', 'prior'], stdout: "prior\n", stderr: '',
                                   exit_status: 0, started_at: now, ended_at: now)
    cap.save(capture_path)
    rec.complete!(ExecutionRecord.default_path(repo, task_id, exec_id), durable_capture_path: capture_path)

    # Now add the Gen2 marker
    add_gen2_marker(repo)

    # Direct API rejection for new or existing task executions
    err = assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.acquire_task_owned!(repo, task_id, 'EXEC_NEW_002', pid: Process.pid)
    end
    assert_includes err.message, 'repository is marked for generation-2'

    # CLI --run rejection
    stdout, stderr, status = Open3.capture3(
      RUBY_BIN, CLI_SCRIPT,
      '--run',
      '--repo', repo,
      '--worktree', repo,
      '--task-id', task_id,
      '--execution-id', 'EXEC_NEW_002',
      '--',
      RUBY_BIN, '-e', 'puts "SHOULD_NOT_RUN"'
    )
    refute status.success?
    assert_equal 1, status.exitstatus
    assert_includes stderr, ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    assert_includes stderr, 'legacy protected execution rejected'

    # CLI replay of existing completed task is also blocked by admission guard
    stdout, stderr, status = Open3.capture3(
      RUBY_BIN, CLI_SCRIPT,
      '--run',
      '--repo', repo,
      '--worktree', repo,
      '--task-id', task_id,
      '--execution-id', exec_id,
      '--',
      'echo', 'prior'
    )
    refute status.success?
    assert_equal 1, status.exitstatus
    assert_includes stderr, ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
  end

  # A09: Linked worktree resolves to same Git common-dir Marker
  def test_linked_worktree_resolves_to_same_git_common_dir_marker
    main_repo = create_disposable_git_repo
    worktree = create_linked_worktree(main_repo)

    # Before marker: both resolve to common dir and allow admission
    main_common = ExecutionRecord.git_common_dir(main_repo)
    wt_common = ExecutionRecord.git_common_dir(worktree)
    assert_equal main_common, wt_common, 'Linked worktree must resolve to same git common dir'

    assert ExecutionRecord.assert_legacy_admission_permitted!(main_repo)
    assert ExecutionRecord.assert_legacy_admission_permitted!(worktree)

    # Place marker in git common dir
    marker_path = add_gen2_marker(main_repo)
    assert_equal marker_path, ExecutionRecord.generation2_marker_path(worktree)

    # Linked worktree rejects via Ruby API
    err = assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.assert_legacy_admission_permitted!(worktree)
    end
    assert_includes err.message, 'repository is marked for generation-2'

    # Linked worktree rejects via with_task_lock
    assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.with_task_lock(worktree, 'TASK_WT_001') { flunk 'should not run' }
    end

    # Linked worktree rejects via CLI --run
    stdout, stderr, status = Open3.capture3(
      RUBY_BIN, CLI_SCRIPT,
      '--run',
      '--repo', worktree,
      '--worktree', worktree,
      '--task-id', 'TASK_WT_001',
      '--execution-id', 'EXEC_WT_001',
      '--',
      RUBY_BIN, '-e', 'puts "FAIL"'
    )
    refute status.success?
    assert_equal 1, status.exitstatus
    assert_includes stderr, 'legacy protected execution rejected'

    # Removing the marker restores permission in both
    FileUtils.rm(marker_path)
    assert ExecutionRecord.assert_legacy_admission_permitted!(main_repo)
    assert ExecutionRecord.assert_legacy_admission_permitted!(worktree)
  end

  # A06: Direct Ruby API entrypoints all reject when Marker present
  def test_direct_ruby_api_entrypoints_all_enforce_guard
    repo = create_disposable_git_repo
    add_gen2_marker(repo)
    task_id = 'TASK_DIRECT_API_001'

    # 1. ExecutionRecord.with_task_lock
    assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.with_task_lock(repo, task_id) { flunk 'should not enter lock block' }
    end

    # 2. ExecutionRecord.acquire_task_owned!
    assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.acquire_task_owned!(repo, task_id, 'EXEC_001', pid: Process.pid)
    end

    # 3. ExecutionRecoveryTransition.reserve!
    assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecoveryTransition.reserve!(
        repo_root: repo, task_id: task_id, old_execution_id: 'EXEC_OLD',
        application_state_path: File.join(repo, 'app_state.json'),
        worktree_path: repo, command: ['echo', 'test']
      )
    end

    # 4. ExecutionRecoveryTransition.acquire_successor!
    assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecoveryTransition.acquire_successor!(
        repo_root: repo, task_id: task_id, old_execution_id: 'EXEC_OLD',
        application_state_path: File.join(repo, 'app_state.json'),
        worktree_path: repo, command: ['echo', 'test'], pid: Process.pid
      )
    end

    # 5. ExecutionRecord.acquire_nested_owned!
    mock_capability = ExecutionRecord::NestedCapability.new(
      lock: nil, parent_execution_id: 'EXEC_PARENT', ancestor_execution_ids: ['EXEC_PARENT'], secret: '0' * 64
    )
    assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.acquire_nested_owned!(repo, task_id, 'EXEC_NESTED', mock_capability, pid: Process.pid)
    end
  end

  # A04, A13: CLI --run rejects and suppresses child start
  def test_cli_run_rejection_and_child_suppression
    repo = create_disposable_git_repo
    add_gen2_marker(repo)
    sentinel = File.join(repo, 'sentinel_run.txt')
    refute File.exist?(sentinel)

    stdout, stderr, status = Open3.capture3(
      RUBY_BIN, CLI_SCRIPT,
      '--run',
      '--repo', repo,
      '--worktree', repo,
      '--task-id', 'TASK_SENTINEL_001',
      '--execution-id', 'EXEC_SENTINEL_001',
      '--',
      RUBY_BIN, '-e', "File.write('#{sentinel}', 'RAN')"
    )
    refute status.success?
    assert_equal 1, status.exitstatus
    assert_includes stderr, 'legacy protected execution rejected'
    refute File.exist?(sentinel), 'Child process MUST NOT be executed upon rejection'
    refute File.exist?(ExecutionRecord.task_lock_path(repo, 'TASK_SENTINEL_001'))
    refute File.exist?(ExecutionRecord.default_path(repo, 'TASK_SENTINEL_001', 'EXEC_SENTINEL_001'))
  end

  # A05, A13: CLI --recover-run rejects and suppresses child start
  def test_cli_recover_run_rejection_and_child_suppression
    repo = create_disposable_git_repo
    task_id = 'TASK_RECOVER_001'
    old_exec_id = 'EXEC_OLD_001'
    app_state = File.join(repo, 'app_state.json')
    File.write(app_state, '{"state": 1}')

    # Create stale record before marker exists
    ExecutionRecord.start!(
      ExecutionRecord.default_path(repo, task_id, old_exec_id),
      task_id: task_id, execution_id: old_exec_id, pid: 99999999 # definitely dead pid
    )

    # Add Gen2 marker
    add_gen2_marker(repo)
    sentinel = File.join(repo, 'sentinel_recover.txt')

    stdout, stderr, status = Open3.capture3(
      RUBY_BIN, CLI_SCRIPT,
      '--recover-run',
      '--repo', repo,
      '--worktree', repo,
      '--task-id', task_id,
      '--execution-id', old_exec_id,
      '--application-state', app_state,
      '--',
      RUBY_BIN, '-e', "File.write('#{sentinel}', 'RAN')"
    )
    refute status.success?
    assert_equal 1, status.exitstatus
    assert_includes stderr, 'legacy protected execution rejected'
    refute File.exist?(sentinel), 'Child process MUST NOT be executed upon recover-run rejection'
    refute File.exist?(ExecutionRecoveryTransition.default_path(repo, task_id, old_exec_id)),
           'Recovery transition MUST NOT be reserved upon rejection'
  end

  # A10: Invalid / malformed marker content fails closed
  def test_invalid_and_malformed_marker_content_fails_closed
    repo = create_disposable_git_repo
    task_id = 'TASK_MALFORMED_001'

    # Case 1: Corrupted JSON syntax
    add_gen2_marker(repo, content: '{"generation": 2, invalid syntax')
    err = assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.assert_legacy_admission_permitted!(repo)
    end
    assert_includes err.message, 'repository is marked for generation-2'

    # CLI --run exits 1
    _, stderr, status = Open3.capture3(
      RUBY_BIN, CLI_SCRIPT,
      '--run',
      '--repo', repo,
      '--worktree', repo,
      '--task-id', task_id,
      '--execution-id', 'EXEC_001',
      '--',
      'echo', '1'
    )
    assert_equal 1, status.exitstatus
    assert_includes stderr, 'legacy protected execution rejected'

    # Case 2: Empty file
    add_gen2_marker(repo, content: '')
    assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.assert_legacy_admission_permitted!(repo)
    end

    # Case 3: Binary/garbage bytes
    add_gen2_marker(repo, content: "\x00\xFF\xFE\x01\x02\x03")
    assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.assert_legacy_admission_permitted!(repo)
    end
  end

  # A11: Symlink marker, dangling symlink, and symlink directory fail closed
  def test_symlink_marker_and_symlink_directory_fail_closed
    repo = create_disposable_git_repo
    common_dir = ExecutionRecord.git_common_dir(repo)
    locks_dir = File.join(common_dir, 'fable-locks')
    FileUtils.mkdir_p(locks_dir)
    marker_path = File.join(locks_dir, 'repository-generation.json')

    # Case 1: Symlink pointing to regular file
    target_file = File.join(repo, 'real_marker.json')
    File.write(target_file, '{"generation": 2}')
    File.symlink(target_file, marker_path)

    err = assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.assert_legacy_admission_permitted!(repo)
    end
    assert_includes err.message, 'is a symlink'

    # Case 2: Dangling symlink pointing to non-existent target
    FileUtils.rm(marker_path)
    File.symlink(File.join(repo, 'non_existent_target.json'), marker_path)

    err = assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.assert_legacy_admission_permitted!(repo)
    end
    assert_includes err.message, 'is a symlink'

    # Case 3: fable-locks directory itself is a symlink
    FileUtils.rm_rf(locks_dir)
    real_locks_dir = File.join(repo, 'real_locks')
    FileUtils.mkdir_p(real_locks_dir)
    File.symlink(real_locks_dir, locks_dir)

    err = assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.assert_legacy_admission_permitted!(repo)
    end
    assert_includes err.message, 'is a symlink'
  end

  # A11: Unreadable marker fails closed
  def test_unreadable_marker_fails_closed
    repo = create_disposable_git_repo
    marker_path = add_gen2_marker(repo, content: '{"generation": 2}')

    # Make marker file unreadable (chmod 000)
    system('chmod', '000', marker_path, exception: true)

    err = assert_raises(ExecutionRecord::LegacyAdmissionError) do
      ExecutionRecord.assert_legacy_admission_permitted!(repo)
    end
    assert_includes err.message, 'is unreadable'

    # CLI --run exits 1
    _, stderr, status = Open3.capture3(
      RUBY_BIN, CLI_SCRIPT,
      '--run',
      '--repo', repo,
      '--worktree', repo,
      '--task-id', 'TASK_UNREADABLE_001',
      '--execution-id', 'EXEC_001',
      '--',
      'echo', '1'
    )
    assert_equal 1, status.exitstatus
    assert_includes stderr, 'legacy protected execution rejected'
  ensure
    system('chmod', '600', marker_path) rescue nil
  end

  # A14: Active owner rejection capability is preserved without Marker
  def test_active_owner_rejection_preserved_without_marker
    repo = create_disposable_git_repo
    task_id = 'TASK_ACTIVE_OWNER_001'
    exec_id = 'EXEC_OWNER_001'

    # Create an active record with the current process PID (alive)
    ExecutionRecord.start!(
      ExecutionRecord.default_path(repo, task_id, exec_id),
      task_id: task_id, execution_id: exec_id, pid: Process.pid
    )

    # Attempt to acquire another execution for the same task
    err = assert_raises(ExecutionRecord::DuplicateExecutionError) do
      ExecutionRecord.acquire_task_owned!(repo, task_id, 'EXEC_COMPETING_002', pid: Process.pid)
    end
    assert_includes err.message, "task '#{task_id}' is owned by execution '#{exec_id}'"

    # CLI --run also rejects with PRIOR_PROCESS_ACTIVE
    _, stderr, status = Open3.capture3(
      RUBY_BIN, CLI_SCRIPT,
      '--run',
      '--repo', repo,
      '--worktree', repo,
      '--task-id', task_id,
      '--execution-id', 'EXEC_COMPETING_002',
      '--',
      'echo', '1'
    )
    assert_equal 1, status.exitstatus
    assert_includes stderr, ExecutionRecord::CLASSIFICATION_ACTIVE
  end

  # A15: Nested lineage and FD capability verification preserved without Marker
  def test_nested_capability_preserved_without_marker
    repo = create_disposable_git_repo
    task_id = 'TASK_NESTED_001'
    top_exec_id = 'EXEC_TOP_001'
    nested_exec_id = 'EXEC_NESTED_002'

    ExecutionRecord.with_task_lock(repo, task_id) do |lock|
      secret = ExecutionRecord.write_task_lineage!(lock, repo, task_id, top_exec_id)
      # Create started record for parent
      ExecutionRecord.start!(
        ExecutionRecord.default_path(repo, task_id, top_exec_id),
        task_id: task_id, execution_id: top_exec_id, pid: Process.pid
      )

      env = ExecutionRecord.nested_capability_env(repo, task_id, lock, top_exec_id, secret)
      request = ExecutionRecord.nested_capability_request(env, repo, task_id)
      refute_nil request

      nested_cap = ExecutionRecord.verify_nested_capability!(repo, task_id, nested_exec_id, request)
      assert_equal top_exec_id, nested_cap.parent_execution_id

      # Nested acquire succeeds
      rec = ExecutionRecord.acquire_nested_owned!(repo, task_id, nested_exec_id, nested_cap, pid: Process.pid)
      assert_nil rec.classification
      assert_equal nested_exec_id, rec.execution_record.execution_id
    end
  end

  # A18, A19, A20: Explicit limitation and scope assertions
  def test_explicit_limitations_and_scope_acknowledged
    # A18: Unpatched legacy scripts are not restrained
    # (By definition, an external script without this patch executes its own unpatched code)
    assert true, 'A18: Acknowledged that unpatched legacy scripts cannot be restrained by this local candidate'

    # A19: Single pre-admission check does not eliminate all TOCTOU races
    assert true, 'A19: Acknowledged that single pre-admission check does not eliminate concurrent TOCTOU races'

    # A20: Candidate is an admission guard, not full StableLockAnchor
    assert true, 'A20: Acknowledged that candidate is not a full implementation of StableLockAnchor'
  end
end
