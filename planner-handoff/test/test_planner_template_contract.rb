# frozen_string_literal: true

require 'minitest/autorun'
require 'open3'
require 'rbconfig'
require 'tmpdir'

class PlannerTemplateContractTest < Minitest::Test
  REPOSITORY_ROOT = File.expand_path('../..', __dir__)
  TEMPLATE_ROOT = ENV.fetch('PLANNER_TEMPLATE_CONTRACT_TEMPLATE_ROOT', REPOSITORY_ROOT)
  TEMPLATES = {
    'prompt template' => File.join(
      TEMPLATE_ROOT,
      'prompt/Personal_Planner_Handoff_Prompt_v5.5.1_Lean_Final.md'
    ),
    'reference template' => File.join(
      TEMPLATE_ROOT,
      'planner-handoff/references/planner-handoff-v5.5.1.md'
    )
  }.freeze
  ROUTING_ENUMS = %w[
    TASK_CLASS
    WORKER_ROUTE
    IMPLEMENTATION_DEPTH
    JUDGE_MODE
  ].freeze
  PACKET_FIELD_ORDER = %w[
    TASK_CLASS
    WORKER_ROUTE
    JUDGE_MODE
    IMPLEMENTATION_DEPTH
    IMPLEMENTATION_DEPTH_REASON
    SIGNAL_POLICY
    CONTINUES_FROM
  ].freeze
  PASS_CASES = [
    {
      name: 'state-changing FAST/NORMAL',
      fields: {
        'TASK_CLASS' => 'STATE_CHANGING_IMPLEMENTATION',
        'WORKER_ROUTE' => 'FAST',
        'JUDGE_MODE' => 'NOT_APPLICABLE',
        'IMPLEMENTATION_DEPTH' => 'NORMAL'
      }
    },
    {
      name: 'state-changing STANDARD/ENHANCED',
      fields: {
        'TASK_CLASS' => 'STATE_CHANGING_IMPLEMENTATION',
        'WORKER_ROUTE' => 'STANDARD',
        'JUDGE_MODE' => 'NOT_APPLICABLE',
        'IMPLEMENTATION_DEPTH' => 'ENHANCED'
      }
    },
    {
      name: 'state-changing STANDARD_JUDGED/FRESH_CONTEXT',
      fields: {
        'TASK_CLASS' => 'STATE_CHANGING_IMPLEMENTATION',
        'WORKER_ROUTE' => 'STANDARD_JUDGED',
        'JUDGE_MODE' => 'FRESH_CONTEXT',
        'IMPLEMENTATION_DEPTH' => 'NORMAL'
      }
    },
    {
      name: 'state-changing LOOP_JUDGED/SELF_CHECK_ONLY',
      fields: {
        'TASK_CLASS' => 'STATE_CHANGING_IMPLEMENTATION',
        'WORKER_ROUTE' => 'LOOP_JUDGED',
        'JUDGE_MODE' => 'SELF_CHECK_ONLY',
        'IMPLEMENTATION_DEPTH' => 'NORMAL'
      }
    },
    {
      name: 'read-only completion review',
      fields: {
        'TASK_CLASS' => 'READ_ONLY_COMPLETION_REVIEW',
        'WORKER_ROUTE' => 'NOT_APPLICABLE',
        'JUDGE_MODE' => 'NOT_APPLICABLE'
      }
    },
    {
      name: 'planning only',
      fields: {
        'TASK_CLASS' => 'PLANNING_ONLY',
        'WORKER_ROUTE' => 'NOT_APPLICABLE',
        'JUDGE_MODE' => 'NOT_APPLICABLE'
      }
    },
    {
      name: 'pure QA',
      fields: {
        'TASK_CLASS' => 'PURE_QA',
        'WORKER_ROUTE' => 'NOT_APPLICABLE',
        'JUDGE_MODE' => 'NOT_APPLICABLE'
      }
    }
  ].freeze

  def test_t1_packet_template_section_matches_between_both_templates
    sections = TEMPLATES.map do |name, path|
      [name, section_between(read_template(name, path, 'T1'), /^## 9\b/, /^## 10\b/, "#{name}: T1")]
    end.to_h

    assert_equal(
      sections.fetch('reference template'),
      sections.fetch('prompt template'),
      'prompt template and reference template: T1 §9 packet template must match byte-for-byte through the start of §10'
    )
  end

  def test_t2_allowed_scope_rules_are_present_once_inside_section_9
    TEMPLATES.each do |name, path|
      section = section_between(read_template(name, path, 'T2'), /^## 9\b/, /^## 10\b/, "#{name}: T2")
      reads = section.lines.select { |line| line.start_with?('ALLOWED_READS: <') }
      writes = section.lines.select { |line| line.start_with?('ALLOWED_WRITES: <') }

      assert_equal(1, reads.length, "#{name}: T2 §9 must contain exactly one ALLOWED_READS row")
      assert_includes(reads.first.to_s, '不可省略', "#{name}: T2 ALLOWED_READS must require non-omission")
      assert_includes(
        reads.first.to_s,
        '步驟與驗收必然需要的讀取',
        "#{name}: T2 ALLOWED_READS must cover reads required by the steps and acceptance"
      )
      assert_equal(1, writes.length, "#{name}: T2 §9 must contain exactly one ALLOWED_WRITES row")
      assert_includes(writes.first.to_s, '附帶寫入', "#{name}: T2 ALLOWED_WRITES must cover incidental writes")
    end
  end

  def test_t3_packet_fields_appear_once_in_canonical_order
    TEMPLATES.each do |name, path|
      section = section_between(read_template(name, path, 'T3'), /^## 9\b/, /^## 10\b/, "#{name}: T3")
      positions = PACKET_FIELD_ORDER.map do |field|
        matches = section.lines.each_index.select do |index|
          section.lines[index].match?(/^#{Regexp.escape(field)}:/)
        end
        assert_equal(1, matches.length, "#{name}: T3 §9 must contain #{field} exactly once")
        matches.first
      end

      assert_equal(
        positions.compact.sort,
        positions.compact,
        "#{name}: T3 §9 fields must be ordered #{PACKET_FIELD_ORDER.join(', ')}"
      )
    end
  end

  def test_t4_section_6_enums_have_passing_validator_packets_and_rejected_counterexamples
    TEMPLATES.each do |name, path|
      section = section_between(read_template(name, path, 'T4'), /^## 6\b/, /^## 7\b/, "#{name}: T4")
      enums = ROUTING_ENUMS.to_h do |field|
        rows = section.lines.select { |line| line.match?(/^#{Regexp.escape(field)}:/) }
        assert_equal(1, rows.length, "#{name}: T4 §6 must define #{field} exactly once")
        match = rows.first&.match(/^#{Regexp.escape(field)}:\s*(.+?)\s*$/)
        values = match ? match[1].split('|').map(&:strip) : []
        refute_empty(values, "#{name}: T4 §6 #{field} enum must contain values")
        [field, values]
      end

      covered = ROUTING_ENUMS.to_h { |field| [field, []] }
      PASS_CASES.each do |pass_case|
        pass_case.fetch(:fields).each do |field, value|
          assert_includes(
            enums.fetch(field),
            value,
            "#{name}: T4 #{pass_case.fetch(:name)} uses #{field}=#{value}, which must come from the §6 enum"
          )
          covered.fetch(field) << value
        end
      end
      ROUTING_ENUMS.each do |field|
        enums.fetch(field).each do |value|
          assert_includes(
            covered.fetch(field),
            value,
            "#{name}: T4 §6 #{field} enum value #{value} needs a passing validator packet"
          )
        end
      end

      PASS_CASES.each do |pass_case|
        stdout, stderr, status = run_validator(packet_for(pass_case.fetch(:fields)))
        assert(
          status.success? && stdout.lines.first&.strip == 'ROUTING_STRUCTURE_PASS',
          "#{name}: T4 #{pass_case.fetch(:name)} must pass explicit_contract.rb; stdout=#{stdout.inspect} stderr=#{stderr.inspect} exit=#{status.exitstatus}"
        )
      end

      rejected_cases = [
        {
          name: 'unknown TASK_CLASS',
          fields: {
            'TASK_CLASS' => 'NOT_A_CLASS',
            'WORKER_ROUTE' => 'NOT_APPLICABLE',
            'JUDGE_MODE' => 'NOT_APPLICABLE'
          }
        },
        {
          name: 'FAST with ENHANCED depth',
          fields: {
            'TASK_CLASS' => 'STATE_CHANGING_IMPLEMENTATION',
            'WORKER_ROUTE' => 'FAST',
            'JUDGE_MODE' => 'NOT_APPLICABLE',
            'IMPLEMENTATION_DEPTH' => 'ENHANCED'
          }
        }
      ]
      rejected_cases.each do |rejected_case|
        stdout, stderr, status = run_validator(packet_for(rejected_case.fetch(:fields)))
        refute(
          status.success? && stdout.lines.first&.strip == 'ROUTING_STRUCTURE_PASS',
          "#{name}: T4 #{rejected_case.fetch(:name)} must not pass explicit_contract.rb; stdout=#{stdout.inspect} stderr=#{stderr.inspect} exit=#{status.exitstatus}"
        )
      end
    end
  end

  private

  def read_template(name, path, rule)
    File.read(path, encoding: 'UTF-8')
  rescue SystemCallError, IOError, EncodingError => error
    flunk("#{name}: #{rule} template must be readable as UTF-8 at #{path}: #{error.message}")
  end

  def section_between(text, start_heading, end_heading, failure_context)
    lines = text.lines
    start_index = lines.index { |line| start_heading.match?(line) }
    end_index = start_index && lines.each_index.find do |index|
      index > start_index && end_heading.match?(lines[index])
    end
    assert(start_index && end_index, "#{failure_context}: required section headings must exist")
    return '' unless start_index && end_index

    lines[start_index...end_index].join
  end

  def packet_for(fields)
    lines = ['TASK_ID: planner-template-contract-test']
    PACKET_FIELD_ORDER.each do |field|
      if fields.key?(field)
        lines << "#{field}: #{fields.fetch(field)}"
      elsif field == 'IMPLEMENTATION_DEPTH_REASON' && fields.key?('IMPLEMENTATION_DEPTH')
        lines << 'IMPLEMENTATION_DEPTH_REASON: test case'
      elsif field == 'SIGNAL_POLICY'
        lines << 'SIGNAL_POLICY: ALWAYS'
      elsif field == 'CONTINUES_FROM'
        lines << 'CONTINUES_FROM: NONE'
      end
    end
    lines.join("\n") + "\n"
  end

  def run_validator(packet)
    validator = File.join(REPOSITORY_ROOT, 'fable-method/scripts/explicit_contract.rb')
    Dir.mktmpdir('planner-template-contract-packet-') do |directory|
      packet_path = File.join(directory, 'packet.md')
      File.write(packet_path, packet, encoding: 'UTF-8')
      Open3.capture3(
        RbConfig.ruby,
        validator,
        '--check-routing-packet',
        packet_path,
        chdir: REPOSITORY_ROOT
      )
    end
  end
end
