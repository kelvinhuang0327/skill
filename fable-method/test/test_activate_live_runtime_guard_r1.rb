# frozen_string_literal: true

require 'minitest/autorun'
require 'tmpdir'
require 'fileutils'
require 'open3'
require 'digest'

class TestActivateLiveRuntimeGuardR1 < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  ACTIVATE_SCRIPT = File.join(ROOT, 'fable-method/scripts/activate-live.sh')
  REAL_LIVE_CODEX_SKILL = '/Users/kelvin/.codex/skills/fable-method'

  def setup
    @scratch = File.realpath(Dir.mktmpdir('fable-guard-r1-test-'))
    @real_live_fingerprint = capture_tree_fingerprint(REAL_LIVE_CODEX_SKILL)
  end

  def teardown
    FileUtils.rm_rf(@scratch) if @scratch && File.directory?(@scratch)
    # AC11: Invariant check - real installed skill must NEVER mutate
    current_live_fingerprint = capture_tree_fingerprint(REAL_LIVE_CODEX_SKILL)
    assert_equal @real_live_fingerprint, current_live_fingerprint,
                 'AC11 INVARIANT VIOLATION: real installed Codex skill was mutated!'
  end

  # --- Disposable Fixture Helpers ---

  def create_live_skill(root, helpers: true, extra_helpers: [])
    FileUtils.mkdir_p(File.join(root, 'references'))
    File.write(File.join(root, 'SKILL.md'), "# Fable Method Live\n")
    File.write(File.join(root, 'references/operational-gates.md'), "# Operational Gates\n")

    if helpers
      scripts_dir = File.join(root, 'scripts')
      FileUtils.mkdir_p(scripts_dir)

      launcher = File.join(scripts_dir, 'task_checkpoint.rb')
      File.write(launcher, "#!/usr/bin/env ruby\n# launcher v1\nputs 'task_checkpoint'\n")
      FileUtils.chmod(0755, launcher)

      companion = File.join(scripts_dir, 'paused_run_continuation.rb')
      File.write(companion, "#!/usr/bin/env ruby\n# companion v1\nputs 'paused_run_continuation'\n")
      FileUtils.chmod(0644, companion)
    end

    extra_helpers.each do |rel_name, content, mode|
      path = File.join(root, rel_name)
      FileUtils.mkdir_p(File.dirname(path))
      File.write(path, content)
      FileUtils.chmod(mode || 0644, path)
    end
  end

  def create_source_materialization(root, helpers: false, helper_overrides: {})
    FileUtils.mkdir_p(File.join(root, 'references'))
    File.write(File.join(root, 'SKILL.md'), "# Fable Method Source\n")
    File.write(File.join(root, 'references/operational-gates.md'), "# Operational Gates\n")

    if helpers
      scripts_dir = File.join(root, 'scripts')
      FileUtils.mkdir_p(scripts_dir)

      launcher_content = helper_overrides[:task_checkpoint] || "#!/usr/bin/env ruby\n# launcher v1\nputs 'task_checkpoint'\n"
      launcher = File.join(scripts_dir, 'task_checkpoint.rb')
      File.write(launcher, launcher_content)
      FileUtils.chmod(helper_overrides[:task_checkpoint_mode] || 0755, launcher)

      companion_content = helper_overrides[:paused_run_continuation] || "#!/usr/bin/env ruby\n# companion v1\nputs 'paused_run_continuation'\n"
      companion = File.join(scripts_dir, 'paused_run_continuation.rb')
      File.write(companion, companion_content)
      FileUtils.chmod(helper_overrides[:paused_run_continuation_mode] || 0644, companion)
    end
  end

  def capture_tree_fingerprint(root)
    return 'ABSENT' unless File.directory?(root)

    entries = []
    Dir.glob(File.join(root, '**/*'), File::FNM_DOTMATCH).sort.each do |p|
      rel = p.sub("#{root}/", '')
      next if rel == '.' || rel == '..'

      st = File.lstat(p)
      digest = if st.file?
                 Digest::SHA256.file(p).hexdigest
               elsif st.symlink?
                 "SYMLINK->#{File.readlink(p)}"
               else
                 'DIR'
               end
      entries << [rel, st.mode & 07777, digest]
    end
    entries
  end

  def run_guard(canonical_abs, live, platform = 'codex')
    script_path = ACTIVATE_SCRIPT
    bash_cmd = <<~BASH
      source #{script_path}
      guard_runtime_helper_preservation "#{canonical_abs}" "#{live}" "#{platform}"
    BASH
    Open3.capture3('bash', '-c', bash_cmd)
  end

  # --- Tests ---

  def test_docs_only_source_with_live_helpers_refuses_destructive_sync_and_preserves_live
    live_dir = File.join(@scratch, 'live_codex')
    src_dir = File.join(@scratch, 'source_docs_only')

    create_live_skill(live_dir, helpers: true)
    create_source_materialization(src_dir, helpers: false) # Docs only, no helpers

    fingerprint_before = capture_tree_fingerprint(live_dir)

    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')

    # AC04: Guard blocks execution
    refute_equal 0, status.exitstatus, 'Expected guard to refuse docs-only source when live helpers exist'
    assert_equal 2, status.exitstatus

    # AC06: Output reports exact missing file names
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_LOSS_PREVENTED'
    assert_includes stderr, 'scripts/task_checkpoint.rb'
    assert_includes stderr, 'scripts/paused_run_continuation.rb'

    # AC05: Live target bytes completely preserved
    fingerprint_after = capture_tree_fingerprint(live_dir)
    assert_equal fingerprint_before, fingerprint_after, 'AC05 violation: live bytes modified on refusal'
  end

  def test_missing_single_helper_in_source_reports_exact_filename_and_refuses
    live_dir = File.join(@scratch, 'live_codex')
    src_dir = File.join(@scratch, 'source_partial')

    create_live_skill(live_dir, helpers: true)
    # Source only has task_checkpoint.rb, missing paused_run_continuation.rb
    FileUtils.mkdir_p(File.join(src_dir, 'scripts'))
    File.write(File.join(src_dir, 'SKILL.md'), "# Source\n")
    launcher = File.join(src_dir, 'scripts/task_checkpoint.rb')
    File.write(launcher, "#!/usr/bin/env ruby\n# launcher v1\nputs 'task_checkpoint'\n")
    FileUtils.chmod(0755, launcher)

    fingerprint_before = capture_tree_fingerprint(live_dir)

    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')

    refute_equal 0, status.exitstatus
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_LOSS_PREVENTED'
    assert_includes stderr, 'scripts/paused_run_continuation.rb'
    refute_includes stderr, 'missing: scripts/task_checkpoint.rb'

    fingerprint_after = capture_tree_fingerprint(live_dir)
    assert_equal fingerprint_before, fingerprint_after
  end

  def test_source_and_live_helpers_consistent_passes_without_false_positive
    live_dir = File.join(@scratch, 'live_codex')
    src_dir = File.join(@scratch, 'source_valid')

    create_live_skill(live_dir, helpers: true)
    create_source_materialization(src_dir, helpers: true) # Identical helpers

    fingerprint_before = capture_tree_fingerprint(live_dir)

    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')

    # AC07: When consistent, no error is produced
    assert_equal 0, status.exitstatus, "Expected guard to pass when helpers match. Stderr: #{stderr}"
    assert_empty stderr

    fingerprint_after = capture_tree_fingerprint(live_dir)
    assert_equal fingerprint_before, fingerprint_after
  end

  def test_source_and_live_helper_mismatch_refuses_and_fails_closed
    live_dir = File.join(@scratch, 'live_codex')
    src_dir = File.join(@scratch, 'source_mismatched')

    create_live_skill(live_dir, helpers: true)
    # Different content in task_checkpoint.rb
    create_source_materialization(src_dir, helpers: true, helper_overrides: {
      task_checkpoint: "#!/usr/bin/env ruby\n# launcher v2 different bytes\n"
    })

    fingerprint_before = capture_tree_fingerprint(live_dir)

    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')

    # AC08: Source / Live helper mismatch fails closed, does not claim consistent
    refute_equal 0, status.exitstatus
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_MISMATCH'
    assert_includes stderr, 'scripts/task_checkpoint.rb'

    fingerprint_after = capture_tree_fingerprint(live_dir)
    assert_equal fingerprint_before, fingerprint_after
  end

  def test_live_helper_is_symlink_fails_closed
    live_dir = File.join(@scratch, 'live_codex')
    src_dir = File.join(@scratch, 'source_valid')

    create_live_skill(live_dir, helpers: false)
    FileUtils.mkdir_p(File.join(live_dir, 'scripts'))
    target_dummy = File.join(@scratch, 'external_dummy.rb')
    File.write(target_dummy, "puts 'external'\n")
    File.symlink(target_dummy, File.join(live_dir, 'scripts/task_checkpoint.rb'))

    create_source_materialization(src_dir, helpers: true)

    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')

    # AC09: Symlink fails closed
    refute_equal 0, status.exitstatus
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_SYMLINK'
    assert_includes stderr, 'live helper is a symlink: scripts/task_checkpoint.rb'
  end

  def test_source_helper_is_symlink_fails_closed
    live_dir = File.join(@scratch, 'live_codex')
    src_dir = File.join(@scratch, 'source_symlink')

    create_live_skill(live_dir, helpers: true)
    create_source_materialization(src_dir, helpers: false)
    FileUtils.mkdir_p(File.join(src_dir, 'scripts'))
    target_dummy = File.join(@scratch, 'external_dummy.rb')
    File.write(target_dummy, "puts 'external'\n")
    File.symlink(target_dummy, File.join(src_dir, 'scripts/task_checkpoint.rb'))

    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')

    # AC09: Symlink in source fails closed
    refute_equal 0, status.exitstatus
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_SYMLINK'
    assert_includes stderr, 'source helper is a symlink: scripts/task_checkpoint.rb'
  end

  def test_live_scripts_dir_is_symlink_fails_closed
    live_dir = File.join(@scratch, 'live_codex')
    src_dir = File.join(@scratch, 'source_valid')

    create_live_skill(live_dir, helpers: false)
    external_scripts = File.join(@scratch, 'external_scripts')
    FileUtils.mkdir_p(external_scripts)
    File.symlink(external_scripts, File.join(live_dir, 'scripts'))

    create_source_materialization(src_dir, helpers: true)

    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')

    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_SYMLINK'
    assert_includes stderr, 'live scripts path is a symlink'
  end

  def test_source_scripts_dir_is_symlink_fails_closed
    live_dir = File.join(@scratch, 'live_codex')
    src_dir = File.join(@scratch, 'source_symlink_dir')

    create_live_skill(live_dir, helpers: true)
    create_source_materialization(src_dir, helpers: false)
    external_scripts = File.join(@scratch, 'external_scripts')
    FileUtils.mkdir_p(external_scripts)
    File.symlink(external_scripts, File.join(src_dir, 'scripts'))

    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')

    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_SYMLINK'
    assert_includes stderr, 'source scripts path is a symlink'
  end

  def test_source_launcher_not_executable_fails_closed
    live_dir = File.join(@scratch, 'live_codex')
    src_dir = File.join(@scratch, 'source_non_exec')

    create_live_skill(live_dir, helpers: true)
    create_source_materialization(src_dir, helpers: true, helper_overrides: {
      task_checkpoint_mode: 0644
    })

    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')

    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_UNVERIFIABLE'
    assert_includes stderr, 'source launcher is not executable'
  end

  def test_source_helper_unreadable_fails_closed
    live_dir = File.join(@scratch, 'live_codex')
    src_dir = File.join(@scratch, 'source_unreadable')

    create_live_skill(live_dir, helpers: true)
    create_source_materialization(src_dir, helpers: true)
    FileUtils.chmod(0000, File.join(src_dir, 'scripts/paused_run_continuation.rb'))

    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')

    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_UNVERIFIABLE'
    assert_includes stderr, 'source helper is not readable'
  ensure
    # Restore permissions so tmp cleanup can succeed
    unreadable = File.join(src_dir, 'scripts/paused_run_continuation.rb')
    FileUtils.chmod(0644, unreadable) if File.exist?(unreadable)
  end

  def test_extra_custom_helper_in_live_is_discovered_and_protected
    live_dir = File.join(@scratch, 'live_codex')
    src_dir = File.join(@scratch, 'source_valid')

    create_live_skill(live_dir, helpers: true, extra_helpers: [
      ['scripts/custom_engine.rb', "#!/usr/bin/env ruby\nputs 'engine'\n", 0644]
    ])
    create_source_materialization(src_dir, helpers: true) # has standard helpers, lacks custom_engine.rb

    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')

    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_LOSS_PREVENTED'
    assert_includes stderr, 'scripts/custom_engine.rb'
  end

  def test_non_codex_platform_passes_without_codex_helper_requirement
    live_dir = File.join(@scratch, 'live_claude')
    src_dir = File.join(@scratch, 'source_docs')

    create_live_skill(live_dir, helpers: false)
    create_source_materialization(src_dir, helpers: false)

    stdout, stderr, status = run_guard(src_dir, live_dir, 'claude')

    assert_equal 0, status.exitstatus
    assert_empty stderr
  end

  def test_live_absent_passes_without_live_helpers_to_preserve
    live_dir = File.join(@scratch, 'live_absent')
    src_dir = File.join(@scratch, 'source_docs')
    create_source_materialization(src_dir, helpers: false)

    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')

    assert_equal 0, status.exitstatus
    assert_empty stderr
  end

  def test_full_activation_flow_blocked_before_rsync_in_disposable_repo
    fixture_canonical = File.join(@scratch, 'fixture_repo')
    fixture_home = File.join(@scratch, 'fixture_home')
    FileUtils.mkdir_p(fixture_canonical)
    FileUtils.mkdir_p(File.join(fixture_home, '.codex/skills'))

    # Archive and copy current worktree
    Open3.pipeline(
      ['git', '-C', ROOT, 'archive', 'HEAD'],
      ['tar', '-x', '-C', fixture_canonical]
    )

    # Overlay our modified activate-live.sh into fixture_canonical
    FileUtils.cp(ACTIVATE_SCRIPT, File.join(fixture_canonical, 'fable-method/scripts/activate-live.sh'))

    # Rewrite paths in fixture repository scripts and manifest
    replacements = {
      '/Users/kelvin/VibeCoding-WorkSpace/skill' => fixture_canonical,
      '/Users/kelvin' => fixture_home
    }
    scripts_to_rewrite = [
      File.join(fixture_canonical, 'fable-method/scripts/activate-live.sh'),
      File.join(fixture_canonical, 'fable-method/scripts/sync-platforms.sh'),
      File.join(fixture_canonical, 'fable-method/scripts/platform_manifest.rb'),
      File.join(fixture_canonical, 'fable-method/platforms.yaml')
    ]
    scripts_to_rewrite.each do |p|
      next unless File.file?(p)

      content = File.binread(p)
      content = content.gsub(Regexp.union(replacements.keys)) { |k| replacements[k] }
      File.binwrite(p, content)
    end

    # Initialize git repo in fixture_canonical to satisfy git guards
    system('git', '-C', fixture_canonical, 'init', '-q')
    system('git', '-C', fixture_canonical, 'branch', '-M', 'master')
    system('git', '-C', fixture_canonical, 'config', 'user.email', 'test@test.local')
    system('git', '-C', fixture_canonical, 'config', 'user.name', 'Test')
    system('git', '-C', fixture_canonical, 'add', '.')
    system('git', '-C', fixture_canonical, 'commit', '-q', '-m', 'fixture commit')
    commit_sha = `git -C "#{fixture_canonical}" rev-parse HEAD`.strip
    system('git', '-C', fixture_canonical, 'remote', 'add', 'origin', fixture_canonical)
    system('git', '-C', fixture_canonical, 'update-ref', 'refs/remotes/origin/master', commit_sha)

    # Setup live destination in fixture_home with live helpers
    disposable_live = File.join(fixture_home, '.codex/skills/fable-method')
    create_live_skill(disposable_live, helpers: true)
    live_fingerprint_before = capture_tree_fingerprint(disposable_live)

    # Run activate-live.sh --activate --platform codex in fixture
    activate_cmd = File.join(fixture_canonical, 'fable-method/scripts/activate-live.sh')
    stdout, stderr, status = Open3.capture3('/bin/bash', activate_cmd, '--activate', '--platform', 'codex')

    # AC03 & AC04: Refusal before rsync --delete
    refute_equal 0, status.exitstatus, 'Expected activation to be refused'
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_LOSS_PREVENTED'
    assert_includes stderr, 'scripts/task_checkpoint.rb'
    assert_includes stderr, 'scripts/paused_run_continuation.rb'

    # AC05: All bytes in live destination remain intact
    live_fingerprint_after = capture_tree_fingerprint(disposable_live)
    assert_equal live_fingerprint_before, live_fingerprint_after, 'AC05: live target modified despite refusal'
  end
end
