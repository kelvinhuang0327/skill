# frozen_string_literal: true

require 'minitest/autorun'

class AttachmentRoutingIncidentScopeRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  ATTACHMENT_RULE = <<~TEXT.chomp
    When the Packet arrives only as an attached or pasted-text file (for example
    a Codex `/goal` attachment), read the complete Task Packet first. If the
    platform requires `get_goal`, call it next; then immediately emit the routing
    block. Do not read `AGENTS.md`, this Skill, source code, or make any other
    task tool call before that block. After emitting it, read the Packet-named
    project rules and any needed Skill references before acting. That order is
    expected and is not an incident.
  TEXT
  INCIDENT_RULE = <<~TEXT.chomp
    - Lint, format, or test failures found and fixed during implementation, before
      final verification, are normal development and not incidents. Use
      `INCIDENT` only for a stop, block, abort, failed final acceptance, or a
      problem that remains or changed a result.
  TEXT
  REQUIRED_TEXT = [
    'read the complete Task Packet first',
    'Do not read `AGENTS.md`, this Skill, source code',
    'After emitting it, read the Packet-named',
    'are normal development and not incidents',
    'problem that remains or changed a result'
  ].freeze

  def test_attachment_routing_and_incident_scope_rules_appear_once_in_their_sections
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      first_output = skill.index("## First output and task class\n")
      first_text_fence = skill.index("```text\n", first_output)
      receipt = skill.index("### Task receipt\n")
      explicit_triggers = skill.index("## Explicit Packet triggers\n")

      refute_nil first_output, "#{relative_path} is missing ## First output and task class"
      refute_nil first_text_fence, "#{relative_path} is missing the routing block"
      refute_nil receipt, "#{relative_path} is missing ### Task receipt"
      refute_nil explicit_triggers, "#{relative_path} is missing ## Explicit Packet triggers"

      attachment_pattern = Regexp.new(Regexp.escape(ATTACHMENT_RULE))
      incident_pattern = Regexp.new(Regexp.escape(INCIDENT_RULE))
      assert_equal 1, skill.scan(attachment_pattern).length, "#{relative_path} repeats the attachment rule"
      assert_equal 1, skill.scan(incident_pattern).length, "#{relative_path} repeats the incident rule"
      assert_operator first_output, :<, skill.index(ATTACHMENT_RULE), relative_path
      assert_operator skill.index(ATTACHMENT_RULE), :<, first_text_fence, relative_path
      assert_operator receipt, :<, skill.index(INCIDENT_RULE), relative_path
      assert_operator skill.index(INCIDENT_RULE), :<, explicit_triggers, relative_path

      REQUIRED_TEXT.each do |text|
        assert_includes skill, text, "#{relative_path} is missing #{text.inspect}"
      end
    end
  end
end
