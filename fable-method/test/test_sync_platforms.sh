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
  method_before = digest.call('fable-method')
  judge_before = digest.call('fable-judge')
  implicit = run.call('--check')
  explicit = run.call('--check', '--skill', 'fable-method')
  check.call(implicit[0] && implicit == explicit, 'legacy implicit and explicit Method CLI are identical')
  check.call(run.call('--write')[0], 'Method write succeeds')
  check.call(method_before == digest.call('fable-method'), 'four Method bundles stay byte invariant')
  2.times { check.call(run.call('--write', '--skill', 'fable-judge')[0], 'Judge generation succeeds') }
  check.call(judge_before == digest.call('fable-judge'), 'three Judge bundles deterministic after second write')
  check.call(run.call('--check', '--skill', 'fable-judge')[0], 'Judge second-generation NO_DRIFT')
  File.open(File.join(tmp, 'fable-judge/shared/SKILL.md'), 'a') { |f| f.write("\nJudge fixture change.\n") }
  check.call(run.call('--write', '--skill', 'fable-judge')[0], 'Judge-only source change generates')
  check.call(method_before == digest.call('fable-method'), 'Judge-only operation leaves Method unchanged')
  judge_changed = digest.call('fable-judge')
  File.open(File.join(tmp, 'fable-method/shared/SKILL.md'), 'a') { |f| f.write("\nMethod fixture change.\n") }
  check.call(run.call('--write', '--skill', 'fable-method')[0], 'Method-only source change generates')
  check.call(judge_changed == digest.call('fable-judge'), 'Method-only change leaves Judge body unchanged')
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
