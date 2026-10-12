# frozen_string_literal: true

require 'minitest/autorun'

class ThreeuiVuePageSkillTest < Minitest::Test
  SKILL_DIR = File.expand_path('..', __dir__)
  SKILL_MD = File.join(SKILL_DIR, 'SKILL.md')
  ADAPTATION_MD = File.join(SKILL_DIR, 'references', 'THREEUI_VUE_ADAPTATION.md')

  def setup
    @skill = File.read(SKILL_MD)
    @adaptation = File.read(ADAPTATION_MD)
  end

  def test_skill_has_frontmatter_name_and_description
    frontmatter = @skill[/\A---\n(.*?)\n---/m, 1]
    refute_nil frontmatter, 'SKILL.md must open with a --- frontmatter block'
    assert_match(/^name:\s*threeui-vue-page\s*$/, frontmatter)
    assert_match(/^description:\s*\S/, frontmatter)
  end

  def test_skill_forbids_treating_source_as_a_runtime_dependency
    assert_match(/never.*runtime dependency/i, @skill)
    assert_match(/do not install/i, @skill)
    assert_match(/npm\/gem package/i, @skill)
  end

  def test_skill_requires_real_browser_observation_for_visual_acceptance
    assert_match(/real browser observation/i, @skill)
    assert_match(/not\s+.*substitut\w*\s+build\/test output/i, @skill)
  end

  def test_skill_does_not_hardcode_a_specific_product_as_a_general_rule
    refute_match(/MathStatisticalAnalysis/, @skill)
  end

  def test_skill_links_to_the_adaptation_reference
    assert_match(%r{references/THREEUI_VUE_ADAPTATION\.md}, @skill)
  end

  def test_adaptation_reference_records_exact_provenance
    assert_match(/MengTo\/threeui/, @adaptation)
    assert_match(/68802d5428071ada5c20db8094b1649e6bb770ed/, @adaptation)
    assert_match(%r{src/shaders/neuform-isolated/sources/gradient-pill-button\.html}, @adaptation)
    assert_match(/MIT License/, @adaptation)
    assert_match(/Copyright \(c\) 2026 Meng To/, @adaptation)
  end

  def test_adaptation_reference_defines_the_no_duplicate_emit_while_loading_rule
    assert_match(/cannot emit twice/i, @adaptation)
  end
end
