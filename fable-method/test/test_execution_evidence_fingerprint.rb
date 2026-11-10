# frozen_string_literal: true

require 'minitest/autorun'
require 'tmpdir'
require 'fileutils'
require 'json'
require 'digest'
require 'open3'
require 'minitest/mock'
require_relative '../scripts/execution_evidence_fingerprint'

class ExecutionEvidenceFingerprintTest < Minitest::Test
  FIXED_TIME = '2026-11-09T00:00:00.000000Z'

  def setup
    parent = ENV.fetch('FABLE_TASK_EVIDENCE_ROOT', Dir.tmpdir)
    FileUtils.mkdir_p(parent)
    @tmpdir = Dir.mktmpdir('execution-evidence-fingerprint-', parent)
    @repo = File.join(@tmpdir, 'repo')
    FileUtils.mkdir_p(@repo)
    git('init', '--quiet', '--initial-branch=main')
    git('config', 'core.filemode', 'true')
    git('config', 'user.name', 'Fable Test')
    git('config', 'user.email', 'fable-test@example.invalid')

    File.write(File.join(@repo, 'tracked.txt'), "initial\n")
    File.write(File.join(@repo, 'script.sh'), "#!/bin/sh\n")
    File.chmod(0o644, File.join(@repo, 'script.sh'))
    git('add', 'tracked.txt', 'script.sh')
    git('commit', '--quiet', '-m', 'seed')
    File.write(File.join(@repo, 'tracked.txt'), "modified\n")
    File.chmod(0o755, File.join(@repo, 'script.sh'))
    File.write(File.join(@repo, 'new.txt'), "new input\n")
    @result_path = File.join(@tmpdir, 'result.json')
  end

  def teardown
    FileUtils.remove_entry(@tmpdir) if @tmpdir && File.directory?(@tmpdir)
  end

  def test_canonical_serialization_and_digest_are_reproducible_from_saved_payload
    left = { 'z' => [3, 2, 1], 'a' => { 'y' => true, 'x' => 'value' } }
    right = { 'a' => { 'x' => 'value', 'y' => true }, 'z' => [3, 2, 1] }
    canonical = ExecutionEvidenceFingerprint.canonical_json(left)

    assert_equal canonical, ExecutionEvidenceFingerprint.canonical_json(right)

    plan = prepare(hashed_paths: %w[tracked.txt script.sh new.txt])
    saved_payload = File.binread(plan.fingerprint_payload_path)
    assert_equal plan.fingerprint_payload, saved_payload
    assert_equal Digest::SHA256.hexdigest(saved_payload), plan.fingerprint_sha256
    assert_equal plan.fingerprint_sha256,
                 ExecutionEvidenceFingerprint.digest_saved_payload(plan.fingerprint_payload_path)
    assert_equal JSON.parse(saved_payload), JSON.parse(plan.fingerprint_payload)
    assert_equal saved_payload, plan.receipt.dig('fingerprint', 'canonical_payload')
    assert_equal plan.fingerprint_sha256, plan.receipt.dig('fingerprint', 'sha256')
    assert_equal plan.validated_result_path, plan.receipt.fetch('validated_result_path')
  end

  def test_observation_captures_every_declared_input_with_status_mode_and_sha
    plan = prepare(hashed_paths: %w[tracked.txt script.sh new.txt])
    payload = JSON.parse(plan.fingerprint_payload)
    files = payload.fetch('hashed_inputs').to_h { |entry| [entry.fetch('path'), entry] }

    assert_equal 'main', payload.fetch('branch')
    assert_match(/\A[0-9a-f]{40,64}\z/, payload.fetch('head'))
    assert_match(/\A[0-9a-f]{40,64}\z/, payload.fetch('tree'))
    assert_equal 3, files.length
    assert_equal %w[new.txt script.sh tracked.txt], files.keys.sort
    assert_equal '??', files.fetch('new.txt').fetch('status')
    assert_equal ' M', files.fetch('tracked.txt').fetch('status')
    assert_equal '100755', files.fetch('script.sh').fetch('mode')
    assert_equal Digest::SHA256.file(File.join(@repo, 'new.txt')).hexdigest,
                 files.fetch('new.txt').fetch('sha256')
    assert_equal Digest::SHA256.file(File.join(@repo, 'script.sh')).hexdigest,
                 files.fetch('script.sh').fetch('sha256')
    assert_equal Digest::SHA256.file(File.join(@repo, 'tracked.txt')).hexdigest,
                 files.fetch('tracked.txt').fetch('sha256')
    assert_equal 3, payload.fetch('status').length
    assert_equal FIXED_TIME, payload.fetch('observed_at')
    assert_equal File.realpath(@repo), payload.dig('source_identity', 'worktree_path')
    assert_equal plan.result_path, payload.fetch('result_path')
  end

  def test_missing_required_hashed_inputs_fail_closed
    assert_raises(ExecutionEvidenceFingerprint::Error) do
      prepare(hashed_paths: [])
    end
    assert_raises(ExecutionEvidenceFingerprint::Error) do
      prepare(hashed_paths: %w[tracked.txt missing.txt])
    end
  end

  def test_validator_execution_argv_and_receipt_share_the_single_generated_path
    generator_calls = 0
    validator_path = nil
    plan = prepare(
      hashed_paths: %w[tracked.txt],
      result_path_generator: lambda {
        generator_calls += 1
        @result_path
      },
      result_path_validator: lambda { |path|
        validator_path = path
        true
      }
    )
    argv_path = plan.execution_argv.fetch(plan.execution_argv.index('--result-path') + 1)

    assert_equal 1, generator_calls
    assert_same plan.result_path, plan.validated_result_path
    assert_same plan.result_path, validator_path
    assert_same plan.result_path, argv_path
    assert_same plan.result_path, plan.receipt.fetch('result_path')
    assert_equal @result_path, plan.result_path
  end

  def test_validator_cannot_substitute_a_different_result_path
    alternate_path = File.join(@tmpdir, 'alternate-result.json')

    assert_raises(ExecutionEvidenceFingerprint::Error) do
      prepare(
        hashed_paths: %w[tracked.txt],
        result_path_validator: ->(_path) { alternate_path }
      )
    end
  end

  def test_restore_reuses_saved_payload_without_overwriting_it
    original = prepare(hashed_paths: %w[tracked.txt])
    saved_payload = File.binread(original.fingerprint_payload_path)
    File.write(original.result_path, 'completed result')

    restored = ExecutionEvidenceFingerprint.restore(
      repository_path: @repo,
      worktree_path: @repo,
      hashed_paths: %w[tracked.txt],
      result_path: @result_path,
      result_path_validator: ->(path) { path == @result_path },
      execution_argv: ['synthetic-runner', '--mode', 'verify']
    )

    assert_equal original.result_path, restored.validated_result_path
    assert_equal original.execution_argv, restored.execution_argv
    assert_equal JSON.parse(JSON.generate(original.receipt)), JSON.parse(JSON.generate(restored.receipt))
    assert_equal saved_payload, File.binread(original.fingerprint_payload_path)
  end

  def test_unstable_snapshot_fails_closed_before_saving_payload
    original_capture = ExecutionEvidenceFingerprint.method(:capture_hashed_inputs)
    capture_count = 0
    unstable_capture = lambda do |*arguments|
      result = original_capture.call(*arguments)
      capture_count += 1
      File.write(File.join(@repo, 'tracked.txt'), "changed during capture\n") if capture_count == 1
      result
    end

    error = ExecutionEvidenceFingerprint.stub(:capture_hashed_inputs, unstable_capture) do
      assert_raises(ExecutionEvidenceFingerprint::Error) do
        prepare(hashed_paths: %w[tracked.txt])
      end
    end
    assert_includes error.message, 'changed while the fingerprint was captured'
    refute File.exist?("#{@result_path}#{ExecutionEvidenceFingerprint::FINGERPRINT_SUFFIX}")
  end

  def test_prelaunch_verification_fails_closed_when_a_hashed_input_changes
    plan = prepare(hashed_paths: %w[tracked.txt])
    File.write(File.join(@repo, 'tracked.txt'), "changed after capture\n")

    error = assert_raises(ExecutionEvidenceFingerprint::Error) do
      ExecutionEvidenceFingerprint.verify_current!(plan, worktree_path: @repo)
    end
    assert_includes error.message, 'changed'
  end

  private

  def prepare(hashed_paths:, result_path_generator: -> { @result_path },
              result_path_validator: ->(_path) { true })
    ExecutionEvidenceFingerprint.prepare(
      repository_path: @repo,
      worktree_path: @repo,
      hashed_paths: hashed_paths,
      result_path_generator: result_path_generator,
      result_path_validator: result_path_validator,
      execution_argv: ['synthetic-runner', '--mode', 'verify'],
      observed_at: FIXED_TIME
    )
  end

  def git(*arguments)
    stdout, stderr, status = Open3.capture3('git', '-C', @repo, *arguments)
    raise "git #{arguments.join(' ')} failed: #{stderr}" unless status.success?

    stdout
  end
end
