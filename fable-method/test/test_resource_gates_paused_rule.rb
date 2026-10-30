# frozen_string_literal: true

require 'minitest/autorun'

class ResourceGatesPausedRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  BASES = ['fable-method/shared', *%w[claude codex gemini antigravity].map { |platform| "fable-method/platforms/#{platform}/fable-method" }].freeze
  RULE = <<~TEXT.chomp
    The machine-wide heavy-compute lease is paused. The launcher does not take the heavy slot or check other tasks'
    heavy processes unless `FABLE_HEAVY_SCIENTIFIC_LEASE=on` is set in its environment. Do not wait for another
    task's computation before starting your own; check only that no process writes to your own task's surfaces.
  TEXT

  def test_paused_lease_rule_occurs_once_in_shared_and_all_four_platforms
    BASES.each do |base|
      text = File.read(File.join(ROOT, base, 'SKILL.md')).gsub(/\s+/, ' ')
      assert_equal 1, text.scan(Regexp.new(Regexp.escape(RULE.gsub(/\s+/, ' ')))).length, base
    end
  end

  def test_resource_guidance_never_demands_a_budget_authorization
    BASES.each do |base|
      text = File.read(File.join(ROOT, base, 'references/operational-gates.md'))
      refute_includes text, 'RESOURCE_BUDGET_INCREASE_REQUIRED', base
      section = text.split("## Shared workstation resource budget\n", 2).last.split("\n## ", 2).first.gsub(/\s+/, ' ')
      assert_includes section, 'default to 2 CPU-heavy workers', base
      assert_includes section, 'Use the worker count specified by the Packet', base
      assert_includes section, "Never stop, wait, or request authorization because of the worker count, machine load, or another task's computation.", base
      assert_includes section, 'Record the actual worker count in the task receipt.', base
      refute_includes section, 'FORBIDDEN', base
      checkpoint = File.read(File.join(ROOT, base, 'references/task-checkpoint.md'))
      assert_includes checkpoint, '`FABLE_HEAVY_SCIENTIFIC_LEASE=on`', base
    end
  end

  def test_codex_disk_guidance_does_not_claim_low_space_blocks_launch
    text = File.read(File.join(ROOT, 'fable-method/platforms/codex/fable-method/SKILL.md'))
    refute_includes text, 'prevents the command from starting'
    assert_includes text, 'low space never blocks the command.'
  end
end
