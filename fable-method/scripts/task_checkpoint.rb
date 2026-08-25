#!/usr/bin/env ruby
# frozen_string_literal: true

require 'json'
require 'fileutils'
require 'open3'
require 'time'
require 'digest'

# TaskCheckpoint encapsulates the minimal, durable, authoritative continuation state
# for Fable Worker execution across sessions and model boundaries.
class TaskCheckpoint
  SCHEMA_VERSION = 1

  VALID_LIFECYCLE_STATES = %w[IN_PROGRESS BLOCKED COMPLETED ABORTED].freeze
  VALID_VERDICTS = %w[CONTINUE ALREADY_COMPLETED RECONCILE_LIVE_STATE AUTHORIZATION_REQUIRED STOP_UNRESOLVED].freeze
  QUEUE_DISPOSITION_BLOCKED_DEFERRED = 'BLOCKED_DEFERRED'
  DEFERRED_RECHECK_ACTION = 'RECHECK_DEFERRED_RESUME_GATE'
  DEFER_ELIGIBLE_BLOCKER = 'TRANSIENT_ELIGIBLE'
  MAX_DEFERRED_TASKS = 1
  MAX_AUTOMATIC_END_RECHECKS = 1
  DEFERRED_TASK_TERMINAL_STATES = %w[BLOCKED COMPLETED ABORTED].freeze
  OPTIONAL_QUEUE_FIELDS = %i[
    queue_disposition
    resume_after_task_id
    next_authorized_task_packet_ref
    deferred_resume_action
    deferred_recheck_count
  ].freeze

  REQUIRED_FIELDS = %i[
    task_id
    repository
    worktree
    authoritative_packet_ref
    branch
    current_head
    current_tree
    task_lifecycle_state
    next_action
    authorization_boundary
    updated_at
    revision
  ].freeze

  attr_accessor :schema_version,
                :task_id,
                :repository,
                :worktree,
                :authoritative_packet_ref,
                :branch,
                :current_head,
                :current_tree,
                :task_lifecycle_state,
                :current_blocker,
                :next_action,
                :authorization_boundary,
                :pr_state,
                :pr_number,
                :pr_url,
                :updated_at,
                :revision,
                :queue_disposition,
                :resume_after_task_id,
                :next_authorized_task_packet_ref,
                :deferred_resume_action,
                :deferred_recheck_count

  def initialize(attrs = {})
    @schema_version = attrs[:schema_version] || attrs['schema_version'] || SCHEMA_VERSION
    @task_id = (attrs[:task_id] || attrs['task_id'])&.to_s
    @repository = (attrs[:repository] || attrs['repository'])&.to_s
    @worktree = (attrs[:worktree] || attrs['worktree'])&.to_s
    @authoritative_packet_ref = (attrs[:authoritative_packet_ref] || attrs['authoritative_packet_ref'])&.to_s
    @branch = (attrs[:branch] || attrs['branch'] || 'master')&.to_s
    @current_head = (attrs[:current_head] || attrs['current_head'])&.to_s
    @current_tree = (attrs[:current_tree] || attrs['current_tree'])&.to_s
    @task_lifecycle_state = (attrs[:task_lifecycle_state] || attrs['task_lifecycle_state'] || 'IN_PROGRESS')&.to_s
    @current_blocker = attrs[:current_blocker] || attrs['current_blocker']
    @next_action = (attrs[:next_action] || attrs['next_action'])&.to_s
    @authorization_boundary = (attrs[:authorization_boundary] || attrs['authorization_boundary'] || 'NONE')&.to_s
    @pr_state = attrs[:pr_state] || attrs['pr_state']
    @pr_number = attrs[:pr_number] || attrs['pr_number']
    @pr_url = attrs[:pr_url] || attrs['pr_url']
    @updated_at = (attrs[:updated_at] || attrs['updated_at'] || Time.now.utc.iso8601)&.to_s
    @revision = (attrs[:revision] || attrs['revision'] || 1).to_i
    @queue_disposition = optional_value(attrs, :queue_disposition)&.to_s
    @resume_after_task_id = optional_value(attrs, :resume_after_task_id)&.to_s
    @next_authorized_task_packet_ref = optional_value(attrs, :next_authorized_task_packet_ref)&.to_s
    @deferred_resume_action = optional_value(attrs, :deferred_resume_action)&.to_s
    @deferred_recheck_count = optional_value(attrs, :deferred_recheck_count)
  end

  def validate!
    errors = []

    errors << "schema_version must be #{SCHEMA_VERSION}" unless @schema_version == SCHEMA_VERSION

    REQUIRED_FIELDS.each do |field|
      val = send(field)
      errors << "missing required field: #{field}" if val.nil? || val.to_s.strip.empty?
    end

    if @authoritative_packet_ref.to_s.strip == 'ORIGINAL_TASK_RULES_INHERITED: YES'
      errors << 'authoritative_packet_ref must specify an explicit locator/source, not just inheritance affirmation'
    end

    if @authoritative_packet_ref.to_s.strip =~ %r{\A(conversation|session|chat)://}i
      errors << 'authoritative_packet_ref must be a durable cross-agent locator (repo file path or git locator), not an ephemeral session URI'
    end

    unless VALID_LIFECYCLE_STATES.include?(@task_lifecycle_state)
      errors << "invalid task_lifecycle_state: #{@task_lifecycle_state} (must be one of #{VALID_LIFECYCLE_STATES.join(', ')})"
    end

    errors << 'revision must be a positive integer >= 1' if @revision.nil? || @revision < 1

    validate_queue_fields(errors)

    raise ValidationError, "Checkpoint validation failed: #{errors.join('; ')}" unless errors.empty?

    true
  end

  def valid?
    validate!
    true
  rescue ValidationError
    false
  end

  def to_h
    data = {
      'schema_version' => @schema_version,
      'task_id' => @task_id,
      'repository' => @repository,
      'worktree' => @worktree,
      'authoritative_packet_ref' => @authoritative_packet_ref,
      'branch' => @branch,
      'current_head' => @current_head,
      'current_tree' => @current_tree,
      'task_lifecycle_state' => @task_lifecycle_state,
      'current_blocker' => @current_blocker,
      'next_action' => @next_action,
      'authorization_boundary' => @authorization_boundary,
      'pr_state' => @pr_state,
      'pr_number' => @pr_number,
      'pr_url' => @pr_url,
      'updated_at' => @updated_at,
      'revision' => @revision
    }
    OPTIONAL_QUEUE_FIELDS.each do |field|
      value = send(field)
      data[field.to_s] = value unless value.nil?
    end
    data
  end

  def to_json(*args)
    JSON.pretty_generate(to_h, *args)
  end

  def self.from_json(json_str)
    data = JSON.parse(json_str)
    raise ValidationError, 'checkpoint JSON root must be an Object' unless data.is_a?(Hash)

    new(data)
  rescue JSON::ParserError => e
    raise ValidationError, "Malformed JSON checkpoint: #{e.message}"
  end

  def self.default_path(repo_root, task_id)
    File.join(repo_root, '.fable', 'checkpoints', "#{task_id}.json")
  end

  def self.load(file_path)
    raise LoadError, "Checkpoint file does not exist: #{file_path}" unless File.file?(file_path)

    raw = File.read(file_path, encoding: 'UTF-8')
    cp = from_json(raw)
    cp.validate!
    cp
  end

  def save(file_path, expected_revision: nil)
    validate!

    dir = File.dirname(file_path)
    FileUtils.mkdir_p(dir)

    if File.exist?(file_path)
      existing_raw = File.read(file_path, encoding: 'UTF-8')
      begin
        existing = self.class.from_json(existing_raw)
        if expected_revision
          if existing.revision != expected_revision
            raise ConcurrencyError,
                  "Revision mismatch: expected #{expected_revision}, but stored revision is #{existing.revision}"
          end
          @revision = existing.revision + 1
        elsif @revision <= existing.revision
          @revision = existing.revision + 1
        end
      rescue JSON::ParserError, ValidationError
        # Overwrite corrupt file if explicitly directed
      end
    end

    @updated_at = Time.now.utc.iso8601
    temp_path = "#{file_path}.tmp.#{Process.pid}.#{Time.now.to_i}"
    File.open(temp_path, 'w:UTF-8') { |f| f.write(to_json) }
    File.rename(temp_path, file_path)
    true
  end

  def deferred?
    @queue_disposition == QUEUE_DISPOSITION_BLOCKED_DEFERRED
  end

  def self.validate_deferred_limit!(checkpoints)
    checkpoints = Array(checkpoints)
    unless checkpoints.all? { |checkpoint| checkpoint.is_a?(TaskCheckpoint) }
      raise DeferredQueueStateError, 'deferred-task inventory must contain only TaskCheckpoint records'
    end
    checkpoints.each(&:validate!)

    deferred_count = checkpoints.count(&:deferred?)
    return true if deferred_count <= MAX_DEFERRED_TASKS

    raise DeferredQueueLimitError,
          "maximum deferred tasks exceeded: #{deferred_count} present, #{MAX_DEFERRED_TASKS} allowed"
  end

  def defer_for_authorized_task!(blocker:, blocker_disposition:, resume_after_task_id:,
                                 next_authorized_task_packet_ref:, task_b_independent:,
                                 task_b_packet_authorized:, existing_deferred_checkpoints: [])
    validate!
    raise DeferredQueueStateError, "task #{@task_id} is already deferred" if deferred?
    unless %w[IN_PROGRESS BLOCKED].include?(@task_lifecycle_state)
      raise DeferredQueueStateError, "terminal Task A state #{@task_lifecycle_state} cannot enter the deferred queue"
    end
    unless blocker_disposition.to_s == DEFER_ELIGIBLE_BLOCKER
      raise DeferredQueueEligibilityError,
            "blocker disposition must be #{DEFER_ELIGIBLE_BLOCKER}; semantic, authorization, safety, database-authority, and permanent blockers are ineligible"
    end
    unless task_b_independent == true
      raise DeferredQueueEligibilityError, 'Task B independence must be explicitly confirmed by the authoritative Packet'
    end
    unless task_b_packet_authorized == true
      raise DeferredQueueEligibilityError, 'Task B must already have an executable Owner-authorized Packet'
    end

    blocker = blocker.to_s.strip
    task_b_id = resume_after_task_id.to_s.strip
    task_b_packet_ref = next_authorized_task_packet_ref.to_s.strip
    original_next_action = @next_action.to_s.strip
    raise DeferredQueueStateError, 'deferred blocker must be specific and non-empty' if blocker.empty?
    raise DeferredQueueStateError, 'resume_after_task_id must be non-empty' if task_b_id.empty?
    raise DeferredQueueStateError, 'Task B must be independent from Task A' if task_b_id == @task_id
    if original_next_action.empty? || original_next_action == DEFERRED_RECHECK_ACTION
      raise DeferredQueueStateError, 'Task A must have an original continuation action distinct from the deferred recheck gate'
    end

    inventory = Array(existing_deferred_checkpoints)
    self.class.validate_deferred_limit!(inventory)
    if inventory.any?(&:deferred?)
      raise DeferredQueueLimitError, "maximum deferred tasks is #{MAX_DEFERRED_TASKS}; Task C chaining is prohibited"
    end

    resolve_packet_locator(task_b_packet_ref, @repository)

    @task_lifecycle_state = 'BLOCKED'
    @current_blocker = blocker
    @queue_disposition = QUEUE_DISPOSITION_BLOCKED_DEFERRED
    @resume_after_task_id = task_b_id
    @next_authorized_task_packet_ref = task_b_packet_ref
    @deferred_resume_action = original_next_action
    @deferred_recheck_count = 0
    @next_action = DEFERRED_RECHECK_ACTION
    validate!
    self
  end

  def authorized_deferred_task
    return nil unless deferred?

    {
      task_id: @resume_after_task_id,
      authoritative_packet_ref: @next_authorized_task_packet_ref
    }
  end

  def recheck_deferred_resume_gate!(completed_task:, gate_passed:)
    validate!
    raise DeferredQueueStateError, "task #{@task_id} is not BLOCKED_DEFERRED" unless deferred?
    unless completed_task.is_a?(TaskCheckpoint)
      raise DeferredQueueStateError, 'deferred resume recheck requires the exact Task B checkpoint'
    end
    completed_task.validate!
    unless completed_task.task_id == @resume_after_task_id
      raise DeferredQueueStateError,
            "deferred resume expected Task B '#{@resume_after_task_id}', received '#{completed_task.task_id}'"
    end
    unless completed_task.authoritative_packet_ref == @next_authorized_task_packet_ref
      raise DeferredQueueStateError,
            "Task B checkpoint Packet ref does not match '#{@next_authorized_task_packet_ref}'"
    end
    if completed_task.deferred?
      raise DeferredQueueLimitError, 'Task B cannot defer to Task C while Task A occupies the single deferred slot'
    end
    unless DEFERRED_TASK_TERMINAL_STATES.include?(completed_task.task_lifecycle_state)
      raise DeferredQueueStateError,
            "Task B must reach a terminal end-of-task state before recheck (received #{completed_task.task_lifecycle_state})"
    end
    unless gate_passed == true || gate_passed == false
      raise DeferredQueueStateError, 'gate_passed must be exactly true or false'
    end
    if @deferred_recheck_count >= MAX_AUTOMATIC_END_RECHECKS
      raise DeferredQueueLimitError,
            "maximum automatic end rechecks is #{MAX_AUTOMATIC_END_RECHECKS}"
    end

    @deferred_recheck_count += 1
    unless gate_passed
      validate!
      return :blocked_deferred
    end

    resumed_action = @deferred_resume_action
    @task_lifecycle_state = 'IN_PROGRESS'
    @current_blocker = nil
    @next_action = resumed_action
    clear_deferred_queue_fields
    validate!
    :resumed
  end

  def resolve_authoritative_packet(base_repo = nil)
    locator = @authoritative_packet_ref.to_s.strip
    raise ResolutionError, 'authoritative_packet_ref is empty' if locator.empty?

    if locator == 'ORIGINAL_TASK_RULES_INHERITED: YES'
      raise ResolutionError, 'authoritative_packet_ref is only an inheritance affirmation without an explicit locator'
    end

    if locator =~ %r{\A(conversation|session|chat)://}i
      raise ResolutionError, "authoritative_packet_ref uses ephemeral session URI '#{locator}' which cannot be resolved by a fresh Worker without chat memory"
    end

    resolve_packet_locator(locator, base_repo)
  end

  def resolve_packet_locator(locator, base_repo = nil)
    locator = locator.to_s.strip
    raise ResolutionError, 'packet locator is empty' if locator.empty?
    if locator == 'ORIGINAL_TASK_RULES_INHERITED: YES'
      raise ResolutionError, 'packet locator is only an inheritance affirmation without an explicit locator'
    end
    if locator =~ %r{\A(conversation|session|chat)://}i
      raise ResolutionError, "packet locator uses ephemeral session URI '#{locator}' which cannot be resolved by a fresh Worker without chat memory"
    end

    root = base_repo || @repository || Dir.pwd

    # Handle git-backed locator: git:<ref>:<path>
    if locator =~ /\Agit:([^:]+):(.+)\z/
      git_ref = Regexp.last_match(1)
      git_path = Regexp.last_match(2)
      stdout, _stderr, status = Open3.capture3('git', 'show', "#{git_ref}:#{git_path}", chdir: root)
      if status.success? && !stdout.empty?
        return { status: :resolved, source: :git, locator: locator, content: stdout }
      else
        raise ResolutionError, "Git-backed packet locator '#{locator}' could not be resolved from Git object database"
      end
    end

    # Handle file path (with optional #section-anchor)
    clean_path = locator.split('#').first
    target_path = File.expand_path(clean_path, root)

    # Also check if it was relative to worktree
    if !File.file?(target_path) && @worktree && File.directory?(@worktree)
      worktree_target = File.expand_path(clean_path, @worktree)
      target_path = worktree_target if File.file?(worktree_target)
    end

    if File.file?(target_path)
      content = File.read(target_path, encoding: 'UTF-8')
      return { status: :resolved, source: :file, path: target_path, content: content }
    end

    raise ResolutionError, "Durable packet file not found at '#{target_path}' (locator: '#{locator}')"
  end

  private

  def optional_value(attrs, field)
    return attrs[field] if attrs.key?(field)

    attrs[field.to_s]
  end

  def validate_queue_fields(errors)
    if @queue_disposition.nil?
      populated = OPTIONAL_QUEUE_FIELDS.reject { |field| field == :queue_disposition }.select do |field|
        !send(field).nil?
      end
      errors << "queue-specific fields require queue_disposition: #{populated.join(', ')}" unless populated.empty?
      return
    end

    unless @queue_disposition == QUEUE_DISPOSITION_BLOCKED_DEFERRED
      errors << "invalid queue_disposition: #{@queue_disposition} (must be #{QUEUE_DISPOSITION_BLOCKED_DEFERRED})"
      return
    end

    errors << 'BLOCKED_DEFERRED requires task_lifecycle_state BLOCKED' unless @task_lifecycle_state == 'BLOCKED'
    if @current_blocker.nil? || @current_blocker.to_s.strip.empty?
      errors << 'BLOCKED_DEFERRED requires a specific current_blocker'
    end
    unless @next_action == DEFERRED_RECHECK_ACTION
      errors << "BLOCKED_DEFERRED next_action must be #{DEFERRED_RECHECK_ACTION}"
    end
    if @resume_after_task_id.nil? || @resume_after_task_id.strip.empty?
      errors << 'BLOCKED_DEFERRED requires resume_after_task_id'
    elsif @resume_after_task_id == @task_id
      errors << 'resume_after_task_id must identify an independent Task B'
    end
    if @next_authorized_task_packet_ref.nil? || @next_authorized_task_packet_ref.strip.empty?
      errors << 'BLOCKED_DEFERRED requires next_authorized_task_packet_ref'
    elsif @next_authorized_task_packet_ref == 'ORIGINAL_TASK_RULES_INHERITED: YES'
      errors << 'next_authorized_task_packet_ref must be an explicit durable locator'
    elsif @next_authorized_task_packet_ref =~ %r{\A(conversation|session|chat)://}i
      errors << 'next_authorized_task_packet_ref must be durable, not an ephemeral session URI'
    end
    if @deferred_resume_action.nil? || @deferred_resume_action.strip.empty?
      errors << 'BLOCKED_DEFERRED requires deferred_resume_action'
    elsif @deferred_resume_action == DEFERRED_RECHECK_ACTION
      errors << 'deferred_resume_action must preserve Task A continuation separately from the recheck gate'
    end
    unless @deferred_recheck_count.is_a?(Integer) && @deferred_recheck_count.between?(0, MAX_AUTOMATIC_END_RECHECKS)
      errors << "deferred_recheck_count must be an integer from 0 to #{MAX_AUTOMATIC_END_RECHECKS}"
    end
  end

  def clear_deferred_queue_fields
    @queue_disposition = nil
    @resume_after_task_id = nil
    @next_authorized_task_packet_ref = nil
    @deferred_resume_action = nil
    @deferred_recheck_count = nil
  end

  class ValidationError < StandardError; end
  class LoadError < StandardError; end
  class ConcurrencyError < StandardError; end
  class ResolutionError < StandardError; end
  class DeferredQueueError < StandardError; end
  class DeferredQueueEligibilityError < DeferredQueueError; end
  class DeferredQueueStateError < DeferredQueueError; end
  class DeferredQueueLimitError < DeferredQueueError; end
end

# TaskReconciler performs bounded reconciliation between a durable checkpoint
# and the live repository/worktree/git/PR/authorization state.
class TaskReconciler
  attr_reader :checkpoint, :options

  ReconciliationResult = Struct.new(
    :verdict,
    :reason,
    :recommended_action,
    :checkpoint,
    :live_state,
    keyword_init: true
  ) do
    def to_h
      {
        'verdict' => verdict,
        'reason' => reason,
        'recommended_action' => recommended_action,
        'checkpoint_task_id' => checkpoint&.task_id,
        'live_state' => live_state
      }
    end

    def to_text
      <<~TEXT.strip
        RECONCILIATION_VERDICT: #{verdict}
        REASON: #{reason}
        RECOMMENDED_ACTION: #{recommended_action}
        TASK_ID: #{checkpoint&.task_id}
      TEXT
    end
  end

  def initialize(checkpoint, options = {})
    @checkpoint = checkpoint
    @options = options
  end

  def reconcile
    live = capture_live_state

    # Guard 1: Repository Identity
    if live[:repository] && File.expand_path(live[:repository]) != File.expand_path(checkpoint.repository)
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Repository identity mismatch: checkpoint expects '#{checkpoint.repository}' but live is '#{live[:repository]}'",
        recommended_action: 'Switch to expected repository or verify checkpoint path',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    # Guard 2: Worktree existence & access
    if live[:worktree] && !File.directory?(live[:worktree])
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Worktree directory does not exist or is not accessible: '#{live[:worktree]}'",
        recommended_action: 'Re-create worktree or verify checkout path',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    # Guard 2b: Authoritative Packet Resolution
    begin
      checkpoint.resolve_authoritative_packet(live[:repository])
    rescue TaskCheckpoint::ResolutionError => e
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Authoritative packet ref cannot be resolved across sessions: #{e.message}",
        recommended_action: 'Specify a durable repo-relative or git-backed authoritative packet locator',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    if checkpoint.deferred?
      begin
        checkpoint.resolve_packet_locator(checkpoint.next_authorized_task_packet_ref, live[:repository])
      rescue TaskCheckpoint::ResolutionError => e
        return ReconciliationResult.new(
          verdict: 'STOP_UNRESOLVED',
          reason: "Deferred Task B packet ref cannot be resolved across sessions: #{e.message}",
          recommended_action: 'Provide the existing executable Owner-authorized Task B Packet through a durable locator',
          checkpoint: checkpoint,
          live_state: live
        )
      end
    end

    # Guard 3: PR / Task Terminal Status
    if checkpoint.task_lifecycle_state == 'COMPLETED'
      return ReconciliationResult.new(
        verdict: 'ALREADY_COMPLETED',
        reason: "Task #{checkpoint.task_id} is already in terminal state COMPLETED",
        recommended_action: 'Close task lifecycle or archive checkpoint',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    if live[:pr_state] == 'MERGED' || checkpoint.pr_state == 'MERGED'
      return ReconciliationResult.new(
        verdict: 'ALREADY_COMPLETED',
        reason: "Associated PR (#{live[:pr_number] || checkpoint.pr_number || 'remote'}) has already been merged into target branch",
        recommended_action: 'Close PR lifecycle, verify postmerge status, and archive checkpoint',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    # Guard 4: Standalone Authorization Boundary Check
    if action_requires_standalone_auth?(checkpoint.next_action, checkpoint.authorization_boundary)
      conversation_auths = options[:conversation_authorizations] || []
      unless conversation_authorized?(checkpoint.next_action, conversation_auths)
        return ReconciliationResult.new(
          verdict: 'AUTHORIZATION_REQUIRED',
          reason: "Next action '#{checkpoint.next_action}' requires standalone Owner authorization in the current conversation; quoted tokens in checkpoint do not transfer",
          recommended_action: "Request standalone Owner authorization for '#{checkpoint.next_action}' in the current conversation before proceeding",
          checkpoint: checkpoint,
          live_state: live
        )
      end
    end

    # Guard 5: Live Git Tree & HEAD Comparison (Root-Cause-First)
    head_matches = (live[:head] == checkpoint.current_head) || checkpoint.current_head == 'UNCOMMITTED'
    tree_matches = (live[:tree] == checkpoint.current_tree) || checkpoint.current_tree == 'UNCOMMITTED'

    if head_matches && tree_matches
      # Tree matches checkpoint state exactly
      return reconcile_deferred_checkpoint(live) if checkpoint.deferred?

      if checkpoint.current_blocker && !checkpoint.current_blocker.strip.empty?
        return ReconciliationResult.new(
          verdict: 'CONTINUE',
          reason: "Resuming interrupted root-cause investigation: #{checkpoint.current_blocker}",
          recommended_action: checkpoint.next_action,
          checkpoint: checkpoint,
          live_state: live
        )
      else
        return ReconciliationResult.new(
          verdict: 'CONTINUE',
          reason: 'Live repository and worktree state match checkpoint cleanly',
          recommended_action: checkpoint.next_action,
          checkpoint: checkpoint,
          live_state: live
        )
      end
    end

    # Live HEAD/tree differed from checkpoint: determine compatible advancement vs conflict
    compatible_status = evaluate_git_advancement(live)

    case compatible_status[:status]
    when :compatible_advancement
      ReconciliationResult.new(
        verdict: 'RECONCILE_LIVE_STATE',
        reason: "Live state advanced compatibly from #{checkpoint.current_head[0..7]} to #{live[:head][0..7]} without semantic conflict",
        recommended_action: "Update checkpoint current_head/current_tree to live values and continue with '#{checkpoint.next_action}'",
        checkpoint: checkpoint,
        live_state: live
      )
    when :already_completed
      ReconciliationResult.new(
        verdict: 'ALREADY_COMPLETED',
        reason: compatible_status[:reason] || 'Load-bearing change already merged or committed in live state',
        recommended_action: 'Verify acceptance on current HEAD and finalize task',
        checkpoint: checkpoint,
        live_state: live
      )
    when :material_conflict
      ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Material conflict detected between checkpoint state and live tree: #{compatible_status[:reason]}",
        recommended_action: 'Resolve file/tree conflicts or request Planner Delta before resuming execution',
        checkpoint: checkpoint,
        live_state: live
      )
    else
      ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Unreconciled difference between checkpoint (#{checkpoint.current_head[0..7]}) and live (#{live[:head]&.[](0..7)}): #{compatible_status[:reason]}",
        recommended_action: 'Inspect git tree diff and resolve divergence before continuing',
        checkpoint: checkpoint,
        live_state: live
      )
    end
  end

  private

  def reconcile_deferred_checkpoint(live)
    if checkpoint.deferred_recheck_count >= TaskCheckpoint::MAX_AUTOMATIC_END_RECHECKS
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: 'Task A remains BLOCKED_DEFERRED after its single automatic end-of-task recheck',
        recommended_action: 'Retain the durable checkpoint and request a new Owner/Planner decision; do not recheck again automatically',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    task_b = options[:resume_after_task_checkpoint]
    if task_b && !task_b.is_a?(TaskCheckpoint)
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: 'Deferred Task B evidence is not a TaskCheckpoint record',
        recommended_action: 'Load the exact durable Task B checkpoint before rechecking Task A',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    if task_b && task_b.task_id != checkpoint.resume_after_task_id
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Deferred queue expected Task B '#{checkpoint.resume_after_task_id}', received '#{task_b.task_id}'",
        recommended_action: 'Load only the exact Task B named by Task A checkpoint',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    if task_b && task_b.authoritative_packet_ref != checkpoint.next_authorized_task_packet_ref
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Deferred Task B checkpoint Packet ref does not match '#{checkpoint.next_authorized_task_packet_ref}'",
        recommended_action: 'Load the exact Task B checkpoint rooted in the Packet named by Task A',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    if task_b&.deferred?
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: 'Task B attempted to defer to Task C while Task A occupies the single deferred slot',
        recommended_action: 'Stop Task B chaining; retain Task A as the only deferred task',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    task_b_state = task_b&.task_lifecycle_state
    if task_b_state.nil?
      return ReconciliationResult.new(
        verdict: 'CONTINUE',
        reason: "Task A is durably BLOCKED_DEFERRED pending the exact authorized independent Task B '#{checkpoint.resume_after_task_id}'",
        recommended_action: deferred_task_execution_action,
        checkpoint: checkpoint,
        live_state: live
      )
    end

    unless TaskCheckpoint::VALID_LIFECYCLE_STATES.include?(task_b_state)
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Deferred Task B lifecycle state is invalid: #{task_b_state}",
        recommended_action: 'Reconcile the exact Task B checkpoint before continuing',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    unless TaskCheckpoint::DEFERRED_TASK_TERMINAL_STATES.include?(task_b_state)
      return ReconciliationResult.new(
        verdict: 'CONTINUE',
        reason: "Authorized Task B '#{checkpoint.resume_after_task_id}' has not reached an end-of-task state (#{task_b_state})",
        recommended_action: deferred_task_execution_action,
        checkpoint: checkpoint,
        live_state: live
      )
    end

    ReconciliationResult.new(
      verdict: 'CONTINUE',
      reason: "Authorized Task B '#{checkpoint.resume_after_task_id}' reached #{task_b_state}; Task C chaining is prohibited and Task A has one recheck available",
      recommended_action: TaskCheckpoint::DEFERRED_RECHECK_ACTION,
      checkpoint: checkpoint,
      live_state: live
    )
  end

  def deferred_task_execution_action
    "EXECUTE_AUTHORIZED_DEFERRED_TASK task_id=#{checkpoint.resume_after_task_id} packet_ref=#{checkpoint.next_authorized_task_packet_ref}"
  end

  def capture_live_state
    {
      repository: options[:repository] || detect_git_repo_root,
      worktree: options[:worktree] || checkpoint.worktree,
      head: options[:head] || detect_git_head,
      tree: options[:tree] || detect_git_tree,
      branch: options[:branch] || detect_git_branch,
      pr_state: options[:pr_state] || checkpoint.pr_state,
      pr_number: options[:pr_number] || checkpoint.pr_number,
      has_conflict: options[:has_conflict] || false,
      compatible_advancement: options[:compatible_advancement]
    }
  end

  def detect_git_repo_root
    stdout, _stderr, status = Open3.capture3('git', 'rev-parse', '--show-toplevel', chdir: checkpoint.worktree)
    status.success? ? stdout.strip : checkpoint.repository
  rescue StandardError
    checkpoint.repository
  end

  def detect_git_head
    stdout, _stderr, status = Open3.capture3('git', 'rev-parse', 'HEAD', chdir: checkpoint.worktree)
    status.success? ? stdout.strip : 'UNKNOWN'
  rescue StandardError
    'UNKNOWN'
  end

  def detect_git_tree
    stdout, _stderr, status = Open3.capture3('git', 'rev-parse', 'HEAD^{tree}', chdir: checkpoint.worktree)
    status.success? ? stdout.strip : 'UNKNOWN'
  rescue StandardError
    'UNKNOWN'
  end

  def detect_git_branch
    stdout, _stderr, status = Open3.capture3('git', 'rev-parse', '--abbrev-ref', 'HEAD', chdir: checkpoint.worktree)
    status.success? ? stdout.strip : 'master'
  rescue StandardError
    'master'
  end

  def action_requires_standalone_auth?(action, boundary)
    return true if boundary =~ /CURRENT_WORKER_CONVERSATION_STANDALONE_AUTH_REQUIRED/i
    return true if boundary =~ /STANDALONE_AUTH/i

    high_risk_actions = %w[MERGE_PR PUSH PUSH_BRANCH DEPLOY DESTRUCTIVE_CLEANUP ACTIVATE_LIVE]
    high_risk_actions.any? { |hra| action.to_s.upcase.include?(hra) }
  end

  def conversation_authorized?(action, auth_tokens)
    return false if auth_tokens.nil? || auth_tokens.empty?

    auth_tokens.any? do |token|
      token.to_s.strip.length > 3 && (token.to_s.upcase.include?('AUTHORIZED') || token.to_s.upcase.include?(action.to_s.upcase))
    end
  end

  def evaluate_git_advancement(live)
    return { status: :material_conflict, reason: 'Explicit merge conflict or conflicting modifications detected' } if live[:has_conflict]

    if live[:compatible_advancement] == true
      return { status: :compatible_advancement }
    elsif live[:compatible_advancement] == false
      return { status: :material_conflict, reason: 'Conflicting task-owned changes detected in tree diff' }
    end

    # Check git ancestry if live worktree is accessible
    if File.directory?(checkpoint.worktree) && live[:head] != 'UNKNOWN' && checkpoint.current_head != 'UNCOMMITTED'
      # Check if checkpoint HEAD is an ancestor of live HEAD
      _stdout, _stderr, status = Open3.capture3('git', 'merge-base', '--is-ancestor', checkpoint.current_head, live[:head], chdir: checkpoint.worktree)
      if status.success?
        # Check if the diff between checkpoint HEAD and live HEAD touches forbidden or conflicting files
        diff_stdout, _diff_err, diff_status = Open3.capture3('git', 'diff', '--name-only', checkpoint.current_head, live[:head], chdir: checkpoint.worktree)
        if diff_status.success?
          # If changes exist, check if there are merge conflicts
          conflict_stdout, _conflict_err, conflict_status = Open3.capture3('git', 'status', '--porcelain', chdir: checkpoint.worktree)
          if conflict_status.success? && conflict_stdout.include?('UU ')
            return { status: :material_conflict, reason: 'Git unmerged path conflict present' }
          end
          return { status: :compatible_advancement }
        end
      end
    end

    { status: :unresolved_divergence, reason: 'HEAD/tree mismatch without verifiable ancestor lineage' }
  rescue StandardError => e
    { status: :material_conflict, reason: "Git evaluation error: #{e.message}" }
  end
end

# CLI interface when executed directly
if __FILE__ == $PROGRAM_NAME
  require 'optparse'

  options = {}
  mode = :reconcile

  parser = OptionParser.new do |opts|
    opts.banner = 'Usage: task_checkpoint.rb [options] <checkpoint_file_or_task_id>'

    opts.on('--reconcile', 'Reconcile live state against checkpoint (default)') { mode = :reconcile }
    opts.on('--show', 'Display checkpoint contents') { mode = :show }
    opts.on('--save', 'Save/update checkpoint') { mode = :save }

    opts.on('--repo PATH', 'Override live repository path') { |v| options[:repository] = v }
    opts.on('--worktree PATH', 'Override live worktree path') { |v| options[:worktree] = v }
    opts.on('--head SHA', 'Override live git HEAD SHA') { |v| options[:head] = v }
    opts.on('--tree SHA', 'Override live git tree SHA') { |v| options[:tree] = v }
    opts.on('--branch NAME', 'Override live git branch') { |v| options[:branch] = v }
    opts.on('--pr-state STATE', 'Override live PR state') { |v| options[:pr_state] = v }
    opts.on('--resume-after-task-checkpoint PATH', 'Load the exact Task B checkpoint for deferred resume reconciliation') do |v|
      options[:resume_after_task_checkpoint_path] = v
    end
    opts.on('--auth TOKEN', 'Provide direct conversation authorization token') do |v|
      (options[:conversation_authorizations] ||= []) << v
    end
  end

  parser.parse!

  target = ARGV.first
  if target.nil? || target.empty?
    warn parser
    exit 2
  end

  checkpoint_path = if File.file?(target)
                      target
                    else
                      # Look for task_id in .fable/checkpoints
                      TaskCheckpoint.default_path(Dir.pwd, target)
                    end

  case mode
  when :show
    begin
      cp = TaskCheckpoint.load(checkpoint_path)
      puts cp.to_json
      exit 0
    rescue StandardError => e
      warn "ERROR: #{e.message}"
      exit 1
    end

  when :reconcile
    begin
      cp = TaskCheckpoint.load(checkpoint_path)
      if options[:resume_after_task_checkpoint_path]
        options[:resume_after_task_checkpoint] = TaskCheckpoint.load(options.delete(:resume_after_task_checkpoint_path))
      end
      reconciler = TaskReconciler.new(cp, options)
      result = reconciler.reconcile
      puts result.to_text
      exit(result.verdict == 'STOP_UNRESOLVED' ? 1 : 0)
    rescue TaskCheckpoint::ValidationError, TaskCheckpoint::LoadError => e
      puts "RECONCILIATION_VERDICT: STOP_UNRESOLVED\nREASON: Malformed or unreadable checkpoint: #{e.message}\nRECOMMENDED_ACTION: Inspect or regenerate checkpoint"
      exit 1
    end
  end
end
