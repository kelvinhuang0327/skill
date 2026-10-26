# frozen_string_literal: true

require 'minitest/autorun'
require 'minitest/mock'
require 'open3'
require 'shellwords'
require_relative '../scripts/task_checkpoint'

# Classifier-only tests for the low-load local runtime exemption in
# HeavyScientificLease.legacy_heavy_writers_once. Every process row is
# synthetic; /bin/ps is stubbed, so no lease or lock is touched.
class HeavyWriterLocalRuntimeExemptionTest < Minitest::Test
  PYTHON = '/opt/synthetic/bin/python3.13'
  UV = '/opt/synthetic/bin/uv'
  NODE = '/opt/synthetic/bin/node'
  REPOSITORY = '/work/.task-data/SYNTHETIC_UI_TASK/repo'
  TOKEN = '0123456789abcdef0123456789abcdef'
  SOURCE = '89abcdef0123456789abcdef0123456789abcdef'

  def launcher_argv(role, cwd, child)
    [PYTHON, '-m', 'lottolab.infrastructure.local_runtime', '_launch',
     '--role', role, '--token', TOKEN, '--repository', REPOSITORY,
     '--source-commit', SOURCE, '--cwd', cwd, '--'] + child
  end

  def backend_child
    [UV, 'run', '--no-sync', 'uvicorn', '--factory',
     'lottolab.interfaces.api.local_app:create_local_app', '--app-dir', "#{REPOSITORY}/src",
     '--host', '127.0.0.1', '--port', '8000', '--header', "X-LottoLab-Owner:#{TOKEN}",
     '--no-access-log', '--log-level', 'warning']
  end

  def frontend_child
    [NODE, "#{REPOSITORY}/frontend/node_modules/vite/bin/vite.js",
     '--host', '127.0.0.1', '--port', '5173', '--strictPort',
     '--config', "#{REPOSITORY}/frontend/vite.config.ts"]
  end

  # Rows 0..2 match the commands captured by the Horizon and EH09 STOPs:
  # backend launcher, its Uvicorn worker, frontend launcher (all 0.0% CPU).
  # With bridge: true, row 3 is the uv process between launcher and Uvicorn.
  def runtime_rows(bridge: false)
    ids = (1..4).map { |i| Process.pid + 1_000_000 + i }
    uvicorn = [PYTHON, "#{REPOSITORY}/.venv/bin/uvicorn"] + backend_child[4..-1]
    rows = [
      [ids[0], 1, 0.0, 0.1, 'S', Shellwords.join(launcher_argv('backend', REPOSITORY, backend_child))],
      [ids[1], bridge ? ids[3] : ids[0], 0.0, 0.3, 'S', Shellwords.join(uvicorn)],
      [ids[2], 1, 0.0, 0.1, 'S', Shellwords.join(launcher_argv('frontend', "#{REPOSITORY}/frontend", frontend_child))]
    ]
    rows << [ids[3], ids[0], 0.0, 0.0, 'S', Shellwords.join(backend_child)] if bridge
    rows
  end

  def row(argv, cpu: 0.0, parent: 1, pid: Process.pid + 2_000_000, state: 'S')
    [pid, parent, cpu, 0.0, state, Shellwords.join(argv)]
  end

  def classify(rows)
    text = rows.map { |r| r.join(' ') }.join("\n") + "\n"
    status = Object.new
    def status.success?; true; end
    snapshot = lambda do |*argv|
      assert_equal ['/bin/ps', '-ww', '-axo', 'pid=,ppid=,%cpu=,%mem=,state=,command='], argv
      [text, status]
    end
    Open3.stub(:capture2, snapshot) { HeavyScientificLease.legacy_heavy_writers_once }
  end

  def mutate_command(row)
    argv = Shellwords.split(row[5])
    yield argv
    row[5] = Shellwords.join(argv)
  end

  def test_low_load_local_runtime_is_not_a_writer
    assert_empty classify(runtime_rows)
    assert_empty classify(runtime_rows(bridge: true))
  end

  def test_unknown_low_cpu_python_still_blocks
    writers = classify([row(['/usr/bin/python3', '/work/.task-data/UNKNOWN/worker.py'])])
    assert_equal 1, writers.length
    assert_equal 'UNKNOWN', writers[0]['TASK_ID']
  end

  def test_actual_research_worker_blocks_even_while_idle
    [0.0, 0.1, 99.0].each do |cpu|
      writers = classify([row(['/usr/bin/python3', '-m', 'lottolab.research.backtest'], cpu: cpu)])
      assert_equal 1, writers.length
    end
  end

  def test_native_scientific_engines_and_high_cpu_unknown_binary_remain_blocked
    [%w[/tools/tbr_engine], %w[/tools/ldr_engine],
     %w[/usr/bin/python3 /tools/exact_score.py],
     %w[/tools/custom /work/.task-data/OTHER_TASK/input]].each do |argv|
      assert_equal 1, classify([row(argv, cpu: 8.0)]).length
    end
  end

  def test_high_cpu_runtime_retains_gate_for_each_role
    3.times do |index|
      rows = runtime_rows
      rows[index][2] = 5.0
      assert_includes classify(rows).map { |e| e['PID'] }, rows[index][0]
    end
  end

  def test_invalid_runtime_owner_role_source_cwd_or_command_does_not_exempt
    [
      ->(a) { a[5] = 'research' },
      ->(a) { a[7] = 'unknown-owner' },
      ->(a) { a[11] = 'unknown-source' },
      ->(a) { a[13] += '/elsewhere' },
      ->(a) { a[9] += '/../repo' },
      ->(a) { a << '--unknown-option' },
      ->(a) { a[a.index('--app-dir') + 1] += '/other-task' },
      ->(a) { a[a.index('--header') + 1] = 'X-LottoLab-Owner:wrong' },
      ->(a) { a[2] = 'other.local_runtime' },
      ->(a) { a[15] = '/tools/python' }
    ].each do |mutation|
      rows = runtime_rows
      mutate_command(rows[0], &mutation)
      assert_includes classify(rows).map { |e| e['PID'] }, rows[0][0]
    end
  end

  def test_frontend_launcher_cannot_hide_an_extra_python_workload
    rows = runtime_rows
    mutate_command(rows[2]) { |a| a.concat(%w[python /work/.task-data/OTHER/research.py]) }
    assert_includes classify(rows).map { |e| e['PID'] }, rows[2][0]
  end

  def test_orphaned_uvicorn_is_not_exempt
    rows = runtime_rows
    assert_equal [rows[1][0]], classify([rows[1]]).map { |e| e['PID'] }
  end

  def test_uvicorn_requires_matching_owner_application_and_repository
    [
      ->(a) { a[a.index('--header') + 1] = 'X-LottoLab-Owner:wrong' },
      ->(a) { a[a.index('--factory') + 1] = 'research.api:app' },
      ->(a) { a[a.index('--app-dir') + 1] = '/work/.task-data/FOREIGN/repo/src' },
      ->(a) { a[1] = '/work/.task-data/FOREIGN/repo/.venv/bin/uvicorn' },
      ->(a) { a << '--workers=8' }
    ].each do |mutation|
      rows = runtime_rows
      mutate_command(rows[1], &mutation)
      assert_includes classify(rows).map { |e| e['PID'] }, rows[1][0]
    end
  end

  def test_uvicorn_requires_exact_low_load_parent_chain
    rows = runtime_rows(bridge: true)
    rows[3][2] = 6.0
    assert_includes classify(rows).map { |e| e['PID'] }, rows[1][0]
    rows = runtime_rows(bridge: true)
    mutate_command(rows[3]) { |a| a << '--unknown' }
    assert_includes classify(rows).map { |e| e['PID'] }, rows[1][0]
    rows = runtime_rows
    rows[1][1] = rows[2][0]
    assert_includes classify(rows).map { |e| e['PID'] }, rows[1][0]
  end

  def test_runtime_descendant_research_worker_never_inherits_exemption
    rows = runtime_rows
    worker = row(['/usr/bin/python3', '/work/.task-data/OTHER_TASK/research.py'], parent: rows[1][0])
    assert_equal [worker[0]], classify(rows + [worker]).map { |e| e['PID'] }
  end

  def test_ambiguous_or_malformed_process_identity_never_grants_exemption
    rows = runtime_rows
    rows[0][5] += ' "unterminated'
    assert_includes classify(rows).map { |e| e['PID'] }, rows[0][0]
    rows = runtime_rows
    assert_equal 4, classify(rows + [rows.first.dup]).length
  end
end
