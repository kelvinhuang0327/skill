# frozen_string_literal: true

# Fixtures mutate the actual normative input, not a duplicate Worker simulator.
# Positive clauses bind protections; negative controls prove weakening is caught.
module ContractFixtures
  def normalized_contract(text)
    text.gsub(/\s+/, ' ')
  end

  def assert_guarded_contract(text, clauses, weakenings: {})
    live = normalized_contract(text)
    clauses.each { |clause| assert_includes live, clause }
    (clauses.map { |clause| [clause, ''] } + weakenings.to_a).each do |old, replacement|
      assert_includes live, old, 'negative fixture must target live source'
      changed = live.gsub(old, replacement)
      refute_equal live, changed
      missing = clauses.reject { |clause| changed.include?(clause) }
      refute_empty missing, "weakening escaped the protected contract: #{old}"
    end
  end

  def linked_reference(skill, filename)
    relative = "references/#{filename}"
    assert_includes skill, relative, 'front door must expose the applicable reference'
    File.read(File.expand_path("../../shared/#{relative}", __dir__), encoding: 'UTF-8')
  end
end
