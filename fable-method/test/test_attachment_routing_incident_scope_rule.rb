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
  ROUTE_POSITION_RULE = <<~TEXT.chomp
    Emit one routing block; copy the Packet's values when it supplies them. Its
    position relative to loading this Skill, reading the Packet, or reading
    project rules does not matter and is never an incident.
  TEXT
  ATTACHMENT_RULE = <<~TEXT.chomp
    When the Packet arrives only as an attached or pasted-text file (for example
    a Codex `/goal` attachment), read the complete Task Packet; if the platform
    requires `get_goal`, call it. Read the Packet-named project rules and any
    needed Skill references before acting.
  TEXT
  INCIDENT_RULE = <<~TEXT.chomp
    - Lint, format, or test failures found and fixed during implementation, before
      final verification, are normal development and not incidents. Use
      `INCIDENT` only for a stop, block, abort, failed final acceptance, or a
      problem that remains or changed a result.
  TEXT
  REQUIRED_TEXT = [
    'read the complete Task Packet; if the platform',
    'is never an incident',
    'Read the Packet-named project rules',
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

      route_position_pattern = Regexp.new(Regexp.escape(ROUTE_POSITION_RULE))
      attachment_pattern = Regexp.new(Regexp.escape(ATTACHMENT_RULE))
      incident_pattern = Regexp.new(Regexp.escape(INCIDENT_RULE))
      assert_equal 1, skill.scan(route_position_pattern).length, "#{relative_path} repeats the route position rule"
      assert_operator first_output, :<, skill.index(ROUTE_POSITION_RULE), relative_path
      assert_operator skill.index(ROUTE_POSITION_RULE), :<, skill.index(ATTACHMENT_RULE), relative_path
      [
        'this Skill, source code',
        'Before any tool call, emit one routing block',
        'Before any task tool call, emit one routing block',
        'before that block',
        'Loading this Skill and reading the Packet or its'
      ].each do |old_wording|
        refute_includes skill, old_wording, "#{relative_path} retains the old route-order wording #{old_wording.inspect}"
      end
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
