# frozen_string_literal: true

require 'minitest/autorun'

class DryRunNoWriteContractTest < Minitest::Test
  SKILL_PATH = File.expand_path('../shared/SKILL.md', __dir__)
  REQUIRED_CONTRACT = [
    'When a task requires no writes, a `dry-run` label or a successful exit alone does not prove a command is read-only.',
    'Use a documented read-only observation, or confirm the command has an authoritative no-write contract before treating it as read-only.',
    'If target metadata changes and attribution is unclear, keep attribution `UNKNOWN`; do not infer the command caused the change or perform another write to clean it up.'
  ].freeze

  def test_dry_run_label_or_success_does_not_prove_no_write_and_unattributed_metadata_stays_unknown
    skill = File.read(SKILL_PATH, encoding: 'UTF-8').gsub(/\s+/, ' ')

    REQUIRED_CONTRACT.each { |statement| assert_includes skill, statement }
  end
end
