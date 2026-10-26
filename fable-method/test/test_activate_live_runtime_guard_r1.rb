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
  APPROVED_OLD_LAUNCHER_BLOB = '00e2cf4a4f06b0f0a8d8e4be6091a4304e6e2ae4'
  APPROVED_NEW_LAUNCHER_BLOB = 'c201d6b683d084014add2631a5ef5d7fcd5c8f69'
  APPROVED_NEXT_LAUNCHER_BLOB = '0851b2fa877cb3f9692eb0ef84b543caa9088ccf'
  APPROVED_COMPANION_BLOB = '91592132733be6b697bdbef667b97d9065c829c1'
  APPROVED_NEXT_LAUNCHER_SHA256 = '2e3dc83f1d23953044fb487b40af63c95027c0446a82e385d6987df11c361484'
  # Last canonical commit whose Codex materialization pins APPROVED_NEXT_LAUNCHER_BLOB.
  APPROVED_NEXT_SOURCE_COMMIT = '4f75a9103010f683c6c8aabd7ed0b81f0dee992d'

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

    # Establish a true docs-only source in fixture by removing materialized runtime helpers
    codex_scripts = File.join(fixture_canonical, 'fable-method/platforms/codex/fable-method/scripts')
    FileUtils.rm_rf(codex_scripts)
    refute File.exist?(File.join(codex_scripts, 'task_checkpoint.rb')), 'Source must not contain task_checkpoint.rb'
    refute File.exist?(File.join(codex_scripts, 'paused_run_continuation.rb')), 'Source must not contain paused_run_continuation.rb'

    # Allow canonical source gate to pass for docs-only source fixture
    sync_script = File.join(fixture_canonical, 'fable-method/scripts/sync-platforms.sh')
    File.write(sync_script, "#!/bin/bash\nexit 0\n")
    FileUtils.chmod(0755, sync_script)

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

  # --- Reviewed Runtime Upgrade Fixtures and Helpers ---

  def bytes_from_git_blob(blob)
    bytes, stderr, status = Open3.capture3('git', '-C', ROOT, 'cat-file', 'blob', blob)
    raise "Could not load pinned launcher blob #{blob}: #{stderr}" unless status.success?

    bytes = bytes.b
    actual_blob = Digest::SHA1.hexdigest("blob #{bytes.bytesize}\0#{bytes}")
    raise "Pinned fixture mismatch: #{actual_blob} != #{blob}" unless actual_blob == blob

    bytes
  end

  def git_blob_for_file(path)
    bytes = File.binread(path)
    Digest::SHA1.hexdigest("blob #{bytes.bytesize}\0#{bytes}")
  end

  def launcher_bytes_for_blob(blob)
    bytes = bytes_from_git_blob(blob)
    actual_blob = Digest::SHA1.hexdigest("blob #{bytes.bytesize}\0#{bytes}")
    raise "Launcher fixture mismatch: #{actual_blob} != #{blob}" unless actual_blob == blob
    if blob == APPROVED_NEXT_LAUNCHER_BLOB
      actual_sha256 = Digest::SHA256.hexdigest(bytes)
      raise "New launcher SHA-256 mismatch: #{actual_sha256}" unless actual_sha256 == APPROVED_NEXT_LAUNCHER_SHA256
    end

    bytes
  end

  def create_old_live_skill(root, overrides: {})
    create_live_runtime_skill(root, launcher_blob: APPROVED_OLD_LAUNCHER_BLOB, overrides: overrides)
  end

  def create_live_runtime_skill(root, launcher_blob:, overrides: {})
    FileUtils.mkdir_p(File.join(root, 'references'))
    old_pin = overrides[:skill_pin] || launcher_blob
    File.write(File.join(root, 'SKILL.md'), "# Fable Method Live Old\nLauncher pin: #{old_pin}\n")
    File.write(File.join(root, 'references/operational-gates.md'), "# Operational Gates\n")

    scripts_dir = File.join(root, 'scripts')
    FileUtils.mkdir_p(scripts_dir)

    launcher_bytes = overrides[:launcher_bytes] || launcher_bytes_for_blob(launcher_blob)
    launcher = File.join(scripts_dir, 'task_checkpoint.rb')
    File.binwrite(launcher, launcher_bytes)
    FileUtils.chmod(overrides[:launcher_mode] || 0755, launcher)

    unless overrides[:skip_companion]
      companion_bytes = overrides[:companion_bytes] || bytes_from_git_blob(APPROVED_COMPANION_BLOB)
      companion = File.join(scripts_dir, 'paused_run_continuation.rb')
      File.binwrite(companion, companion_bytes)
      FileUtils.chmod(overrides[:companion_mode] || 0644, companion)
    end
  end

  def create_new_source_materialization(root, overrides: {}, launcher_blob: APPROVED_NEW_LAUNCHER_BLOB)
    FileUtils.mkdir_p(File.join(root, 'references'))
    new_pin = overrides[:skill_pin] || launcher_blob
    File.write(File.join(root, 'SKILL.md'), "# Fable Method Source New\nLauncher pin: #{new_pin}\n")
    File.write(File.join(root, 'references/operational-gates.md'), "# Operational Gates\n")

    scripts_dir = File.join(root, 'scripts')
    FileUtils.mkdir_p(scripts_dir)

    launcher_bytes = overrides[:launcher_bytes] || launcher_bytes_for_blob(launcher_blob)
    launcher = File.join(scripts_dir, 'task_checkpoint.rb')
    File.binwrite(launcher, launcher_bytes)
    FileUtils.chmod(overrides[:launcher_mode] || 0755, launcher)

    unless overrides[:skip_companion]
      companion_bytes = overrides[:companion_bytes] || bytes_from_git_blob(APPROVED_COMPANION_BLOB)
      companion = File.join(scripts_dir, 'paused_run_continuation.rb')
      File.binwrite(companion, companion_bytes)
      FileUtils.chmod(overrides[:companion_mode] || 0644, companion)
    end
  end

  def run_upgrade(canonical_abs, live, platform = 'codex', expected_old = '', expected_new = '', env: {})
    script_path = ACTIVATE_SCRIPT
    bash_cmd = <<~BASH
      source #{script_path}
      upgrade_reviewed_runtime "#{canonical_abs}" "#{live}" "#{platform}" "#{expected_old}" "#{expected_new}"
    BASH
    Open3.capture3(env, 'bash', '-c', bash_cmd)
  end

  def run_verify_upgrade(canonical_abs, live, platform = 'codex', expected_old = '', expected_new = '')
    script_path = ACTIVATE_SCRIPT
    bash_cmd = <<~BASH
      source #{script_path}
      verify_reviewed_runtime_upgrade "#{canonical_abs}" "#{live}" "#{platform}" "#{expected_old}" "#{expected_new}"
    BASH
    Open3.capture3('bash', '-c', bash_cmd)
  end

  # --- Reviewed Runtime Upgrade Test Scenarios (AC03 - AC14) ---

  def test_upgrade_reviewed_runtime_success_updates_live_target_and_achieves_parity
    live_dir = File.join(@scratch, 'live_old_codex')
    src_dir = File.join(@scratch, 'source_new_codex')

    create_old_live_skill(live_dir)
    create_new_source_materialization(src_dir)

    # Pre-upgrade: standard guard must reject the mismatch
    _stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_MISMATCH'

    # Run upgrade with explicit expected blobs matching approved pair
    stdout, stderr, status = run_upgrade(
      src_dir, live_dir, 'codex',
      '00e2cf4a4f06b0f0a8d8e4be6091a4304e6e2ae4',
      'c201d6b683d084014add2631a5ef5d7fcd5c8f69'
    )
    assert_equal 0, status.exitstatus, "Expected upgrade to succeed. Stderr: #{stderr}"

    # Verify post-upgrade state in live directory
    post_launcher = File.join(live_dir, 'scripts/task_checkpoint.rb')
    post_companion = File.join(live_dir, 'scripts/paused_run_continuation.rb')
    post_skill = File.join(live_dir, 'SKILL.md')

    assert File.file?(post_launcher)
    assert_equal 'c201d6b683d084014add2631a5ef5d7fcd5c8f69',
                 Digest::SHA1.hexdigest("blob #{File.binread(post_launcher).bytesize}\0#{File.binread(post_launcher)}")
    assert_equal 0755, File.stat(post_launcher).mode & 0777

    assert File.file?(post_companion)
    assert_equal '91592132733be6b697bdbef667b97d9065c829c1',
                 Digest::SHA1.hexdigest("blob #{File.binread(post_companion).bytesize}\0#{File.binread(post_companion)}")
    assert_equal 0644, File.stat(post_companion).mode & 0777

    assert_includes File.read(post_skill), 'c201d6b683d084014add2631a5ef5d7fcd5c8f69'

    # Standard helper preservation guard must now PASS cleanly (full parity)
    stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')
    assert_equal 0, status.exitstatus, "Post-upgrade guard should pass. Stderr: #{stderr}"
  end

  def test_next_approved_runtime_pair_upgrades_exact_launcher_and_preserves_companion
    live_dir = File.join(@scratch, 'live_c201_codex')
    src_dir = File.join(@scratch, 'source_0851_codex')

    create_live_runtime_skill(live_dir, launcher_blob: APPROVED_NEW_LAUNCHER_BLOB)
    create_new_source_materialization(src_dir, launcher_blob: APPROVED_NEXT_LAUNCHER_BLOB)

    stdout, stderr, status = run_upgrade(
      src_dir, live_dir, 'codex', APPROVED_NEW_LAUNCHER_BLOB, APPROVED_NEXT_LAUNCHER_BLOB
    )
    assert_equal 0, status.exitstatus, "Expected the newly approved pair to upgrade. Stderr: #{stderr}"

    post_launcher = File.join(live_dir, 'scripts/task_checkpoint.rb')
    post_companion = File.join(live_dir, 'scripts/paused_run_continuation.rb')
    assert_equal APPROVED_NEXT_LAUNCHER_BLOB, git_blob_for_file(post_launcher)
    assert_equal APPROVED_NEXT_LAUNCHER_SHA256, Digest::SHA256.file(post_launcher).hexdigest
    assert_equal 0755, File.stat(post_launcher).mode & 0777
    assert_includes File.read(File.join(live_dir, 'SKILL.md')), APPROVED_NEXT_LAUNCHER_BLOB
    assert_equal APPROVED_COMPANION_BLOB, git_blob_for_file(post_companion)
    assert_equal 0644, File.stat(post_companion).mode & 0777
    assert_equal File.binread(File.join(src_dir, 'scripts/paused_run_continuation.rb')), File.binread(post_companion)

    _stdout, stderr, status = run_guard(src_dir, live_dir, 'codex')
    assert_equal 0, status.exitstatus, "Post-upgrade guard should pass. Stderr: #{stderr}"
  end

  def test_runtime_pair_allowlist_rejects_an_unapproved_cross_pair
    live_dir = File.join(@scratch, 'live_00e2_codex')
    src_dir = File.join(@scratch, 'source_0851_codex')
    create_old_live_skill(live_dir)
    create_new_source_materialization(src_dir, launcher_blob: APPROVED_NEXT_LAUNCHER_BLOB)
    fingerprint_before = capture_tree_fingerprint(live_dir)

    _stdout, stderr, status = run_upgrade(
      src_dir, live_dir, 'codex', APPROVED_OLD_LAUNCHER_BLOB, APPROVED_NEXT_LAUNCHER_BLOB
    )
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'runtime pair ('
    assert_equal fingerprint_before, capture_tree_fingerprint(live_dir)
  end

  def test_unauthorized_helper_mismatch_refuses_upgrade_and_fails_closed
    live_dir = File.join(@scratch, 'live_arbitrary')
    src_dir = File.join(@scratch, 'source_new_codex')

    create_old_live_skill(live_dir, overrides: {
      launcher_bytes: "#!/usr/bin/env ruby\n# unapproved custom launcher\nputs 'unauthorized'\n"
    })
    create_new_source_materialization(src_dir)

    fingerprint_before = capture_tree_fingerprint(live_dir)

    stdout, stderr, status = run_upgrade(src_dir, live_dir, 'codex')
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'REVIEWED_RUNTIME_UPGRADE_REJECTED'

    fingerprint_after = capture_tree_fingerprint(live_dir)
    assert_equal fingerprint_before, fingerprint_after, 'Live target must not be mutated on refusal'
  end

  def test_old_launcher_identity_mismatch_refuses_upgrade
    live_dir = File.join(@scratch, 'live_old_codex')
    src_dir = File.join(@scratch, 'source_new_codex')

    create_old_live_skill(live_dir)
    create_new_source_materialization(src_dir)

    fingerprint_before = capture_tree_fingerprint(live_dir)

    # Pass mismatched expected_old blob
    wrong_old = '1111111111111111111111111111111111111111'
    stdout, stderr, status = run_upgrade(src_dir, live_dir, 'codex', wrong_old, 'c201d6b683d084014add2631a5ef5d7fcd5c8f69')
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'REVIEWED_RUNTIME_UPGRADE_REJECTED'

    fingerprint_after = capture_tree_fingerprint(live_dir)
    assert_equal fingerprint_before, fingerprint_after
  end

  def test_new_launcher_identity_mismatch_refuses_upgrade
    live_dir = File.join(@scratch, 'live_old_codex')
    src_dir = File.join(@scratch, 'source_new_codex')

    create_old_live_skill(live_dir)
    create_new_source_materialization(src_dir)

    fingerprint_before = capture_tree_fingerprint(live_dir)

    wrong_new = '2222222222222222222222222222222222222222'
    stdout, stderr, status = run_upgrade(
      src_dir, live_dir, 'codex',
      '00e2cf4a4f06b0f0a8d8e4be6091a4304e6e2ae4', wrong_new
    )
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'REVIEWED_RUNTIME_UPGRADE_REJECTED'

    fingerprint_after = capture_tree_fingerprint(live_dir)
    assert_equal fingerprint_before, fingerprint_after
  end

  def test_new_skill_pin_mismatch_refuses_upgrade
    live_dir = File.join(@scratch, 'live_old_codex')
    src_dir = File.join(@scratch, 'source_new_codex')

    create_old_live_skill(live_dir)
    # Source SKILL.md pin does not match new launcher
    create_new_source_materialization(src_dir, overrides: {
      skill_pin: '0000000000000000000000000000000000000000'
    })

    fingerprint_before = capture_tree_fingerprint(live_dir)

    stdout, stderr, status = run_upgrade(src_dir, live_dir, 'codex')
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'REVIEWED_RUNTIME_UPGRADE_REJECTED'
    assert_includes stderr, 'source SKILL.md does not contain approved new launcher pin'

    fingerprint_after = capture_tree_fingerprint(live_dir)
    assert_equal fingerprint_before, fingerprint_after
  end

  def test_new_pair_live_skill_pin_mismatch_refuses_upgrade
    live_dir = File.join(@scratch, 'live_new_pair_wrong_pin')
    src_dir = File.join(@scratch, 'source_0851_codex')
    create_live_runtime_skill(live_dir, launcher_blob: APPROVED_NEW_LAUNCHER_BLOB, overrides: {
      skill_pin: APPROVED_OLD_LAUNCHER_BLOB
    })
    create_new_source_materialization(src_dir, launcher_blob: APPROVED_NEXT_LAUNCHER_BLOB)
    fingerprint_before = capture_tree_fingerprint(live_dir)

    _stdout, stderr, status = run_upgrade(
      src_dir, live_dir, 'codex', APPROVED_NEW_LAUNCHER_BLOB, APPROVED_NEXT_LAUNCHER_BLOB
    )
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'live SKILL.md does not contain approved old launcher pin'
    assert_equal fingerprint_before, capture_tree_fingerprint(live_dir)
  end

  def test_missing_runtime_helper_in_live_refuses_upgrade
    live_dir = File.join(@scratch, 'live_missing_companion')
    src_dir = File.join(@scratch, 'source_new_codex')

    create_old_live_skill(live_dir, overrides: { skip_companion: true })
    create_new_source_materialization(src_dir)

    fingerprint_before = capture_tree_fingerprint(live_dir)

    stdout, stderr, status = run_upgrade(src_dir, live_dir, 'codex')
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_LOSS_PREVENTED'
    assert_includes stderr, 'scripts/paused_run_continuation.rb'

    fingerprint_after = capture_tree_fingerprint(live_dir)
    assert_equal fingerprint_before, fingerprint_after
  end

  def test_runtime_helper_bytes_mismatch_refuses_upgrade
    live_dir = File.join(@scratch, 'live_tampered_companion')
    src_dir = File.join(@scratch, 'source_new_codex')

    create_old_live_skill(live_dir, overrides: {
      companion_bytes: "#!/usr/bin/env ruby\n# tampered companion\n"
    })
    create_new_source_materialization(src_dir)

    fingerprint_before = capture_tree_fingerprint(live_dir)

    stdout, stderr, status = run_upgrade(src_dir, live_dir, 'codex')
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'REVIEWED_RUNTIME_UPGRADE_REJECTED'
    assert_includes stderr, 'live companion blob'

    fingerprint_after = capture_tree_fingerprint(live_dir)
    assert_equal fingerprint_before, fingerprint_after
  end

  def test_companion_permission_and_symlink_errors_refuse_upgrade
    live_dir = File.join(@scratch, 'live_companion_permission')
    src_dir = File.join(@scratch, 'source_companion_permission')
    create_old_live_skill(live_dir)
    create_new_source_materialization(src_dir, overrides: { companion_mode: 0755 })
    fingerprint_before = capture_tree_fingerprint(live_dir)

    _stdout, stderr, status = run_upgrade(src_dir, live_dir)
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'source companion must not be executable'
    assert_equal fingerprint_before, capture_tree_fingerprint(live_dir)

    bad_source_live = File.join(@scratch, 'live_bad_source_companion')
    bad_source = File.join(@scratch, 'source_bad_companion')
    create_old_live_skill(bad_source_live)
    create_new_source_materialization(bad_source, overrides: {
      companion_bytes: "# invalid source companion\n"
    })
    fingerprint_before = capture_tree_fingerprint(bad_source_live)

    _stdout, stderr, status = run_upgrade(bad_source, bad_source_live)
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'source companion blob'
    assert_equal fingerprint_before, capture_tree_fingerprint(bad_source_live)

    live_symlink = File.join(@scratch, 'live_companion_symlink')
    source = File.join(@scratch, 'source_companion_symlink')
    create_old_live_skill(live_symlink)
    create_new_source_materialization(source)
    companion = File.join(live_symlink, 'scripts/paused_run_continuation.rb')
    FileUtils.rm(companion)
    File.symlink(File.join(@scratch, 'external_companion.rb'), companion)
    fingerprint_before = capture_tree_fingerprint(live_symlink)

    _stdout, stderr, status = run_upgrade(source, live_symlink)
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_SYMLINK'
    assert_equal fingerprint_before, capture_tree_fingerprint(live_symlink)
  end

  def test_live_and_source_launcher_permission_errors_refuse_upgrade
    [ :live, :source ].each do |invalid_target|
      live_dir = File.join(@scratch, "live_permission_#{invalid_target}")
      src_dir = File.join(@scratch, "source_permission_#{invalid_target}")
      live_overrides = invalid_target == :live ? { launcher_mode: 0644 } : {}
      source_overrides = invalid_target == :source ? { launcher_mode: 0644 } : {}
      create_live_runtime_skill(live_dir, launcher_blob: APPROVED_NEW_LAUNCHER_BLOB, overrides: live_overrides)
      create_new_source_materialization(
        src_dir, launcher_blob: APPROVED_NEXT_LAUNCHER_BLOB, overrides: source_overrides
      )
      fingerprint_before = capture_tree_fingerprint(live_dir)

      _stdout, stderr, status = run_upgrade(
        src_dir, live_dir, 'codex', APPROVED_NEW_LAUNCHER_BLOB, APPROVED_NEXT_LAUNCHER_BLOB
      )
      assert_equal 2, status.exitstatus
      assert_includes stderr, "#{invalid_target} launcher is not executable"
      assert_equal fingerprint_before, capture_tree_fingerprint(live_dir)
    end
  end

  def test_symlink_in_live_or_source_refuses_upgrade_and_fails_closed
    live_dir = File.join(@scratch, 'live_symlink')
    src_dir = File.join(@scratch, 'source_new_codex')

    create_old_live_skill(live_dir)
    create_new_source_materialization(src_dir)

    # Turn live launcher into a symlink
    launcher = File.join(live_dir, 'scripts/task_checkpoint.rb')
    target = File.join(@scratch, 'external_launcher.rb')
    File.write(target, "puts 'external'\n")
    FileUtils.rm(launcher)
    File.symlink(target, launcher)

    fingerprint_before = capture_tree_fingerprint(live_dir)

    stdout, stderr, status = run_upgrade(src_dir, live_dir, 'codex')
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_SYMLINK'

    fingerprint_after = capture_tree_fingerprint(live_dir)
    assert_equal fingerprint_before, fingerprint_after

    source_live = File.join(@scratch, 'live_source_symlink')
    source_dir = File.join(@scratch, 'source_symlink')
    create_old_live_skill(source_live)
    create_new_source_materialization(source_dir)
    source_launcher = File.join(source_dir, 'scripts/task_checkpoint.rb')
    FileUtils.rm(source_launcher)
    File.symlink(target, source_launcher)
    fingerprint_before = capture_tree_fingerprint(source_live)

    _stdout, stderr, status = run_upgrade(source_dir, source_live, 'codex')
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'source helper is a symlink'
    assert_equal fingerprint_before, capture_tree_fingerprint(source_live)
  end

  def test_staging_or_update_failure_fails_closed_and_does_not_claim_success
    live_dir = File.join(@scratch, 'live_old_codex')
    src_dir = File.join(@scratch, 'source_new_codex')

    create_old_live_skill(live_dir)
    create_new_source_materialization(src_dir)

    # Invalidate source launcher permissions so verification fails
    FileUtils.chmod(0644, File.join(src_dir, 'scripts/task_checkpoint.rb'))

    fingerprint_before = capture_tree_fingerprint(live_dir)

    stdout, stderr, status = run_upgrade(src_dir, live_dir, 'codex')
    assert_equal 2, status.exitstatus
    refute_includes stdout, 'ACTIVATION_RESULT: ACTIVATED'

    fingerprint_after = capture_tree_fingerprint(live_dir)
    assert_equal fingerprint_before, fingerprint_after
  ensure
    # Restore permissions for tmp cleanup
    p = File.join(src_dir, 'scripts/task_checkpoint.rb')
    FileUtils.chmod(0755, p) if File.exist?(p)
  end

  def test_prewrite_live_or_source_identity_change_is_rejected_before_live_write
    real_rsync = ENV.fetch('PATH').split(File::PATH_SEPARATOR)
                      .map { |dir| File.join(dir, 'rsync') }
                      .find { |path| File.file?(path) && File.executable?(path) }
    flunk 'rsync is required for prewrite identity injection test' unless real_rsync

    [ :live, :source ].each do |target_kind|
      live_dir = File.join(@scratch, "live_prewrite_#{target_kind}")
      src_dir = File.join(@scratch, "source_prewrite_#{target_kind}")
      create_live_runtime_skill(live_dir, launcher_blob: APPROVED_NEW_LAUNCHER_BLOB)
      create_new_source_materialization(src_dir, launcher_blob: APPROVED_NEXT_LAUNCHER_BLOB)

      bin_dir = File.join(@scratch, "prewrite_bin_#{target_kind}")
      FileUtils.mkdir_p(bin_dir)
      wrapper = File.join(bin_dir, 'rsync')
      File.write(wrapper, <<~BASH)
        #!/usr/bin/env bash
        set -u
        "$RUNTIME_REAL_RSYNC" "$@"
        rsync_status=$?
        if [[ $rsync_status -ne 0 ]]; then exit "$rsync_status"; fi
        if [[ ! -e "$RUNTIME_PREWRITE_FLAG" ]]; then
          printf '%s' "$RUNTIME_PREWRITE_BYTES" > "$RUNTIME_PREWRITE_TARGET"
          chmod 0755 "$RUNTIME_PREWRITE_TARGET"
          : > "$RUNTIME_PREWRITE_FLAG"
        fi
        exit 0
      BASH
      FileUtils.chmod(0755, wrapper)

      target_path = if target_kind == :live
                      File.join(live_dir, 'scripts/task_checkpoint.rb')
                    else
                      File.join(src_dir, 'scripts/task_checkpoint.rb')
                    end
      injected_bytes = "# identity changed after staging\n"
      live_fingerprint_before = capture_tree_fingerprint(live_dir)
      env = {
        'PATH' => "#{bin_dir}#{File::PATH_SEPARATOR}#{ENV.fetch('PATH')}",
        'RUNTIME_REAL_RSYNC' => real_rsync,
        'RUNTIME_PREWRITE_FLAG' => File.join(@scratch, "prewrite_#{target_kind}.flag"),
        'RUNTIME_PREWRITE_TARGET' => target_path,
        'RUNTIME_PREWRITE_BYTES' => injected_bytes
      }

      stdout, stderr, status = run_upgrade(
        src_dir, live_dir, 'codex', APPROVED_NEW_LAUNCHER_BLOB, APPROVED_NEXT_LAUNCHER_BLOB, env: env
      )
      assert_equal 2, status.exitstatus, "#{target_kind} identity change should fail before write: #{stderr}"
      assert_includes stderr, "#{target_kind} launcher blob"
      refute_includes stdout, 'ACTIVATION_RESULT: ACTIVATED'
      assert_equal injected_bytes, File.binread(target_path)
      if target_kind == :live
        refute_equal File.binread(File.join(src_dir, 'scripts/task_checkpoint.rb')),
                     File.binread(File.join(live_dir, 'scripts/task_checkpoint.rb'))
      else
        assert_equal live_fingerprint_before, capture_tree_fingerprint(live_dir)
      end
    end
  end

  def test_full_upgrade_cli_flow_in_disposable_repo
    fixture_canonical = File.join(@scratch, 'fixture_repo_upgrade')
    fixture_home = File.join(@scratch, 'fixture_home_upgrade')
    FileUtils.mkdir_p(fixture_canonical)
    FileUtils.mkdir_p(File.join(fixture_home, '.codex/skills'))

    # Archive the last commit that pins the approved NEXT launcher
    Open3.pipeline(
      ['git', '-C', ROOT, 'archive', APPROVED_NEXT_SOURCE_COMMIT],
      ['tar', '-x', '-C', fixture_canonical]
    )

    # Overlay the current activation code. The approved NEW -> NEXT runtime
    # pair is historical, so its manifest and materialization come from the
    # archived commit above.
    %w[
      fable-method/scripts/platform_manifest.rb
      fable-method/scripts/activate-live.sh
    ].each do |relative_path|
      source = File.join(ROOT, relative_path)
      destination = File.join(fixture_canonical, relative_path)
      FileUtils.mkdir_p(File.dirname(destination))
      FileUtils.cp(source, destination)
    end

    # Rewrite paths in fixture scripts
    replacements = {
      '/Users/kelvin/VibeCoding-WorkSpace/skill' => fixture_canonical,
      '/Users/kelvin' => fixture_home
    }
    [
      File.join(fixture_canonical, 'fable-method/scripts/activate-live.sh'),
      File.join(fixture_canonical, 'fable-method/scripts/sync-platforms.sh'),
      File.join(fixture_canonical, 'fable-method/scripts/platform_manifest.rb'),
      File.join(fixture_canonical, 'fable-method/platforms.yaml')
    ].each do |p|
      next unless File.file?(p)

      content = File.binread(p)
      content = content.gsub(Regexp.union(replacements.keys)) { |k| replacements[k] }
      File.binwrite(p, content)
    end

    # Make sure sync-platforms passes in fixture
    sync_script = File.join(fixture_canonical, 'fable-method/scripts/sync-platforms.sh')
    File.write(sync_script, "#!/bin/bash\nexit 0\n")
    FileUtils.chmod(0755, sync_script)

    # Initialize git in fixture_canonical
    system('git', '-C', fixture_canonical, 'init', '-q')
    system('git', '-C', fixture_canonical, 'branch', '-M', 'master')
    system('git', '-C', fixture_canonical, 'config', 'user.email', 'test@test.local')
    system('git', '-C', fixture_canonical, 'config', 'user.name', 'Test')
    system('git', '-C', fixture_canonical, 'add', '.')
    system('git', '-C', fixture_canonical, 'commit', '-q', '-m', 'fixture commit')
    commit_sha = `git -C "#{fixture_canonical}" rev-parse HEAD`.strip
    system('git', '-C', fixture_canonical, 'remote', 'add', 'origin', fixture_canonical)
    system('git', '-C', fixture_canonical, 'update-ref', 'refs/remotes/origin/master', commit_sha)

    # Setup live destination in fixture_home with approved old runtime
    disposable_live = File.join(fixture_home, '.codex/skills/fable-method')
    create_live_runtime_skill(disposable_live, launcher_blob: APPROVED_NEW_LAUNCHER_BLOB)

    activate_cmd = File.join(fixture_canonical, 'fable-method/scripts/activate-live.sh')

    # Step A: Ordinary activation must be refused due to runtime helper mismatch
    stdout, stderr, status = Open3.capture3('/bin/bash', activate_cmd, '--activate', '--platform', 'codex')
    assert_equal 2, status.exitstatus
    assert_includes stderr, 'ACTIVATION_RUNTIME_HELPER_MISMATCH'

    # Step B: Upgrade mode with verified identities succeeds
    stdout, stderr, status = Open3.capture3(
      '/bin/bash', activate_cmd,
      '--activate', '--platform', 'codex',
      '--upgrade-reviewed-runtime',
      '--expected-old-launcher-blob', APPROVED_NEW_LAUNCHER_BLOB,
      '--expected-new-launcher-blob', APPROVED_NEXT_LAUNCHER_BLOB
    )
    assert_equal 0, status.exitstatus, "Expected CLI upgrade to succeed. Stderr: #{stderr}"
    assert_includes stdout, 'ACTIVATION_MODE: UPGRADE_REVIEWED_RUNTIME'
    assert_includes stdout, "OLD_LAUNCHER_BLOB: #{APPROVED_NEW_LAUNCHER_BLOB}"
    assert_includes stdout, "NEW_LAUNCHER_BLOB: #{APPROVED_NEXT_LAUNCHER_BLOB}"
    assert_includes stdout, 'ACTIVATION_RESULT: ACTIVATED'
    assert_includes stdout, 'FINAL_STATE: EXACT_CURRENT_MATERIALIZATION'

    # Step C: Subsequent ordinary activation reports ALREADY_CURRENT
    stdout, stderr, status = Open3.capture3('/bin/bash', activate_cmd, '--activate', '--platform', 'codex')
    assert_equal 0, status.exitstatus
    assert_includes stdout, 'ACTIVATION_RESULT: ALREADY_CURRENT'
  end
end
