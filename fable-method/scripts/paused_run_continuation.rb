# frozen_string_literal: true

# A checkpoint-safe pause is a completed protected execution with a separate,
# explicit scientific continuation authority. This transition never rewrites
# the completed predecessor or its durable capture.
class PausedRunContinuationTransition
  SCHEMA_VERSION = 1
  STATUS_RESERVED = 'SUCCESSOR_RESERVED'
  ACTION = 'CONTINUE_PAUSED_RUN'
  SCIENTIFIC_PAUSED = 'PAUSED_RESUMABLE'
  CHILD_ENV_KEYS = %w[
    FABLE_PAUSED_CONTINUATION_PREDECESSOR_EXECUTION_ID
    FABLE_PAUSED_CONTINUATION_CHECKPOINT_SHA256
    FABLE_PAUSED_CONTINUATION_NEXT_ATTEMPT
  ].freeze

  CHECKPOINT_KEYS = %w[
    schema_version task_id scientific_status completed_attempts total_attempts next_expected_attempt
    journal_path journal_prefix_sha256 journal_record_count journal_execution_ids
    source_path source_sha256 baseline_path baseline_sha256
  ].freeze
  JOURNAL_RECORD_KEYS = %w[
    sequence first_attempt last_attempt status execution_id source_sha256 baseline_sha256
  ].freeze
  TRANSITION_KEYS = %w[
    schema_version task_id predecessor_execution_id predecessor_execution_sha256
    predecessor_capture_sha256 successor_execution_id checkpoint_path checkpoint_sha256
    journal_path journal_prefix_sha256 journal_record_count journal_execution_ids
    journal_execution_evidence source_path source_sha256 baseline_path baseline_sha256
    scientific_status completed_attempts total_attempts next_attempt
    owner_authorization_path authorization_text_sha256
    worktree_path successor_command_sha256 status created_at
  ].freeze

  class DuplicateJSONKeyError < StandardError; end

  class UniqueJSONHash < Hash
    def []=(key, value)
      raise DuplicateJSONKeyError, "duplicate JSON key: #{key}" if key?(key)

      super
    end
  end

  Recovery = Struct.new(:classification, :execution_record, :durable_capture, :recovery_transition,
                        keyword_init: true)

  attr_reader(*TRANSITION_KEYS.map(&:to_sym))

  def initialize(attrs = {})
    TRANSITION_KEYS.each do |key|
      value = attrs.key?(key.to_sym) ? attrs[key.to_sym] : attrs[key]
      instance_variable_set("@#{key}", value)
    end
  end

  def to_h
    TRANSITION_KEYS.to_h { |key| [key, instance_variable_get("@#{key}")] }
  end

  def to_json(*args)
    JSON.pretty_generate(to_h, *args)
  end

  def self.default_path(repo_root, task_id, checkpoint_sha256)
    unless checkpoint_sha256.to_s.match?(/\A[0-9a-f]{64}\z/)
      raise ExecutionRecord::UnresolvedExecutionStateError, 'checkpoint SHA-256 is malformed'
    end

    File.join(repo_root, '.fable', 'checkpoints', task_id.to_s, 'continuations',
              "#{checkpoint_sha256}.json")
  end

  def self.acquire_successor!(repo_root:, task_id:, predecessor_execution_id:,
                              successor_execution_id:, application_state_path:,
                              owner_authorization_path:, worktree_path:, command:, pid:)
    worktree = ExecutionRecoveryTransition.normalized_worktree_path(worktree_path)
    ExecutionRecord.with_task_lock(repo_root, task_id) do
      ExecutionRecord.verify_no_active_owner!(
        repo_root, task_id, except_execution_ids: [successor_execution_id.to_s],
        reject_unrecovered_stale: true
      )

      expected = expected_transition_fields(
        repo_root: repo_root,
        task_id: task_id,
        predecessor_execution_id: predecessor_execution_id,
        successor_execution_id: successor_execution_id,
        application_state_path: application_state_path,
        owner_authorization_path: owner_authorization_path,
        worktree_path: worktree,
        command: command
      )
      path = default_path(repo_root, task_id, expected.fetch('checkpoint_sha256'))
      transition = reserve!(repo_root, path, expected)
      transition.verify_readback!(path)
      transition.verify_inputs!(
        repo_root: repo_root,
        application_state_path: application_state_path,
        owner_authorization_path: owner_authorization_path,
        worktree_path: worktree,
        command: command
      )

      successor_path = ExecutionRecord.default_path(repo_root, task_id, successor_execution_id)
      capture_path = DurableCommandCapture.default_path(repo_root, task_id, successor_execution_id)
      if (File.exist?(capture_path) || File.symlink?(capture_path)) &&
         !File.exist?(successor_path) && !File.symlink?(successor_path)
        raise ExecutionRecord::UnresolvedExecutionStateError,
              'successor capture exists without its execution record'
      end

      record_recovery = ExecutionRecord.acquire!(
        successor_path,
        task_id: task_id,
        execution_id: successor_execution_id,
        pid: pid,
        continuation_from_execution_id: predecessor_execution_id,
        continuation_checkpoint_sha256: transition.checkpoint_sha256,
        continuation_transition_sha256: transition.file_sha256(path)
      )
      record = record_recovery.execution_record
      unless record && record.schema_version == ExecutionRecord::SCHEMA_VERSION &&
             record.task_id == task_id.to_s &&
             record.execution_id == successor_execution_id.to_s &&
             record.continuation_from_execution_id == predecessor_execution_id.to_s &&
             record.continuation_checkpoint_sha256 == transition.checkpoint_sha256 &&
             record.continuation_transition_sha256 == transition.file_sha256(path)
        raise ExecutionRecord::UnresolvedExecutionStateError,
              'successor execution is not bound to the paused continuation transition'
      end

      if record_recovery.classification == ExecutionRecord::CLASSIFICATION_COMPLETED
        unless record.durable_capture_path == capture_path &&
               record.classify == ExecutionRecord::CLASSIFICATION_COMPLETED
          raise ExecutionRecord::UnresolvedExecutionStateError,
                'continuation successor terminal record or capture is ambiguous'
        end
        _capture_path, capture_bytes, = stable_input!(capture_path, 'successor terminal capture')
        capture = DurableCommandCapture.from_json(capture_bytes)
        return Recovery.new(classification: ExecutionRecord::CLASSIFICATION_COMPLETED,
                            execution_record: record, durable_capture: capture,
                            recovery_transition: transition)
      end

      unless record_recovery.classification.nil?
        raise ExecutionRecord::UnresolvedExecutionStateError,
              'a started or incomplete continuation successor cannot be relaunched'
      end
      transition.verify_inputs!(
        repo_root: repo_root,
        application_state_path: application_state_path,
        owner_authorization_path: owner_authorization_path,
        worktree_path: worktree,
        command: command
      )
      Recovery.new(classification: nil, execution_record: record, durable_capture: nil,
                   recovery_transition: transition)
    end
  rescue ExecutionRecord::ValidationError, ExecutionRecoveryTransition::ValidationError => e
    raise ExecutionRecord::UnresolvedExecutionStateError, "paused continuation is malformed: #{e.message}"
  end

  def self.expected_transition_fields(repo_root:, task_id:, predecessor_execution_id:,
                                      successor_execution_id:, application_state_path:,
                                      owner_authorization_path:, worktree_path:, command:)
    checkpoint_path, checkpoint_bytes, checkpoint_sha256 =
      stable_input!(application_state_path, 'application-state')
    checkpoint = parse_object!(checkpoint_bytes, 'application-state', CHECKPOINT_KEYS)
    validate_checkpoint!(checkpoint, task_id)

    source_path, _source_bytes, source_sha256 = stable_input!(checkpoint.fetch('source_path'), 'source')
    baseline_path, _baseline_bytes, baseline_sha256 =
      stable_input!(checkpoint.fetch('baseline_path'), 'baseline')
    unless source_sha256 == checkpoint.fetch('source_sha256') &&
           baseline_sha256 == checkpoint.fetch('baseline_sha256')
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'source or baseline bytes do not match the paused checkpoint hashes'
    end

    journal_path, journal_bytes, journal_sha256 = stable_input!(checkpoint.fetch('journal_path'), 'journal')
    unless journal_sha256 == checkpoint.fetch('journal_prefix_sha256')
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'original journal prefix SHA-256 does not match the paused checkpoint'
    end
    journal_records = validate_journal!(
      journal_bytes, checkpoint, source_sha256: source_sha256, baseline_sha256: baseline_sha256
    )

    execution_evidence = {}
    checkpoint.fetch('journal_execution_ids').each do |execution_id|
      evidence = completed_execution_evidence!(
        repo_root, task_id, execution_id,
        require_exit_status: execution_id == predecessor_execution_id.to_s ? 1 : nil
      )
      execution_evidence[execution_id] = evidence
    end

    unless checkpoint.fetch('journal_execution_ids').last == predecessor_execution_id.to_s &&
           journal_records.last.fetch('execution_id') == predecessor_execution_id.to_s
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'predecessor execution does not own the end of the original journal prefix'
    end

    authorization_path, authorization_bytes, =
      stable_input!(owner_authorization_path, 'Owner authorization')
    authorization = authorization_bytes.dup.force_encoding(Encoding::UTF_8)
    unless authorization.valid_encoding?
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'Owner authorization is not valid UTF-8 text'
    end
    worktree = ExecutionRecoveryTransition.normalized_worktree_path(worktree_path)
    command_sha256 = ExecutionRecoveryTransition.command_sha256!(command)
    target = authorization_target(
      task_id: task_id,
      predecessor_execution_id: predecessor_execution_id,
      successor_execution_id: successor_execution_id,
      checkpoint_sha256: checkpoint_sha256,
      journal_sha256: journal_sha256,
      source_sha256: source_sha256,
      baseline_sha256: baseline_sha256,
      next_attempt: checkpoint.fetch('next_expected_attempt'),
      worktree_path: worktree,
      command_sha256: command_sha256
    )
    unless TaskReconciler.conversation_authorized?(ACTION, [authorization], authorization_target: target)
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'Owner authorization does not exactly bind this paused continuation'
    end

    predecessor_evidence = execution_evidence.fetch(predecessor_execution_id.to_s)
    {
      'schema_version' => SCHEMA_VERSION,
      'task_id' => task_id.to_s,
      'predecessor_execution_id' => predecessor_execution_id.to_s,
      'predecessor_execution_sha256' => predecessor_evidence.fetch('execution_sha256'),
      'predecessor_capture_sha256' => predecessor_evidence.fetch('capture_sha256'),
      'successor_execution_id' => successor_execution_id.to_s,
      'checkpoint_path' => checkpoint_path,
      'checkpoint_sha256' => checkpoint_sha256,
      'journal_path' => journal_path,
      'journal_prefix_sha256' => journal_sha256,
      'journal_record_count' => journal_records.length,
      'journal_execution_ids' => checkpoint.fetch('journal_execution_ids'),
      'journal_execution_evidence' => execution_evidence,
      'source_path' => source_path,
      'source_sha256' => source_sha256,
      'baseline_path' => baseline_path,
      'baseline_sha256' => baseline_sha256,
      'scientific_status' => checkpoint.fetch('scientific_status'),
      'completed_attempts' => checkpoint.fetch('completed_attempts'),
      'total_attempts' => checkpoint.fetch('total_attempts'),
      'next_attempt' => checkpoint.fetch('next_expected_attempt'),
      'owner_authorization_path' => authorization_path,
      'authorization_text_sha256' => Digest::SHA256.hexdigest(authorization_bytes),
      'worktree_path' => worktree,
      'successor_command_sha256' => command_sha256,
      'status' => STATUS_RESERVED
    }
  end

  def self.validate_checkpoint!(checkpoint, task_id)
    unless checkpoint.fetch('schema_version') == SCHEMA_VERSION &&
           checkpoint.fetch('task_id') == task_id.to_s &&
           checkpoint.fetch('scientific_status') == SCIENTIFIC_PAUSED
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'only a PAUSED_RESUMABLE scientific checkpoint is eligible for continuation'
    end

    completed = checkpoint.fetch('completed_attempts')
    total = checkpoint.fetch('total_attempts')
    next_attempt = checkpoint.fetch('next_expected_attempt')
    record_count = checkpoint.fetch('journal_record_count')
    ids = checkpoint.fetch('journal_execution_ids')
    unless integer?(completed) && completed.positive? && integer?(total) &&
           total > completed && integer?(next_attempt) && next_attempt == completed + 1 &&
           next_attempt <= total && integer?(record_count) && record_count.positive? &&
           ids.is_a?(Array) && !ids.empty? &&
           ids.all? { |id| ExecutionRecord.stable_component?(id) } && ids.uniq == ids
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'paused checkpoint cursor, journal inventory, or attempt bounds are invalid'
    end
    %w[journal_prefix_sha256 source_sha256 baseline_sha256].each do |field|
      unless sha256?(checkpoint.fetch(field))
        raise ExecutionRecord::UnresolvedExecutionStateError, "checkpoint #{field} is malformed"
      end
    end
    %w[journal_path source_path baseline_path].each do |field|
      unless checkpoint.fetch(field).is_a?(String) && Pathname.new(checkpoint.fetch(field)).absolute?
        raise ExecutionRecord::UnresolvedExecutionStateError,
              "checkpoint #{field} must be an absolute file path"
      end
    end
  end

  def self.validate_journal!(bytes, checkpoint, source_sha256:, baseline_sha256:)
    unless bytes.end_with?("\n")
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'original journal prefix ends with an incomplete record'
    end
    lines = bytes.lines(chomp: true)
    unless lines.length == checkpoint.fetch('journal_record_count')
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'original journal prefix record count does not match the checkpoint'
    end

    expected_attempt = 1
    seen_execution_ids = []
    records = lines.each_with_index.map do |line, index|
      row = parse_object!(line, 'journal record', JOURNAL_RECORD_KEYS)
      sequence = index + 1
      unless row.fetch('sequence') == sequence &&
             row.fetch('status') == 'COMPLETED' &&
             row.fetch('first_attempt') == expected_attempt &&
             integer?(row.fetch('last_attempt')) &&
             row.fetch('last_attempt') >= row.fetch('first_attempt') &&
             row.fetch('last_attempt') <= checkpoint.fetch('total_attempts') &&
             row.fetch('source_sha256') == source_sha256 &&
             row.fetch('baseline_sha256') == baseline_sha256 &&
             checkpoint.fetch('journal_execution_ids').include?(row.fetch('execution_id'))
        raise ExecutionRecord::UnresolvedExecutionStateError,
              "original journal prefix is not contiguous at record #{sequence}"
      end
      seen_execution_ids << row.fetch('execution_id') unless seen_execution_ids.include?(row.fetch('execution_id'))
      expected_attempt = row.fetch('last_attempt') + 1
      row
    end
    unless seen_execution_ids == checkpoint.fetch('journal_execution_ids') &&
           records.last.fetch('last_attempt') == checkpoint.fetch('completed_attempts') &&
           expected_attempt == checkpoint.fetch('next_expected_attempt')
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'original journal prefix and next expected attempt do not match the checkpoint'
    end
    records
  end

  def self.authorization_target(task_id:, predecessor_execution_id:, successor_execution_id:,
                                checkpoint_sha256:, journal_sha256:, source_sha256:, baseline_sha256:,
                                next_attempt:, worktree_path:, command_sha256:)
    [
      "task_id=#{task_id}",
      "predecessor_execution_id=#{predecessor_execution_id}",
      "successor_execution_id=#{successor_execution_id}",
      "checkpoint_sha256=#{checkpoint_sha256}",
      "journal_prefix_sha256=#{journal_sha256}",
      "source_sha256=#{source_sha256}",
      "baseline_sha256=#{baseline_sha256}",
      "next_attempt=#{next_attempt}",
      "worktree_path=#{worktree_path}",
      "successor_command_sha256=#{command_sha256}"
    ].join('|')
  end

  def self.completed_execution_evidence!(repo_root, task_id, execution_id, require_exit_status: nil)
    unless ExecutionRecord.stable_component?(execution_id)
      raise ExecutionRecord::UnresolvedExecutionStateError, 'journal execution identity is malformed'
    end
    record_path = ExecutionRecord.default_path(repo_root, task_id, execution_id)
    _resolved_record_path, record_bytes, record_sha256 = stable_input!(record_path, 'prior execution record')
    record_data = parse_object!(record_bytes, 'prior execution record', nil)
    required_record_keys = %w[
      schema_version task_id execution_id pid status durable_capture_path started_at ended_at
    ]
    optional_record_keys = %w[
      parent_execution_id continuation_from_execution_id continuation_checkpoint_sha256
      continuation_transition_sha256
    ]
    unless (required_record_keys - record_data.keys).empty? &&
           (record_data.keys - required_record_keys - optional_record_keys).empty?
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "journal execution '#{execution_id}' has an unsupported record schema"
    end
    record = ExecutionRecord.new(record_data)
    unless record.schema_version == ExecutionRecord::SCHEMA_VERSION &&
           record.task_id == task_id.to_s && record.execution_id == execution_id &&
           record.status == ExecutionRecord::STATUS_COMPLETED &&
           record.pid.is_a?(Integer) && record.pid.positive? &&
           !record.started_at.to_s.empty? && !record.ended_at.to_s.empty?
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "journal execution '#{execution_id}' is not a completed protected execution"
    end

    expected_capture_path = DurableCommandCapture.default_path(repo_root, task_id, execution_id)
    unless record.durable_capture_path == expected_capture_path
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "journal execution '#{execution_id}' does not name its canonical terminal capture"
    end
    _capture_path, capture_bytes, capture_sha256 = stable_input!(expected_capture_path, 'terminal capture')
    capture_data = parse_object!(capture_bytes, 'terminal capture', nil)
    capture_keys = %w[schema_version command stdout stderr exit_status started_at ended_at]
    unless capture_data.keys.sort == capture_keys.sort
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "journal execution '#{execution_id}' has an unsupported capture schema"
    end
    capture = DurableCommandCapture.new(capture_data)
    unless capture.schema_version == DurableCommandCapture::SCHEMA_VERSION && capture.complete? &&
           record.classify == ExecutionRecord::CLASSIFICATION_COMPLETED
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "journal execution '#{execution_id}' has no complete terminal capture"
    end
    if !require_exit_status.nil? && capture.exit_status != require_exit_status
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "predecessor execution '#{execution_id}' did not capture the authorized pause exit"
    end
    { 'execution_sha256' => record_sha256, 'capture_sha256' => capture_sha256 }
  end

  def self.stable_input!(path, label)
    normalized = ExecutionRecoveryTransition.normalized_application_state_path(path)
    digest = ExecutionRecoveryTransition.application_state_sha256!(normalized)
    bytes = File.binread(normalized)
    unless Digest::SHA256.hexdigest(bytes) == digest
      raise ExecutionRecord::UnresolvedExecutionStateError, "#{label} changed while it was being read"
    end
    [normalized, bytes, digest]
  rescue ExecutionRecord::UnresolvedExecutionStateError => e
    raise ExecutionRecord::UnresolvedExecutionStateError, "#{label} could not be verified: #{e.message}"
  rescue StandardError => e
    raise ExecutionRecord::UnresolvedExecutionStateError, "#{label} could not be verified: #{e.message}"
  end

  def self.parse_object!(bytes, label, expected_keys)
    value = JSON.parse(bytes, object_class: UniqueJSONHash)
    unless value.is_a?(Hash) && (expected_keys.nil? || value.keys.sort == expected_keys.sort)
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "#{label} has an incomplete, unsupported, or ambiguous schema"
    end
    value
  rescue JSON::ParserError, DuplicateJSONKeyError => e
    raise ExecutionRecord::UnresolvedExecutionStateError, "#{label} is malformed: #{e.message}"
  end

  def self.integer?(value)
    value.is_a?(Integer) && ![true, false].include?(value)
  end

  def self.sha256?(value)
    value.is_a?(String) && value.match?(/\A[0-9a-f]{64}\z/)
  end

  def self.reserve!(repo_root, path, expected)
    if File.exist?(path) || File.symlink?(path)
      transition = load(path)
      transition.assert_compatible!(expected)
      return transition
    end

    successor_path = ExecutionRecord.default_path(
      repo_root, expected.fetch('task_id'), expected.fetch('successor_execution_id')
    )
    if File.exist?(successor_path) || File.symlink?(successor_path)
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'successor execution already exists without this checkpoint continuation transition'
    end

    candidate = new(expected.merge('created_at' => Time.now.utc.iso8601))
    candidate.save_if_absent!(path)
    transition = load(path)
    transition.assert_compatible!(expected)
    transition.verify_readback!(path)
    transition
  rescue Errno::EEXIST
    transition = load(path)
    transition.assert_compatible!(expected)
    transition
  end

  def self.load(path)
    _resolved_path, bytes, = stable_input!(path, 'continuation transition')
    value = JSON.parse(bytes, object_class: UniqueJSONHash)
    unless value.is_a?(Hash) && value.keys.sort == TRANSITION_KEYS.sort
      raise ExecutionRecord::UnresolvedExecutionStateError, 'continuation transition schema is ambiguous'
    end
    new(value)
  rescue JSON::ParserError, DuplicateJSONKeyError => e
    raise ExecutionRecord::UnresolvedExecutionStateError, "continuation transition is malformed: #{e.message}"
  end

  def save_if_absent!(path)
    dir = File.dirname(path)
    FileUtils.mkdir_p(dir)
    Tempfile.create(['.paused-continuation-', '.tmp'], dir) do |file|
      file.write(to_json)
      file.flush
      file.fsync
      File.link(file.path, path)
      ExecutionRecord.sync_directory!(dir)
    end
    true
  end

  def assert_compatible!(expected)
    unless TRANSITION_KEYS.reject { |key| key == 'created_at' }.all? do |key|
             to_h[key] == (expected.key?(key) ? expected[key] : nil)
           end &&
           @schema_version == SCHEMA_VERSION && @status == STATUS_RESERVED &&
           !@created_at.to_s.empty?
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'continuation transition is already bound to another checkpoint, command, or owner authority'
    end
    true
  end

  def verify_readback!(path)
    persisted = self.class.load(path)
    unless persisted.to_h == to_h
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'paused continuation transition failed read-back verification'
    end
    true
  end

  def file_sha256(path)
    _resolved, _bytes, digest = self.class.stable_input!(path, 'continuation transition')
    digest
  end

  def verify_inputs!(repo_root:, application_state_path:, owner_authorization_path:,
                     worktree_path:, command:)
    expected = self.class.expected_transition_fields(
      repo_root: repo_root,
      task_id: @task_id,
      predecessor_execution_id: @predecessor_execution_id,
      successor_execution_id: @successor_execution_id,
      application_state_path: application_state_path,
      owner_authorization_path: owner_authorization_path,
      worktree_path: worktree_path,
      command: command
    )
    assert_compatible!(expected)
    true
  end

  def child_environment
    {
      'FABLE_PAUSED_CONTINUATION_PREDECESSOR_EXECUTION_ID' => @predecessor_execution_id,
      'FABLE_PAUSED_CONTINUATION_CHECKPOINT_SHA256' => @checkpoint_sha256,
      'FABLE_PAUSED_CONTINUATION_NEXT_ATTEMPT' => @next_attempt.to_s
    }
  end

  def self.without_child_environment
    CHILD_ENV_KEYS.to_h { |key| [key, nil] }
  end
end
