# frozen_string_literal: true

require 'digest'
require 'fileutils'
require 'json'
require 'minitest/autorun'
require 'tmpdir'

class NonGitArtifactManifestContractTest < Minitest::Test
  CONTRACT = File.expand_path('../shared/references/judge-handoff.md', __dir__)

  def setup
    @contract = File.read(CONTRACT).gsub(/\s+/, ' ')
  end

  def test_contract_defines_manifest_schema_completeness_and_fail_closed_rules
    %w[
      ARTIFACT_ROOT MANIFEST_SCHEMA_VERSION ROOT_EXISTS ROOT_ENTRY_TYPE ROOT_MODE
      ROOT_CHILDREN_ENUMERATED MANIFEST_STATUS ENTRIES RELATIVE_PATH ENTRY_TYPE
      MODE CHILDREN_ENUMERATED SIZE_BYTES SHA256 BASELINE_MANIFEST_SHA256
      FINAL_MANIFEST_SHA256 ARTIFACT_SHA256 MANIFEST_DELTA WRITE_LEDGER
      ACCEPTANCE_CRITERIA
    ].each { |field| assert_includes @contract, field }
    [
      'exact absolute canonical artifact root',
      'every descendant directory and regular file, including empty directories',
      'normalized root-relative',
      'Unicode NFC', 'normalization collision',
      'complete entry sets and all entry fields',
      'normalized relative-path UTF-8 bytes', 'canonical UTF-8 JSON',
      'fixed schema, field order, and no insignificant whitespace',
      'establishes an absent root', 'MANIFEST_STATUS: REJECTED',
      'lstat', 'AT_SYMLINK_NOFOLLOW', 'O_NOFOLLOW',
      'symbolic link', 'nested Git', '.git', 'submodule', 'FIFO', 'socket',
      'device', 'enumeration, metadata, or read error',
      'does not prove that no other activity occurred',
      'Judge launch authority, depth selection, or verdict standards'
    ].each { |phrase| assert_includes @contract, phrase }
  end

  def test_small_fixture_includes_empty_directories_and_compares_complete_entries
    scratch_dir('fable-manifest-safe') do |parent|
      root = File.join(parent, 'artifact')
      nested = File.join(root, 'nested')
      FileUtils.mkdir_p(File.join(nested, 'empty'))
      File.binwrite(File.join(nested, 'notes.txt'), 'before')
      File.binwrite(File.join(root, 'report.txt'), 'baseline')

      assert_equal %w[nested report.txt], Dir.children(root).sort
      assert_equal %w[empty notes.txt], Dir.children(nested).sort
      baseline_paths = %w[nested nested/empty nested/notes.txt report.txt]
      baseline = fixture_manifest(root, baseline_paths)
      entries = baseline.fetch('ENTRIES')

      assert_equal baseline_paths.sort, entries.map { |entry| entry.fetch('RELATIVE_PATH') }
      assert_equal 'DIRECTORY', entries.find { |entry| entry['RELATIVE_PATH'] == 'nested/empty' }.fetch('ENTRY_TYPE')
      assert_equal true, entries.find { |entry| entry['RELATIVE_PATH'] == 'nested/empty' }.fetch('CHILDREN_ENUMERATED')
      report = entries.find { |entry| entry['RELATIVE_PATH'] == 'report.txt' }
      assert_equal 'REGULAR_FILE', report.fetch('ENTRY_TYPE')
      assert_equal 8, report.fetch('SIZE_BYTES')
      assert_equal Digest::SHA256.hexdigest('baseline'), report.fetch('SHA256')
      assert entries.all? { |entry| entry.fetch('MODE').match?(/\A[0-7]{4}\z/) }

      identical = fixture_manifest(root, baseline_paths)
      assert_equal Digest::SHA256.hexdigest(JSON.generate(baseline)),
                   Digest::SHA256.hexdigest(JSON.generate(identical))

      File.binwrite(File.join(root, 'report.txt'), 'final')
      FileUtils.mkdir_p(File.join(root, 'added'))
      File.binwrite(File.join(root, 'added', 'new.txt'), 'new')
      Dir.rmdir(File.join(nested, 'empty'))
      final_paths = %w[added added/new.txt nested nested/notes.txt report.txt]
      final = fixture_manifest(root, final_paths)
      assert_equal(
        {
          'added' => %w[added added/new.txt],
          'changed' => %w[report.txt],
          'removed' => %w[nested/empty],
          'root_changed' => false
        },
        full_collection_delta(baseline, final)
      )
    end
  end

  def test_unsafe_fixture_types_and_paths_are_rejected_without_following_them
    scratch_dir('fable-manifest-reject') do |parent|
      root = File.join(parent, 'artifact')
      FileUtils.mkdir_p(File.join(root, 'nested'))
      outside = File.join(parent, 'outside.txt')
      File.binwrite(outside, 'external bytes')

      link = File.join(root, 'linked.txt')
      File.symlink(outside, link)
      assert_equal 'symbolic link', rejection_reason('linked.txt', File.lstat(link))
      root_link = File.join(parent, 'root-link')
      File.symlink(root, root_link)
      assert_equal 'symbolic link', rejection_reason('root-link', File.lstat(root_link))

      git_dir = File.join(root, 'nested', '.git')
      Dir.mkdir(git_dir)
      assert_equal 'nested Git metadata or submodule pointer',
                   rejection_reason('.git', File.lstat(git_dir))
      Dir.rmdir(git_dir)
      File.binwrite(git_dir, "gitdir: #{File.join(parent, 'outside-git')}\n")
      assert_equal 'nested Git metadata or submodule pointer',
                   rejection_reason('.git', File.lstat(git_dir))

      fifo = File.join(root, 'pipe')
      File.mkfifo(fifo)
      assert_equal 'special file', rejection_reason('pipe', File.lstat(fifo))

      assert_raises(ArgumentError) { normalized_relative_path('/absolute') }
      assert_raises(ArgumentError) { normalized_relative_path('a/../escape') }
      assert_raises(ArgumentError) { normalized_relative_path("bad\\name") }

      seen = {}
      first = normalized_relative_path('café.txt')
      second = normalized_relative_path("cafe\u0301.txt")
      assert_equal first, second
      seen[first] = true
      assert_raises(ArgumentError) { register_path!(seen, second) }

    end
  end

  def fixture_manifest(root, paths)
    root_stat = File.lstat(root)
    raise ArgumentError, 'fixture root must be a real directory' unless root_stat.directory? && !root_stat.symlink?

    entries = paths.map { |path| fixture_entry(root, path) }.sort_by { |entry| entry.fetch('RELATIVE_PATH').b }
    {
      'ARTIFACT_ROOT' => root,
      'MANIFEST_SCHEMA_VERSION' => 1,
      'ROOT_EXISTS' => true,
      'ROOT_ENTRY_TYPE' => 'DIRECTORY',
      'ROOT_MODE' => mode(root_stat),
      'ROOT_CHILDREN_ENUMERATED' => true,
      'MANIFEST_STATUS' => 'COMPLETE',
      'ENTRIES' => entries
    }
  end

  def fixture_entry(root, relative_path)
    path = normalized_relative_path(relative_path)
    stat = File.lstat(File.join(root, path))
    raise ArgumentError, 'fixture entries may not be links or special files' unless stat.directory? || stat.file?

    entry = {
      'RELATIVE_PATH' => path,
      'MODE' => mode(stat),
      'ENTRY_TYPE' => stat.directory? ? 'DIRECTORY' : 'REGULAR_FILE'
    }
    if stat.directory?
      entry['CHILDREN_ENUMERATED'] = true
    else
      File.open(File.join(root, path), File::RDONLY | File::NOFOLLOW | File::NONBLOCK) do |file|
        opened_stat = file.stat
        raise ArgumentError, 'opened file identity changed' unless opened_stat.file? &&
          [opened_stat.dev, opened_stat.ino] == [stat.dev, stat.ino]
        bytes = file.read
        entry['SIZE_BYTES'] = bytes.bytesize
        entry['SHA256'] = Digest::SHA256.hexdigest(bytes)
      end
    end
    entry
  end

  def full_collection_delta(baseline, final)
    before = baseline.fetch('ENTRIES').to_h { |entry| [entry.fetch('RELATIVE_PATH'), entry] }
    after = final.fetch('ENTRIES').to_h { |entry| [entry.fetch('RELATIVE_PATH'), entry] }
    {
      'added' => (after.keys - before.keys).sort_by(&:b),
      'changed' => (before.keys & after.keys).select { |path| before.fetch(path) != after.fetch(path) }.sort_by(&:b),
      'removed' => (before.keys - after.keys).sort_by(&:b),
      'root_changed' => %w[ARTIFACT_ROOT MANIFEST_SCHEMA_VERSION MANIFEST_STATUS ROOT_EXISTS ROOT_ENTRY_TYPE ROOT_MODE ROOT_CHILDREN_ENUMERATED].any? do |field|
        baseline.fetch(field) != final.fetch(field)
      end
    }
  end

  def rejection_reason(name, stat)
    return 'symbolic link' if stat.symlink?
    return 'nested Git metadata or submodule pointer' if name == '.git'
    return 'special file' unless stat.directory? || stat.file?

    nil
  end

  def normalized_relative_path(path)
    raise ArgumentError, 'invalid path' unless path.valid_encoding? && path.encoding == Encoding::UTF_8
    raise ArgumentError, 'invalid path' if path.start_with?('/') || path.include?('\\')

    normalized = path.split('/', -1).map(&:unicode_normalize).join('/')
    raise ArgumentError, 'invalid path' if normalized.split('/', -1).any? { |part| part.empty? || part == '.' || part == '..' }

    normalized
  end

  def register_path!(seen, path)
    raise ArgumentError, 'normalization collision' if seen.key?(path)

    seen[path] = true
  end

  def mode(stat)
    format('%04o', stat.mode & 0o7777)
  end

  def scratch_dir(prefix, &block)
    Dir.mktmpdir(prefix, File.expand_path('../..', __dir__), &block)
  end
end
