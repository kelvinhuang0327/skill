# frozen_string_literal: true

require 'minitest/autorun'

class ExecutionEvidenceOriginRulesTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = %w[codex claude gemini antigravity].map do |platform|
    File.join(ROOT, "fable-method/platforms/#{platform}/fable-method/SKILL.md")
  end.freeze
  REQUIRED_TEXT = [
    'one structured source',
    'pre-run fingerprint',
    'canonical payload',
    'per-file path/status/mode/SHA',
    'command argv, and the receipt',
    '`observed_at` and source identity',
    'label the snapshot historical'
  ].freeze

  def test_generated_platform_skills_require_execution_evidence_to_share_its_source
    SKILLS.each do |path|
      skill = File.read(path).gsub(/\s+/, ' ')
      REQUIRED_TEXT.each do |text|
        assert skill.include?(text), "#{path} is missing #{text.inspect}"
      end
    end
  end
end
