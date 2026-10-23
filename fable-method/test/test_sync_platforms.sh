#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ruby - "$root" <<'RUBY'
require 'tmpdir'
require 'fileutils'
require 'open3'
require 'digest'
root = ARGV.fetch(0)
count = 0
check = lambda do |truth, message|
  raise message unless truth
  count += 1
  puts "PASS: #{message}"
end
Dir.mktmpdir('fable-sync-platforms-') do |tmp|
  tmp = File.realpath(tmp)
  %w[fable-method fable-judge].each { |skill| FileUtils.cp_r(File.join(root, skill), tmp) }
  codex_dir = File.join(tmp, 'fable-method/platforms/codex/fable-method')
  %w[scripts/task_checkpoint.rb scripts/paused_run_continuation.rb scripts/fable_artifact_controls.py].each do |relative|
    FileUtils.rm_f(File.join(codex_dir, relative))
  end
  scripts_dir = File.join(codex_dir, 'scripts')
  Dir.rmdir(scripts_dir) if Dir.exist?(scripts_dir) && Dir.empty?(scripts_dir)
  codex_skill = File.join(codex_dir, 'SKILL.md')
  skill_bytes = File.read(codex_skill)
  if skill_bytes.include?("\n## Codex Runtime Materialization\n")
    File.write(codex_skill, skill_bytes.split("\n## Codex Runtime Materialization\n", 2).first + "\n")
  end
  %w[fable-method/scripts/sync-platforms.sh fable-method/scripts/platform_manifest.rb fable-method/platforms.yaml].each do |rel|
    path = File.join(tmp, rel)
    File.write(path, File.read(path).gsub('/Users/kelvin/VibeCoding-WorkSpace/skill', tmp))
  end
  out, status = Open3.capture2e('git', '-C', tmp, 'init', '-q')
  raise out unless status.success?
  script = File.join(tmp, 'fable-method/scripts/sync-platforms.sh')
  run = lambda do |*args|
    output, status = Open3.capture2e('bash', script, *args)
    [status.success?, output]
  end
  digest = lambda do |skill|
    Dir.glob(File.join(tmp, "fable-method/platforms/*/#{skill}/**/*"), File::FNM_DOTMATCH).select { |f| File.file?(f) }.sort.to_h do |f|
      [f.delete_prefix(tmp), [Digest::SHA256.file(f).hexdigest, File.stat(f).mode]]
    end
  end
  bundle_digest = lambda do |skill, platform|
    Dir.glob(File.join(tmp, "fable-method/platforms/#{platform}/#{skill}/**/*"), File::FNM_DOTMATCH).select { |f| File.file?(f) }.sort.to_h do |f|
      [f.delete_prefix(tmp), [Digest::SHA256.file(f).hexdigest, File.stat(f).mode]]
    end
  end
  method_before = digest.call('fable-method')
  judge_before = digest.call('fable-judge')
  non_codex_before = %w[claude gemini antigravity].to_h do |platform|
    [platform, bundle_digest.call('fable-method', platform)]
  end
  implicit = run.call('--check')
  explicit = run.call('--check', '--skill', 'fable-method')
  check.call(!implicit[0] && implicit == explicit && implicit[1].include?('DRIFT: codex skill=fable-method'), 'docs-only Codex bundle is not Runtime Distribution Ready')
  check.call(run.call('--write')[0], 'Method write succeeds')
  check.call(non_codex_before == %w[claude gemini antigravity].to_h { |platform| [platform, bundle_digest.call('fable-method', platform)] }, 'non-Codex Method bundles stay byte invariant')
  codex_dir = File.join(tmp, 'fable-method/platforms/codex/fable-method')
  launcher = File.join(codex_dir, 'scripts/task_checkpoint.rb')
  companion = File.join(codex_dir, 'scripts/paused_run_continuation.rb')
  launcher_bytes = File.binread(launcher)
  companion_bytes = File.binread(companion)
  artifact_controls = File.join(codex_dir, 'scripts/fable_artifact_controls.py')
  artifact_controls_bytes = File.binread(artifact_controls)
  check.call(Digest::SHA1.hexdigest("blob #{launcher_bytes.bytesize}\0#{launcher_bytes}") == '0851b2fa877cb3f9692eb0ef84b543caa9088ccf' && (File.stat(launcher).mode & 0o777) == 0o755, 'Codex launcher bytes and executable mode match the source pin')
  check.call(Digest::SHA256.hexdigest(companion_bytes) == 'f84247658a111c433252d0dbe6336fd12ac22bbd7ce9aaae8425a7154fa1b1a0' && (File.stat(companion).mode & 0o777) == 0o644, 'companion bytes and mode match the versioned runtime asset pin')
  check.call(Digest::SHA256.hexdigest(artifact_controls_bytes) == '1682632ec89566d50f40f3484cd618ce62c92d1cdf40be1f0ef1741cb6416195' && (File.stat(artifact_controls).mode & 0o777) == 0o644, 'artifact controls bytes and mode match the declared helper asset')
  skill_pin = File.read(File.join(codex_dir, 'SKILL.md')).match(/is pinned to Git blob `([0-9a-f]{40})` from commit/)
  check.call(skill_pin && skill_pin[1] == '0851b2fa877cb3f9692eb0ef84b543caa9088ccf', 'generated Codex SKILL.md pin matches the launcher blob')
  check.call(File.read(File.join(codex_dir, 'SKILL.md')).include?('artifact_controls.py') && File.read(File.join(codex_dir, 'SKILL.md')).include?('Artifact budgets and worktree lifecycle'), 'generated Codex skill includes the lifecycle instructions and helper')
  check.call(run.call('--check', '--skill', 'fable-method')[0], 'complete Method materialization passes NO_DRIFT check')
  method_after = digest.call('fable-method')
  check.call(run.call('--write', '--skill', 'fable-method')[0] && method_after == digest.call('fable-method'), 'Codex runtime output is reproducible across writes')

  helper_source = File.join(tmp, 'fable-method/scripts/paused_run_continuation.rb')
  helper_bytes = File.binread(helper_source)
  before_refusal = digest.call('fable-method')
  File.unlink(helper_source)
  result = run.call('--write', '--skill', 'fable-method')
  check.call(!result[0] && result[1].include?('missing path') && before_refusal == digest.call('fable-method'), 'missing required companion fails closed before any platform write')
  File.binwrite(helper_source, helper_bytes)
  File.open(helper_source, 'ab') { |file| file.write("\nasset mismatch\n") }
  result = run.call('--write', '--skill', 'fable-method')
  check.call(!result[0] && result[1].include?('runtime asset Git blob mismatch') && before_refusal == digest.call('fable-method'), 'mismatched runtime asset fails closed before any platform write')
  File.binwrite(helper_source, helper_bytes)
  artifact_controls_source = File.join(tmp, 'fable-method/scripts/fable_artifact_controls.py')
  artifact_controls_bytes = File.binread(artifact_controls_source)
  File.open(artifact_controls_source, 'ab') { |file| file.write("\nasset mismatch\n") }
  result = run.call('--write', '--skill', 'fable-method')
  check.call(!result[0] && result[1].include?('runtime asset SHA-256 mismatch') && before_refusal == digest.call('fable-method'), 'mismatched artifact controls fail closed before any platform write')
  File.binwrite(artifact_controls_source, artifact_controls_bytes)
  2.times { check.call(run.call('--write', '--skill', 'fable-judge')[0], 'Judge generation succeeds') }
  check.call(judge_before == digest.call('fable-judge'), 'four Judge bundles deterministic after second write')
  check.call(run.call('--check', '--skill', 'fable-judge')[0], 'Judge second-generation NO_DRIFT')
  File.open(File.join(tmp, 'fable-judge/shared/SKILL.md'), 'a') { |f| f.write("\nJudge fixture change.\n") }
  check.call(run.call('--write', '--skill', 'fable-judge')[0], 'Judge-only source change generates')
  check.call(method_after == digest.call('fable-method'), 'Judge-only operation leaves Method unchanged')
  judge_changed = digest.call('fable-judge')
  method_source = File.join(tmp, 'fable-method/shared/SKILL.md')
  method_source_before = File.binread(method_source)
  method_source_after = method_source_before.sub("# The Fable Method\n", "# The Fable Method (fixture)\n")
  raise 'Method fixture mutation did not change source bytes' if method_source_after == method_source_before
  File.binwrite(method_source, method_source_after)
  check.call(run.call('--write', '--skill', 'fable-method')[0], 'Method-only source change generates')
  check.call(judge_changed == digest.call('fable-judge') && method_after != digest.call('fable-method'), 'Method-only change leaves Judge bundle unchanged')
  projection = File.join(tmp, 'fable-method/shared/references/judge-handoff.md')
  File.write(projection, File.read(projection).sub("## Depth and evidence reuse\n", "## Depth and evidence reuse\n\nDeclared projection change.\n"))
  check.call(run.call('--write', '--skill', 'fable-judge')[0] && digest.call('fable-judge') != judge_changed, 'declared shared projection change affects Judge')
  stale = File.join(tmp, 'fable-method/platforms/codex/fable-judge/stale')
  sibling = File.join(tmp, 'fable-method/platforms/codex/fable-method/stale')
  File.write(stale, 'stale')
  File.write(sibling, 'sibling sentinel')
  check.call(run.call('--write', '--skill', 'fable-judge')[0], 'selected stale cleanup succeeds')
  check.call(!File.exist?(stale) && File.read(sibling) == 'sibling sentinel', 'cleanup stays inside selected skill')
  source = File.join(tmp, 'fable-judge/shared/platforms/gemini/frontmatter.md')
  saved = File.read(source)
  File.unlink(source)
  File.symlink(File.join(tmp, 'fable-judge/shared/platforms/codex/frontmatter.md'), source)
  before = digest.call('fable-judge')
  result = run.call('--write', '--skill', 'fable-judge')
  check.call(!result[0] && result[1].include?('symlink') && digest.call('fable-judge') == before, 'source symlink rejects whole selected batch before writes')
  File.unlink(source)
  File.write(source, saved)
  link = File.join(tmp, 'fable-method/platforms/gemini/fable-judge/link')
  File.symlink(sibling, link)
  result = run.call('--write', '--skill', 'fable-judge')
  check.call(!result[0] && result[1].include?('symlink') && File.symlink?(link), 'destination symlink rejected and preserved')
  File.unlink(link)
  path = File.join(tmp, 'fable-method/platforms/gemini/fable-judge/SKILL.md')
  File.unlink(path)
  Dir.mkdir(path)
  sentinel = File.join(tmp, 'fable-method/platforms/codex/fable-judge/SKILL.md')
  File.write(sentinel, 'must survive refusal')
  check.call(!run.call('--write', '--skill', 'fable-judge')[0], 'wrong-type managed file rejected')
  check.call(File.read(sentinel) == 'must survive refusal', 'later wrong-type rejects before earlier platform comparison/write')
  Dir.rmdir(path)
  references = File.join(tmp, 'fable-method/platforms/gemini/fable-judge/references')
  FileUtils.rm_r(references)
  File.write(references, 'wrong-type directory sentinel')
  check.call(!run.call('--write', '--skill', 'fable-judge')[0], 'expected directory as file rejected')
  check.call(File.read(references) == 'wrong-type directory sentinel' && File.read(sentinel) == 'must survive refusal', 'wrong-type directory and earlier platform preserved')
  [['--skill', 'all'], ['--skill', 'unknown'], ['--skill', 'fable-judge', '--skill', 'fable-judge']].each do |args|
    check.call(!run.call('--check', *args)[0], "closed selector #{args.join(' ')}")
  end
end
puts "PASS: #{count} sync cases"
RUBY
