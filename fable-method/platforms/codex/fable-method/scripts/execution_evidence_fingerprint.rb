# frozen_string_literal: true

require 'digest'
require 'json'
require 'open3'
require 'pathname'
require 'time'

# Builds a pre-run fingerprint at the point where a command's result path,
# validation, argv, and receipt are bound together. The exact canonical JSON
# written to disk is the source of its SHA-256 digest.
module ExecutionEvidenceFingerprint
  class Error < StandardError; end

  RESULT_PATH_OPTION = '--result-path'.freeze
  FINGERPRINT_SUFFIX = '.execution-evidence.json'.freeze

  Plan = Struct.new(
    :result_path,
    :validated_result_path,
    :argv_result_path,
    :execution_argv,
    :fingerprint_payload_path,
    :fingerprint_payload,
    :fingerprint_sha256,
    :observation,
    :receipt,
    keyword_init: true
  )

  module_function

  def prepare(repository_path:, worktree_path:, hashed_paths:, result_path_generator:,
              result_path_validator:, execution_argv:, observed_at: Time.now.utc.iso8601(6))
    repository = canonical_directory!(repository_path, 'repository_path')
    worktree = canonical_directory!(worktree_path, 'worktree_path')
    ensure_git_root!(worktree)
    validate_observed_at!(observed_at)

    base_argv = normalize_argv!(execution_argv)
    if base_argv.any? { |argument| argument == RESULT_PATH_OPTION || argument.start_with?("#{RESULT_PATH_OPTION}=") }
      raise Error, "execution_argv reserves #{RESULT_PATH_OPTION} for the generated result path"
    end
    raise Error, 'result_path_generator must respond to call' unless result_path_generator.respond_to?(:call)
    raise Error, 'result_path_validator must respond to call' unless result_path_validator.respond_to?(:call)

    generated_path = result_path_generator.call
    raise Error, 'generated result path is missing' if generated_path.nil? || generated_path.to_s.empty?

    expanded_path = File.expand_path(generated_path.to_s)
    result_parent = File.realpath(File.dirname(expanded_path))
    result_path = File.join(result_parent, File.basename(expanded_path)).freeze
    validate_result_path!(result_path)
    validation_result = result_path_validator.call(result_path)
    unless validation_result == true || validation_result.equal?(result_path)
      raise Error, 'result path validation did not accept the generated result path'
    end

    normalized_inputs = normalize_hashed_paths!(worktree, hashed_paths)
    before = git_state(worktree)
    first_inputs = capture_hashed_inputs(worktree, normalized_inputs, before.fetch('status'))
    after = git_state(worktree)
    second_inputs = capture_hashed_inputs(worktree, normalized_inputs, after.fetch('status'))
    unless before == after && first_inputs == second_inputs
      raise Error, 'Git state or a hashed input changed while the fingerprint was captured'
    end

    observation = {
      'schema_version' => 1,
      'observed_at' => observed_at.to_s,
      'source_identity' => {
        'repository_path' => repository,
        'worktree_path' => worktree
      },
      'branch' => before.fetch('branch'),
      'head' => before.fetch('head'),
      'tree' => before.fetch('tree'),
      'status' => before.fetch('status'),
      'hashed_inputs' => first_inputs,
      'result_path' => result_path
    }

    payload = canonical_json(observation).freeze
    payload_path = "#{result_path}#{FINGERPRINT_SUFFIX}".freeze
    write_payload_exclusively!(payload_path, payload)
    saved_payload = File.binread(payload_path).freeze
    raise Error, 'saved fingerprint payload differs from the canonical source' unless saved_payload == payload

    digest = digest_saved_payload(payload_path).freeze
    argv_path = result_path
    command = (base_argv + [RESULT_PATH_OPTION, argv_path]).freeze
    receipt = {
      'result_path' => result_path,
      'validated_result_path' => result_path,
      'execution_argv' => command,
      'fingerprint' => {
        'payload_path' => payload_path,
        'canonical_payload' => saved_payload,
        'sha256' => digest
      }.freeze
    }.freeze

    Plan.new(
      result_path: result_path,
      validated_result_path: result_path,
      argv_result_path: argv_path,
      execution_argv: command,
      fingerprint_payload_path: payload_path,
      fingerprint_payload: saved_payload,
      fingerprint_sha256: digest,
      observation: deep_freeze(JSON.parse(saved_payload)),
      receipt: receipt
    ).freeze
  rescue JSON::ParserError, SystemCallError, ArgumentError, TypeError => e
    raise Error, "cannot produce execution fingerprint: #{e.message}"
  end

  # Rebuilds the plan for an already-owned execution without replacing its
  # fingerprint payload. This is only for replaying an existing execution;
  # callers must establish that its execution record already exists.
  def restore(repository_path:, worktree_path:, hashed_paths:, result_path:, result_path_validator:,
              execution_argv:)
    repository = canonical_directory!(repository_path, 'repository_path')
    worktree = canonical_directory!(worktree_path, 'worktree_path')
    ensure_git_root!(worktree)

    base_argv = normalize_argv!(execution_argv)
    if base_argv.any? { |argument| argument == RESULT_PATH_OPTION || argument.start_with?("#{RESULT_PATH_OPTION}=") }
      raise Error, "execution_argv reserves #{RESULT_PATH_OPTION} for the generated result path"
    end
    raise Error, 'result_path_validator must respond to call' unless result_path_validator.respond_to?(:call)
    raise Error, 'generated result path is missing' if result_path.nil? || result_path.to_s.empty?

    expanded_path = File.expand_path(result_path.to_s)
    raise Error, 'generated result path must be absolute' unless Pathname.new(result_path.to_s).absolute?
    result_parent = File.realpath(File.dirname(expanded_path))
    canonical_result_path = File.join(result_parent, File.basename(expanded_path)).freeze
    raise Error, 'generated result path is a symlink' if File.symlink?(canonical_result_path)
    validation_result = result_path_validator.call(canonical_result_path)
    unless validation_result == true || validation_result.equal?(canonical_result_path)
      raise Error, 'result path validation did not accept the generated result path'
    end

    normalized_inputs = normalize_hashed_paths!(worktree, hashed_paths)
    payload_path = "#{canonical_result_path}#{FINGERPRINT_SUFFIX}".freeze
    saved_payload = File.binread(payload_path).freeze
    digest = digest_saved_payload(payload_path).freeze
    observation = JSON.parse(saved_payload)
    source_identity = observation['source_identity'] if observation.is_a?(Hash)
    saved_inputs = observation['hashed_inputs'] if observation.is_a?(Hash)
    unless observation['schema_version'] == 1 && observation['result_path'] == canonical_result_path &&
           source_identity.is_a?(Hash) && source_identity['repository_path'] == repository &&
           source_identity['worktree_path'] == worktree && saved_inputs.is_a?(Array) &&
           saved_inputs.map { |entry| entry.is_a?(Hash) ? entry['path'] : nil } == normalized_inputs
      raise Error, 'saved fingerprint payload does not match the requested execution identity'
    end

    command = (base_argv + [RESULT_PATH_OPTION, canonical_result_path]).freeze
    receipt = {
      'result_path' => canonical_result_path,
      'validated_result_path' => canonical_result_path,
      'execution_argv' => command,
      'fingerprint' => {
        'payload_path' => payload_path,
        'canonical_payload' => saved_payload,
        'sha256' => digest
      }.freeze
    }.freeze

    Plan.new(
      result_path: canonical_result_path,
      validated_result_path: canonical_result_path,
      argv_result_path: canonical_result_path,
      execution_argv: command,
      fingerprint_payload_path: payload_path,
      fingerprint_payload: saved_payload,
      fingerprint_sha256: digest,
      observation: deep_freeze(observation),
      receipt: receipt
    ).freeze
  rescue JSON::ParserError, SystemCallError, ArgumentError, TypeError => e
    raise Error, "cannot restore execution fingerprint: #{e.message}"
  end

  # Rechecks the captured Git state and each declared input immediately before
  # the producer launches its child. The fingerprint payload itself is the
  # only generated status entry excluded from this comparison.
  def verify_current!(plan, worktree_path:, generated_status_paths: [])
    raise Error, 'execution fingerprint plan is missing' unless plan.is_a?(Plan)

    worktree = canonical_directory!(worktree_path, 'worktree_path')
    ensure_git_root!(worktree)
    raise Error, 'generated result path appeared before execution' if
      File.exist?(plan.result_path) || File.symlink?(plan.result_path)

    input_paths = plan.observation.fetch('hashed_inputs').map { |entry| entry.fetch('path') }
    first_state = git_state(worktree)
    first_state['status'] = status_without_generated(first_state.fetch('status'), worktree,
                                                      [plan.fingerprint_payload_path, *Array(generated_status_paths)])
    first_inputs = capture_hashed_inputs(worktree, input_paths, first_state.fetch('status'))
    second_state = git_state(worktree)
    second_state['status'] = status_without_generated(second_state.fetch('status'), worktree,
                                                       [plan.fingerprint_payload_path, *Array(generated_status_paths)])
    second_inputs = capture_hashed_inputs(worktree, input_paths, second_state.fetch('status'))
    unless first_state == second_state && first_inputs == second_inputs
      raise Error, 'Git state or a hashed input changed during pre-launch verification'
    end

    observation = plan.observation
    expected_state = observation.slice('branch', 'head', 'tree', 'status')
    unless first_state == expected_state && first_inputs == observation.fetch('hashed_inputs')
      raise Error, 'Git state or a hashed input changed after the execution fingerprint was captured'
    end
    unless digest_saved_payload(plan.fingerprint_payload_path) == plan.fingerprint_sha256
      raise Error, 'saved execution fingerprint digest changed before launch'
    end

    true
  rescue JSON::ParserError, SystemCallError, ArgumentError, TypeError, KeyError => e
    raise Error, "cannot verify execution fingerprint before launch: #{e.message}"
  end

  def canonical_json(value)
    JSON.generate(canonicalize(value))
  rescue JSON::GeneratorError, EncodingError, TypeError => e
    raise Error, "cannot serialize canonical execution fingerprint: #{e.message}"
  end

  def digest_saved_payload(payload_path)
    raise Error, 'fingerprint payload path is a symlink' if File.symlink?(payload_path)
    raise Error, 'fingerprint payload is missing or not a file' unless File.file?(payload_path)

    bytes = File.binread(payload_path)
    parsed = JSON.parse(bytes)
    raise Error, 'saved fingerprint payload is not canonical JSON' unless canonical_json(parsed) == bytes

    Digest::SHA256.hexdigest(bytes)
  rescue JSON::ParserError => e
    raise Error, "saved fingerprint payload is malformed: #{e.message}"
  end

  def canonicalize(value)
    case value
    when Hash
      unless value.keys.all? { |key| key.is_a?(String) }
        raise Error, 'canonical fingerprint object keys must be strings'
      end
      value.keys.sort.each_with_object({}) do |key, sorted|
        sorted[key] = canonicalize(value.fetch(key))
      end
    when Array
      value.map { |item| canonicalize(item) }
    when String
      raise Error, 'canonical fingerprint contains invalid UTF-8' unless value.dup.force_encoding(Encoding::UTF_8).valid_encoding?

      value
    when Integer, Float, TrueClass, FalseClass, NilClass
      value
    else
      raise Error, "unsupported canonical fingerprint value: #{value.class}"
    end
  end
  private_class_method :canonicalize

  def deep_freeze(value)
    case value
    when Hash
      value.each { |key, item| key.freeze; deep_freeze(item) }
    when Array
      value.each { |item| deep_freeze(item) }
    when String
      value.freeze
    end
    value.freeze
  end
  private_class_method :deep_freeze

  def canonical_directory!(path, label)
    raise Error, "#{label} is missing" if path.nil? || path.to_s.empty?
    raise Error, "#{label} is not a directory" unless File.directory?(path)

    File.realpath(path)
  end
  private_class_method :canonical_directory!

  def ensure_git_root!(worktree)
    top_level = git_text!(worktree, 'rev-parse', '--show-toplevel')
    top_level = File.realpath(top_level)
    raise Error, 'worktree_path must name the Git worktree root' unless top_level == worktree
  end
  private_class_method :ensure_git_root!

  def validate_observed_at!(value)
    raise Error, 'observed_at is missing' if value.nil? || value.to_s.empty?

    Time.iso8601(value.to_s)
  rescue ArgumentError
    raise Error, 'observed_at must be an ISO-8601 timestamp'
  end
  private_class_method :validate_observed_at!

  def normalize_argv!(argv)
    arguments = Array(argv).map(&:to_s)
    raise Error, 'execution_argv must be a non-empty argv array' if arguments.empty? || arguments.any?(&:empty?)

    arguments.map { |argument| argument.dup.freeze }
  end
  private_class_method :normalize_argv!

  def validate_result_path!(path)
    raise Error, 'generated result path must be absolute' unless Pathname.new(path).absolute?
    raise Error, 'generated result path parent must already exist' unless File.directory?(File.dirname(path))
    raise Error, 'generated result path already exists or is a symlink' if File.exist?(path) || File.symlink?(path)
  end
  private_class_method :validate_result_path!

  def normalize_hashed_paths!(worktree, paths)
    values = Array(paths)
    raise Error, 'hashed_paths must contain at least one required input' if values.empty?

    normalized = values.map do |value|
      raise Error, 'hashed input path is missing' if value.nil? || value.to_s.empty?

      absolute = File.expand_path(value.to_s, worktree)
      real = File.realpath(absolute)
      unless real.start_with?("#{worktree}/") && File.file?(real) && !File.symlink?(absolute)
        raise Error, "hashed input is not a regular file inside the worktree: #{value}"
      end

      real.delete_prefix("#{worktree}/")
    rescue Errno::ENOENT
      raise Error, "required hashed input is missing: #{value}"
    end
    raise Error, 'hashed_paths contains duplicate inputs' unless normalized.uniq.length == normalized.length

    normalized.sort.freeze
  end
  private_class_method :normalize_hashed_paths!

  def git_state(worktree)
    branch = git_text!(worktree, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    head = git_text!(worktree, 'rev-parse', '--verify', 'HEAD')
    tree = git_text!(worktree, 'rev-parse', '--verify', 'HEAD^{tree}')
    unless head.match?(/\A[0-9a-f]{40,64}\z/) && tree.match?(/\A[0-9a-f]{40,64}\z/)
      raise Error, 'Git HEAD or tree identity is missing or malformed'
    end

    raw_status = git_bytes!(worktree, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    status = parse_status(raw_status)
    {
      'branch' => branch,
      'head' => head,
      'tree' => tree,
      'status' => status
    }
  end
  private_class_method :git_state

  def parse_status(raw_status)
    text = raw_status.dup.force_encoding(Encoding::UTF_8)
    raise Error, 'Git status contains invalid UTF-8 paths' unless text.valid_encoding?

    records = text.split("\0", -1)
    records.pop if records.last == ''
    entries = []
    index = 0
    while index < records.length
      record = records.fetch(index)
      index += 1
      unless record.length >= 3 && record[2] == ' '
        raise Error, 'Git status contains a malformed porcelain entry'
      end

      code = record[0, 2]
      path = record[3..]
      raise Error, 'Git status contains an empty path' if path.nil? || path.empty?

      entry = { 'path' => path, 'status' => code }
      if code.include?('R') || code.include?('C')
        source_path = records.fetch(index) { raise Error, 'Git rename status is missing its source path' }
        index += 1
        entry['source_path'] = source_path
      end
      entries << entry
    end
    entries.sort_by { |entry| [entry.fetch('path'), entry.fetch('status'), entry['source_path'].to_s] }
  end
  private_class_method :parse_status

  def capture_hashed_inputs(worktree, paths, status_entries)
    status_by_path = {}
    status_entries.each do |entry|
      status_by_path[entry.fetch('path')] = entry.fetch('status')
      status_by_path[entry.fetch('source_path')] = entry.fetch('status') if entry['source_path']
    end

    paths.map do |relative_path|
      absolute = File.join(worktree, relative_path)
      raise Error, "hashed input became a symlink: #{relative_path}" if File.symlink?(absolute)
      raise Error, "required hashed input disappeared: #{relative_path}" unless File.file?(absolute)

      status = status_by_path[relative_path] || clean_or_ignored_status(worktree, relative_path)
      stat_before = File.stat(absolute)
      mode = (stat_before.mode & 0o111).zero? ? '100644' : '100755'
      sha256 = Digest::SHA256.file(absolute).hexdigest
      stat_after = File.stat(absolute)
      unless [stat_before.dev, stat_before.ino, stat_before.size, stat_before.mtime, stat_before.mode] ==
             [stat_after.dev, stat_after.ino, stat_after.size, stat_after.mtime, stat_after.mode]
        raise Error, "hashed input changed while it was read: #{relative_path}"
      end

      {
        'path' => relative_path,
        'status' => status,
        'mode' => mode,
        'sha256' => sha256
      }
    rescue SystemCallError => e
      raise Error, "cannot hash required input #{relative_path}: #{e.message}"
    end.freeze
  end
  private_class_method :capture_hashed_inputs

  def clean_or_ignored_status(worktree, relative_path)
    literal_path = ":(literal)#{relative_path}"
    _stdout, _stderr, tracked = Open3.capture3(
      'git', '-C', worktree, 'ls-files', '--error-unmatch', '--', literal_path, err: File::NULL
    )
    return '  ' if tracked.success?

    _stdout, _stderr, ignored = Open3.capture3(
      'git', '-C', worktree, 'check-ignore', '--quiet', '--no-index', '--', literal_path, err: File::NULL
    )
    return '!!' if ignored.success?

    raise Error, "required hashed input is absent from Git status: #{relative_path}"
  end
  private_class_method :clean_or_ignored_status

  def git_text!(worktree, *arguments)
    output = git_bytes!(worktree, *arguments)
    text = output.dup.force_encoding(Encoding::UTF_8)
    raise Error, "Git #{arguments.first} returned invalid UTF-8" unless text.valid_encoding?

    value = text.strip
    raise Error, "Git #{arguments.first} returned an empty required value" if value.empty?

    value
  end
  private_class_method :git_text!

  def git_bytes!(worktree, *arguments)
    stdout, stderr, status = Open3.capture3('git', '-C', worktree, *arguments, err: File::NULL)
    raise Error, "Git #{arguments.first} failed#{stderr.empty? ? '' : ": #{stderr.strip}"}" unless status.success?

    stdout
  rescue SystemCallError => e
    raise Error, "cannot run Git #{arguments.first}: #{e.message}"
  end
  private_class_method :git_bytes!

  def write_payload_exclusively!(path, payload)
    flags = File::WRONLY | File::CREAT | File::EXCL
    File.open(path, flags, 0o600) do |file|
      file.binmode
      file.write(payload)
      file.flush
      file.fsync
    end
  rescue Errno::EEXIST
    raise Error, "fingerprint payload already exists: #{path}"
  end
  private_class_method :write_payload_exclusively!

  def status_without_generated(status, worktree, paths)
    Array(paths).reduce(status) do |current_status, path|
      expanded = File.expand_path(path.to_s)
      next current_status unless expanded.start_with?("#{worktree}/")

      relative_path = expanded.delete_prefix("#{worktree}/")
      entry = current_status.find { |candidate| candidate.fetch('path') == relative_path }
      next current_status unless entry
      raise Error, "generated file has an unexpected Git status: #{relative_path}" unless entry.fetch('status') == '??'

      current_status.reject { |candidate| candidate.fetch('path') == relative_path }
    end
  end
  private_class_method :status_without_generated
end
