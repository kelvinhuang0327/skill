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
      assert_includes(
        writes.first.to_s,
        '依本 Packet 實際宣告的 PERSISTENT_EVIDENCE_ROOT 絕對路徑',
        "#{name}: T2 ALLOWED_WRITES must use the packet's declared evidence root"
      )
      assert_includes(
        writes.first.to_s,
        '必須將 packet.md、result.json 與 final_reply.md 展開為該根目錄下的完整可寫路徑並明列於本 ALLOWED_WRITES 欄',
        "#{name}: T2 receipt paths must be explicitly listed in ALLOWED_WRITES"
      )
      %w[packet.md result.json final_reply.md].each do |receipt|
        assert_includes(
          writes.first.to_s,
          receipt,
          "#{name}: T2 ALLOWED_WRITES must include the #{receipt} receipt"
        )
      end
      assert_includes(
        writes.first.to_s,
        '不得只註明「另行允許」',
        "#{name}: T2 ALLOWED_WRITES must not replace explicit receipt paths with a separate allowance"
      )
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

  def test_t5_required_verification_does_not_promote_static_review_to_runtime_acceptance
    name = 'prompt template'
    prompt = read_template(name, TEMPLATES.fetch(name), 'T5')
    section = section_between(prompt, /^## 強制驗收標準$/, /^## Worker 必要回覆$/, "#{name}: T5")
    verification_rules = section.lines.select { |line| line.match?(/^- REQUIRED_VERIFICATION[:：]/) }

    assert_equal(1, verification_rules.length, "#{name}: T5 must contain exactly one REQUIRED_VERIFICATION row")
    assert_includes(
      verification_rules.first.to_s,
      'STATE_CHANGING_IMPLEMENTATION 必須列出最窄 acceptance command(s)',
      "#{name}: T5 must require a narrow runtime acceptance command"
    )
    assert_includes(
      verification_rules.first.to_s,
      '`STATICALLY_ACCEPTED_RUNTIME_UNVERIFIED`',
      "#{name}: T5 must mark runtime checks that were not run"
    )
    assert_includes(
      verification_rules.first.to_s,
      'static review 不可代替 runtime acceptance PASS',
      "#{name}: T5 must keep static review separate from runtime acceptance"
    )

    fields = {
      'TASK_CLASS' => 'STATE_CHANGING_IMPLEMENTATION',
      'WORKER_ROUTE' => 'STANDARD',
      'JUDGE_MODE' => 'NOT_APPLICABLE',
      'IMPLEMENTATION_DEPTH' => 'NORMAL'
    }
    runtime_command = 'ruby -Itest planner-handoff/test/test_planner_template_contract.rb --name /test_t5_required_verification_does_not_promote_static_review_to_runtime_acceptance/'
    samples = [
      [
        'runtime acceptance command',
        packet_for(fields) + "REQUIRED_VERIFICATION: `#{runtime_command}`\n",
        runtime_command
      ],
      [
        'runtime not run',
        packet_for(fields) + "REQUIRED_VERIFICATION: static review PASS; runtime NOT RUN; STATICALLY_ACCEPTED_RUNTIME_UNVERIFIED\n",
        nil
      ]
    ]

    samples.each do |sample_name, packet, expected_command|
      verification = packet.lines.find { |line| line.start_with?('REQUIRED_VERIFICATION:') }.to_s
      stdout, stderr, status = run_validator(packet)
      assert(
        status.success? && stdout.lines.first&.strip == 'ROUTING_STRUCTURE_PASS' &&
          stdout.include?('SEMANTIC_LIMITATIONS: This checks explicit routing structure only.') &&
          !stdout.include?('RUNTIME_ACCEPTANCE_PASS'),
        "#{sample_name}: explicit_contract.rb must not report routing validation as runtime acceptance; stdout=#{stdout.inspect} stderr=#{stderr.inspect} exit=#{status.exitstatus}"
      )

      if expected_command
        assert_includes(verification, expected_command, "#{sample_name}: REQUIRED_VERIFICATION must preserve the exact command")
      else
        assert_includes(verification, 'runtime NOT RUN', "#{sample_name}: runtime must remain explicitly unverified")
        assert_includes(verification, 'STATICALLY_ACCEPTED_RUNTIME_UNVERIFIED', "#{sample_name}: static review must not imply runtime acceptance")
        refute_match(/runtime acceptance\s*:\s*PASS/i, verification, "#{sample_name}: static review alone cannot be a runtime pass")
      end
    end

  end

  def test_historical_ranking_guidance_uses_calendar_dates_and_qualifies_portfolio_rates
    name = 'reference template'
    guidance = section_between(
      read_template(name, TEMPLATES.fetch(name), 'H1'),
      /^### 歷史回測與策略排行封包$/,
      /^### Worker 結案與專案目標續接$/,
      "#{name}: H1"
    )

    assert_match(/曆年.{0,35}日期欄位.{0,50}target_draw_date|target_draw_date.{0,50}曆年/im,
                 guidance, 'H1 must tie calendar-year grouping to the source date field')
    assert_match(/target_draw_date/, guidance, 'H1 must name the source date field')
    assert_match(/target_draw_id.{0,30}(?:開獎)?識別/m, guidance, 'H1 must reserve draw ID for identity')
    assert_match(/不以 ID 前綴.{0,20}推算年份/m, guidance, 'H1 must reject year inference from draw ID prefixes')
    assert_match(/(?:計分結果|outcome).{0,35}(?:分母|denominator)|(?:分母|denominator).{0,35}(?:計分結果|outcome)/im,
                 guidance, 'H1 must define the outcome and denominator')
    assert_match(/票券數|票數|ticket count/i, guidance, 'H1 must report ticket exposure')
    assert_match(/總成本|成本.*exposure|cost exposure/i, guidance, 'H1 must report cost exposure')
    rate_sentence = guidance.split(/[。！？.!?]/).find do |sentence|
      sentence.match?(/portfolio.*成功率/i) && sentence.match?(/未標準化|unnormalized/i)
    end
    refute_nil(rate_sentence, 'H1 must state the unnormalized condition in the portfolio-rate guidance')
    assert_match(/資料不可得|資料缺失|unavailable/i, rate_sentence.to_s,
                 'H1 must tie the unnormalized label to unavailable exposure')
    assert_match(/(?:避免|不得|不應|不能).{0,60}(?:宣稱|作|比較).{0,40}(?:經濟優越|經濟|可比排名|comparable)/im,
                 rate_sentence.to_s,
                 'H1 must caveat unnormalized rates against economic or comparable-success claims')
  end

  def test_historical_ranking_guidance_uses_chronological_holdout_and_marks_reuse_exploratory
    name = 'reference template'
    guidance = section_between(
      read_template(name, TEMPLATES.fetch(name), 'H2'),
      /^### 歷史回測與策略排行封包$/,
      /^### Worker 結案與專案目標續接$/,
      "#{name}: H2"
    )

    assert_match(
      /`target_draw_date`.{0,20}實際日期排序.{0,40}較早.{0,20}(?:開發|選擇).{0,80}較晚.{0,100}不以 draw-id 前綴判斷先後/im,
      guidance,
      'H2 must order earlier selection and later draws by actual target dates, not draw ID prefixes'
    )
    assert_match(
      /較晚.{0,40}未參與任何選擇的資料作保留評估.{0,50}rolling-origin 證據/im,
      guidance,
      'H2 must require untouched later evaluation or rolling-origin evidence'
    )
    assert_match(
      /若評估期 outcomes 曾影響上述選擇.{0,60}只能標為探索性.{0,40}不得宣稱樣本外確認/im,
      guidance,
      'H2 must label evaluation-history reuse for selection exploratory and bar confirmatory claims'
    )
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
