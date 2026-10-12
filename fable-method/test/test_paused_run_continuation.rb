# frozen_string_literal: true

require 'minitest/autorun'
require 'fileutils'
require 'tmpdir'
require 'json'
require 'digest'
require 'open3'
require 'rbconfig'
require_relative '../scripts/task_checkpoint'

class PausedRunContinuationTest < Minitest::Test
  TASK_ID = 'SYNTHETIC_B649_TASK'
  PREDECESSOR_IDS = %w[RUN1 RUN2].freeze
  NEXT_ATTEMPT = 4_844_449
  COMPLETED_ATTEMPTS = NEXT_ATTEMPT - 1
  TOTAL_ATTEMPTS = 37_674_720
  CLI = File.expand_path('../scripts/task_checkpoint.rb', __dir__)

  def setup
    @tmpdir = Dir.mktmpdir('paused_continuation_test_')
    @repo = File.join(@tmpdir, 'stable-record-root')
    @worktree = File.join(@tmpdir, 'worktree')
    @caller = File.join(@tmpdir, 'caller')
    [@repo, @worktree, @caller].each { |path| FileUtils.mkdir_p(path) }
    _git_out, git_err, git_status = Open3.capture3('git', '-C', @repo, 'init', '--quiet')
    raise "synthetic repository initialization failed: #{git_err}" unless git_status.success?

    StableLockAnchor.initialize_repository_generation!(@repo, confirm_legacy_workers_quiescent: true)
    ExecutionRecord.with_task_lock(@repo, TASK_ID) {}
    @source = File.join(@tmpdir, 'synthetic-source.rb')
    @baseline = File.join(@tmpdir, 'synthetic-baseline.json')
    @journal = File.join(@tmpdir, 'synthetic-journal.jsonl')
    @checkpoint = File.join(@tmpdir, 'synthetic-scientific-checkpoint.json')
    @authorization = File.join(@tmpdir, 'synthetic-owner-authorization.txt')
    @attempt_marker = File.join(@tmpdir, 'attempt.txt')
    File.binwrite(@source, "synthetic source\n")
    File.binwrite(@baseline, "synthetic baseline\n")
    seed_predecessor_records(exit_status: 1)
    seed_paused_checkpoint
  end

  def teardown
    FileUtils.remove_entry(@tmpdir) if @tmpdir && File.directory?(@tmpdir)
  end

  def simulated_command
    [RbConfig.ruby, '-e',
     'File.write(ARGV.fetch(0), ENV.fetch("FABLE_PAUSED_CONTINUATION_NEXT_ATTEMPT"))', @attempt_marker]
  end

  def cli_args(successor_id: 'RUN3', auth_path: @authorization, command: simulated_command)
    ['--continue-paused-run', '--repo', @repo, '--worktree', @worktree,
     '--task-id', TASK_ID, '--previous-execution-id', 'RUN2', '--execution-id', successor_id,
     '--application-state', @checkpoint, '--owner-authorization', auth_path, '--', *command]
  end

  def record_path(execution_id)
    ExecutionRecord.default_path(@repo, TASK_ID, execution_id)
  end

  def capture_path(execution_id)
    DurableCommandCapture.default_path(@repo, TASK_ID, execution_id)
  end

  def capture_sha(execution_id)
    Digest::SHA256.hexdigest(File.binread(capture_path(execution_id)))
  end

  def run_cli(args)
    Open3.capture3(RbConfig.ruby, CLI, *args, chdir: @caller)
  end

  def continuation_target(successor_id: 'RUN3', command: simulated_command, checkpoint_sha256: nil)
    checkpoint_sha256 ||= Digest::SHA256.file(@checkpoint).hexdigest
    PausedRunContinuationTransition.authorization_target(
      task_id: TASK_ID,
      predecessor_execution_id: 'RUN2',
      successor_execution_id: successor_id,
      checkpoint_sha256: checkpoint_sha256,
      journal_sha256: Digest::SHA256.file(@journal).hexdigest,
      source_sha256: Digest::SHA256.file(@source).hexdigest,
      baseline_sha256: Digest::SHA256.file(@baseline).hexdigest,
      next_attempt: NEXT_ATTEMPT,
      worktree_path: File.realpath(@worktree),
      command_sha256: ExecutionRecoveryTransition.command_sha256!(command)
    )
  end

  def write_authorization(successor_id: 'RUN3', command: simulated_command, checkpoint_sha256: nil,
                          path: @authorization)
    target = continuation_target(successor_id: successor_id, command: command,
                                 checkpoint_sha256: checkpoint_sha256)
    File.write(path, <<~AUTH)
      OWNER AUTHORIZATION — #{TASK_ID}

      I authorize exactly:
      - ACTION=CONTINUE_PAUSED_RUN; TARGET=#{target}

      OWNER_ACTION_AUTHORIZATION:
      PRESENT_IN_CURRENT_OWNER_MESSAGE

      AUTHORIZATION_HANDOFF_MODE:
      OWNER_DIRECT_PACKET

      AUTHORIZATION_EVIDENCE:
      CURRENT_OWNER_USER_MESSAGE

      AUTHORIZED_ACTION_SCOPE:
      ACTION=CONTINUE_PAUSED_RUN; TARGET=#{target}
    AUTH
    path
  end

  def seed_paused_checkpoint(scientific_status: 'PAUSED_RESUMABLE')
    source_sha = Digest::SHA256.file(@source).hexdigest
    baseline_sha = Digest::SHA256.file(@baseline).hexdigest
    rows = []
    prior_last = 0
    run1_last = COMPLETED_ATTEMPTS - 1
    622.times do |index|
      last_attempt = ((index + 1) * run1_last) / 622
      rows << journal_row(index + 1, prior_last + 1, last_attempt, 'RUN1', source_sha, baseline_sha)
      prior_last = last_attempt
    end
    rows << journal_row(623, prior_last + 1, COMPLETED_ATTEMPTS, 'RUN2', source_sha, baseline_sha)
    journal_bytes = rows.map { |row| "#{JSON.generate(row)}\n" }.join
    File.binwrite(@journal, journal_bytes)

    @checkpoint_fields = {
      'schema_version' => 1,
      'task_id' => TASK_ID,
      'scientific_status' => scientific_status,
      'completed_attempts' => COMPLETED_ATTEMPTS,
      'total_attempts' => TOTAL_ATTEMPTS,
      'next_expected_attempt' => NEXT_ATTEMPT,
      'journal_path' => @journal,
      'journal_prefix_sha256' => Digest::SHA256.hexdigest(journal_bytes),
      'journal_record_count' => rows.length,
      'journal_execution_ids' => PREDECESSOR_IDS,
      'source_path' => @source,
      'source_sha256' => source_sha,
      'baseline_path' => @baseline,
      'baseline_sha256' => baseline_sha
    }
    write_checkpoint
    write_authorization
  end

  def write_checkpoint
    File.binwrite(@checkpoint, JSON.pretty_generate(@checkpoint_fields))
  end

  def advance_scientific_checkpoint_after_continuation
    File.open(@journal, 'ab') do |file|
      file.write("#{JSON.generate(journal_row(624, NEXT_ATTEMPT, NEXT_ATTEMPT, 'RUN3',
                                               @checkpoint_fields.fetch('source_sha256'),
                                               @checkpoint_fields.fetch('baseline_sha256')))}\n")
    end
    @checkpoint_fields['scientific_status'] = 'PAUSED_RESUMABLE'
    @checkpoint_fields['completed_attempts'] = NEXT_ATTEMPT
    @checkpoint_fields['next_expected_attempt'] = NEXT_ATTEMPT + 1
    @checkpoint_fields['journal_prefix_sha256'] = Digest::SHA256.file(@journal).hexdigest
    @checkpoint_fields['journal_record_count'] += 1
    @checkpoint_fields['journal_execution_ids'] = %w[RUN1 RUN2 RUN3]
    write_checkpoint
  end

  def journal_row(sequence, first_attempt, last_attempt, execution_id, source_sha, baseline_sha)
    {
      'sequence' => sequence,
      'first_attempt' => first_attempt,
      'last_attempt' => last_attempt,
      'status' => 'COMPLETED',
      'execution_id' => execution_id,
      'source_sha256' => source_sha,
      'baseline_sha256' => baseline_sha
    }
  end

  def seed_predecessor_records(exit_status:)
    PREDECESSOR_IDS.each_with_index do |execution_id, index|
      status = execution_id == 'RUN2' ? exit_status : 0
      path = capture_path(execution_id)
      FileUtils.mkdir_p(File.dirname(path))
      DurableCommandCapture.new(
        command: ['synthetic-runner', execution_id], stdout: '', stderr: '', exit_status: status,
        started_at: '2026-10-08T00:00:00Z', ended_at: '2026-10-08T00:01:00Z'
      ).save(path)
      ExecutionRecord.new(
        task_id: TASK_ID, execution_id: execution_id, pid: Process.pid,
        status: ExecutionRecord::STATUS_COMPLETED, durable_capture_path: path,
        started_at: '2026-10-08T00:00:00Z', ended_at: "2026-10-08T00:0#{index + 1}:00Z"
      ).save(record_path(execution_id))
    end
  end

  def test_exact_owner_authorized_pause_runs_next_attempt_once_and_preserves_predecessor
    old_record = File.binread(record_path('RUN2'))
    old_capture = File.binread(capture_path('RUN2'))

    stdout, stderr, status = run_cli(cli_args)

    assert status.success?, "continuation failed: #{stdout} #{stderr}"
    assert_equal '', stderr
    assert_equal NEXT_ATTEMPT.to_s, File.read(@attempt_marker)
    record = ExecutionRecord.load(record_path('RUN3'))
    assert_equal 'RUN2', record.continuation_from_execution_id
    assert_equal Digest::SHA256.file(@checkpoint).hexdigest, record.continuation_checkpoint_sha256
    transition_path = PausedRunContinuationTransition.default_path(
      @repo, TASK_ID, Digest::SHA256.file(@checkpoint).hexdigest
    )
    transition = PausedRunContinuationTransition.load(transition_path)
    assert_equal Digest::SHA256.file(transition_path).hexdigest, record.continuation_transition_sha256
    assert_equal NEXT_ATTEMPT, transition.next_attempt
    assert_equal Digest::SHA256.hexdigest(old_record), transition.predecessor_execution_sha256
    assert_equal Digest::SHA256.hexdigest(old_capture), transition.predecessor_capture_sha256
    assert_equal simulated_command, DurableCommandCapture.load(capture_path('RUN3')).command

    replay_out, replay_err, replay_status = run_cli(cli_args)
    assert replay_status.success?, "completed continuation replay failed: #{replay_out} #{replay_err}"
    assert_equal NEXT_ATTEMPT.to_s, File.read(@attempt_marker)
    assert_equal old_record, File.binread(record_path('RUN2'))
    assert_equal old_capture, File.binread(capture_path('RUN2'))
    assert_equal 'RUN3', record.execution_id
  end

  def test_completed_successor_replays_after_checkpoint_and_journal_advance
    old_record = File.binread(record_path('RUN2'))
    old_capture = File.binread(capture_path('RUN2'))
    stdout, stderr, status = run_cli(cli_args)
    assert status.success?, "continuation failed: #{stdout} #{stderr}"
    run3_record = File.binread(record_path('RUN3'))
    run3_capture = File.binread(capture_path('RUN3'))

    advance_scientific_checkpoint_after_continuation
    replay_out, replay_err, replay_status = run_cli(cli_args)

    assert replay_status.success?, "advanced-checkpoint replay failed: #{replay_out} #{replay_err}"
    assert_equal NEXT_ATTEMPT.to_s, File.read(@attempt_marker)
    assert_equal run3_record, File.binread(record_path('RUN3'))
    assert_equal run3_capture, File.binread(capture_path('RUN3'))
    assert_equal old_record, File.binread(record_path('RUN2'))
    assert_equal old_capture, File.binread(capture_path('RUN2'))
  end

  def test_wrong_checkpoint_sha_does_not_match_owner_authorization
    File.open(@checkpoint, 'ab') { |file| file.write("\n") }

    _stdout, stderr, status = run_cli(cli_args)

    refute status.success?
    assert_includes stderr, 'Owner authorization does not exactly bind'
    refute File.exist?(record_path('RUN3'))
  end

  def test_wrong_source_hash_blocks_continuation
    File.open(@source, 'ab') { |file| file.write("changed\n") }

    _stdout, stderr, status = run_cli(cli_args)

    refute status.success?
    assert_includes stderr, 'source or baseline bytes do not match'
    refute File.exist?(record_path('RUN3'))
  end

  def test_wrong_baseline_hash_blocks_continuation
    File.open(@baseline, 'ab') { |file| file.write("changed\n") }

    _stdout, stderr, status = run_cli(cli_args)

    refute status.success?
    assert_includes stderr, 'source or baseline bytes do not match'
    refute File.exist?(record_path('RUN3'))
  end

  def test_discontinuous_original_journal_prefix_blocks_continuation
    rows = File.readlines(@journal, chomp: true).map { |line| JSON.parse(line) }
    rows.fetch(1)['first_attempt'] += 1
    journal_bytes = rows.map { |row| "#{JSON.generate(row)}\n" }.join
    File.binwrite(@journal, journal_bytes)
    @checkpoint_fields['journal_prefix_sha256'] = Digest::SHA256.hexdigest(journal_bytes)
    write_checkpoint
    write_authorization

    _stdout, stderr, status = run_cli(cli_args)

    refute status.success?
    assert_includes stderr, 'original journal prefix is not contiguous at record 2'
    refute File.exist?(record_path('RUN3'))
  end

  def test_missing_owner_authorization_blocks_continuation
    FileUtils.rm_f(@authorization)

    _stdout, stderr, status = run_cli(cli_args)

    refute status.success?
    assert_includes stderr, 'Owner authorization could not be verified'
    refute File.exist?(record_path('RUN3'))
  end

  def test_active_scientific_writer_blocks_continuation
    ExecutionRecord.start!(record_path('ACTIVE_WRITER'), task_id: TASK_ID,
                           execution_id: 'ACTIVE_WRITER', pid: Process.pid)

    _stdout, stderr, status = run_cli(cli_args)

    refute status.success?
    assert_includes stderr, ExecutionRecord::CLASSIFICATION_ACTIVE
    refute File.exist?(record_path('RUN3'))
  end

  def test_concurrent_task_lock_holder_blocks_continuation
    lock_path = ExecutionRecord.task_lock_path(@repo, TASK_ID)
    FileUtils.mkdir_p(File.dirname(lock_path))
    File.open(lock_path, File::RDWR | File::CREAT, 0o600) do |lock|
      assert lock.flock(File::LOCK_EX | File::LOCK_NB)

      _stdout, stderr, status = run_cli(cli_args)

      refute status.success?
      assert_includes stderr, "task '#{TASK_ID}' has another execution claim in progress"
      refute File.exist?(record_path('RUN3'))
    end
  end

  def test_generic_failed_and_completed_predecessors_cannot_bypass_pause_gate
    [0, 7].each do |exit_status|
      seed_predecessor_records(exit_status: exit_status)
      write_authorization

      _stdout, stderr, status = run_cli(cli_args)

      refute status.success?
      assert_includes stderr, 'did not capture the authorized pause exit'
      refute File.exist?(record_path('RUN3'))
    end
  end

  def test_completed_scientific_terminal_checkpoint_is_not_resumable
    seed_paused_checkpoint(scientific_status: 'COMPLETED')

    _stdout, stderr, status = run_cli(cli_args)

    refute status.success?
    assert_includes stderr, 'only a PAUSED_RESUMABLE scientific checkpoint'
    refute File.exist?(record_path('RUN3'))
  end

  def test_generic_failed_scientific_checkpoint_is_not_resumable
    seed_paused_checkpoint(scientific_status: 'FAILED')

    _stdout, stderr, status = run_cli(cli_args)

    refute status.success?
    assert_includes stderr, 'only a PAUSED_RESUMABLE scientific checkpoint'
    refute File.exist?(record_path('RUN3'))
  end

  def test_same_checkpoint_cannot_reserve_a_second_successor_identity
    first = run_cli(cli_args)
    assert first[2].success?, "initial continuation failed: #{first[0]} #{first[1]}"
    write_authorization(successor_id: 'RUN3B')

    _stdout, stderr, status = run_cli(cli_args(successor_id: 'RUN3B'))

    refute status.success?
    assert_includes stderr, 'already bound to another checkpoint, command, or owner authority'
    refute File.exist?(record_path('RUN3B'))
    assert_equal NEXT_ATTEMPT.to_s, File.read(@attempt_marker)
  end

  def test_interrupted_started_successor_is_never_relaunched
    dead_pid = Process.spawn(RbConfig.ruby, '-e', 'exit 0')
    Process.wait(dead_pid)
    kwargs = {
      repo_root: @repo,
      task_id: TASK_ID,
      predecessor_execution_id: 'RUN2',
      successor_execution_id: 'RUN3',
      application_state_path: @checkpoint,
      owner_authorization_path: @authorization,
      worktree_path: @worktree,
      command: simulated_command
    }

    first = PausedRunContinuationTransition.acquire_successor!(**kwargs, pid: dead_pid)
    assert_nil first.classification
    started_bytes = File.binread(record_path('RUN3'))
    refute File.exist?(capture_path('RUN3'))
    assert_equal ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE,
                 first.execution_record.classify

    error = assert_raises(ExecutionRecord::UnresolvedExecutionStateError) do
      PausedRunContinuationTransition.acquire_successor!(**kwargs, pid: Process.pid)
    end
    assert_includes error.message, 'cannot be relaunched'
    assert_equal started_bytes, File.binread(record_path('RUN3'))
    refute File.exist?(capture_path('RUN3'))
    refute File.exist?(@attempt_marker)
  end

  def test_generic_run_cannot_supply_continuation_authority_flags
    args = ['--run', '--repo', @repo, '--worktree', @worktree, '--task-id', TASK_ID,
            '--execution-id', 'RUN3', '--application-state', @checkpoint, '--', *simulated_command]

    _stdout, stderr, status = run_cli(args)

    refute status.success?
    assert_includes stderr, '--application-state is only valid with --recover-run or --continue-paused-run'
    refute File.exist?(record_path('RUN3'))
  end
end
