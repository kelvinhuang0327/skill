# frozen_string_literal: true

require 'minitest/autorun'

class TaskReceiptRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  REQUIRED_TEXT = [
    'packet.md',
    '`task_id` key whose value',
    'copied exactly (same case)',
    '`IN_PROGRESS`',
    '`COMPLETED`',
    '`BLOCKED`',
    '`ABORTED`',
    'SIGNAL_POLICY',
    'process_signal',
    'UNNECESSARY_WORK',
    'Add no other key',
    '`status`',
    '`summary`',
    '`outcome`',
    '`signal_policy`',
    '`attempt`',
    '`/goal `',
    'is not an incident',
    'evidence-summary'
  ].freeze

  def test_worker_rule_read_contract_in_every_generated_entry
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path)).gsub(/\s+/, ' ')
      ["Worker's own cwd and context", 'actually read each applicable file',
       'SHA-256 of the bytes read', 'whether Git tracks it',
       'exact expected file or source ref', 'same-named main checkout file',
       'read the specified version if available', 'nonessential missing `AGENTS.md`',
       'does not prove platform auto-injection'].each do |requirement|
        assert_includes skill, requirement, relative_path
      end
    end
  end

  def test_task_receipt_rule_is_present_between_report_and_packet_triggers
    SKILLS.each do |relative_path|
      path = File.join(ROOT, relative_path)
      skill = File.read(path)
      report = skill.index("## Report\n")
      receipt = skill.index("### Task receipt\n")
      triggers = skill.index("## Explicit Packet triggers\n")

      refute_nil report, "#{relative_path} is missing ## Report"
      refute_nil receipt, "#{relative_path} is missing ### Task receipt"
      refute_nil triggers, "#{relative_path} is missing ## Explicit Packet triggers"
      assert_operator report, :<, receipt, relative_path
      assert_operator receipt, :<, triggers, relative_path
      assert_equal receipt, skill.rindex("### Task receipt\n"), relative_path

      REQUIRED_TEXT.each do |text|
        assert_includes skill, text, "#{relative_path} is missing #{text.inspect}"
      end
    end
  end
end
