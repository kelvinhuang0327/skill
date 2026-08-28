# frozen_string_literal: true

require 'fileutils'
require 'minitest/autorun'
require 'pathname'
require 'set'
require 'tmpdir'
require 'yaml'

class SkillAuthoringIntegrityTest < Minitest::Test
  REPOSITORY_ROOT = Pathname.new(File.expand_path('../..', __dir__)).cleanpath
  MANIFEST_PATH = REPOSITORY_ROOT.join('fable-method/platforms.yaml')
  CANONICAL_SKILL = 'fable-method/shared/SKILL.md'
  SHARED_ROOT = 'fable-method/shared'
  REFERENCE_ROOT = 'fable-method/shared/references'

  class IntegrityViolation < StandardError; end

  class Validator
    class << self
      def load_registry(manifest_path)
        yaml = File.read(manifest_path, encoding: 'UTF-8')
        document = Psych.parse(yaml)
        unless document&.root
          raise IntegrityViolation, 'platforms.yaml must contain one YAML document'
        end

        reject_duplicate_mapping_keys!(document.root, 'platforms.yaml')
        YAML.safe_load(yaml, permitted_classes: [], aliases: false)
      rescue Psych::Exception => error
        raise IntegrityViolation, "platforms.yaml could not be parsed: #{error.message}"
      end

      private

      def reject_duplicate_mapping_keys!(node, location)
        case node
        when Psych::Nodes::Mapping
          keys = Set.new
          node.children.each_slice(2) do |key, value|
            unless key.is_a?(Psych::Nodes::Scalar)
              raise IntegrityViolation, "#{location} contains a non-scalar mapping key"
            end
            unless keys.add?(key.value)
              raise IntegrityViolation, "duplicate YAML mapping key at #{location}.#{key.value}"
            end
            reject_duplicate_mapping_keys!(value, "#{location}.#{key.value}")
          end
        when Psych::Nodes::Sequence
          node.children.each_with_index do |child, index|
            reject_duplicate_mapping_keys!(child, "#{location}[#{index}]")
          end
        end
      end
    end

    def initialize(repository_root:, registry:)
      @repository_root = Pathname.new(repository_root).expand_path.cleanpath
      @registry = registry
    end

    def validate_canonical_entry!
      canonical_skill = canonical_skill_path!
      unless canonical_skill == CANONICAL_SKILL
        violation!("shared.skill must resolve to #{CANONICAL_SKILL}, got #{canonical_skill}")
      end
      unless @repository_root.join(canonical_skill).file?
        violation!("canonical shared skill is missing: #{canonical_skill}")
      end

      actual_skills = repository_files(SHARED_ROOT, 'SKILL.md')
      expected_skills = [CANONICAL_SKILL]
      unless actual_skills == expected_skills
        violation!(
          "canonical shared SKILL.md set mismatch: expected=#{expected_skills.inspect} " \
          "actual=#{actual_skills.inspect}"
        )
      end

      true
    end

    def validate_reference_registry!
      references = registered_reference_paths!
      unless references.uniq.length == references.length
        violation!('shared.references entries must be unique after path normalization')
      end

      missing = references.reject { |path| @repository_root.join(path).file? }
      unless missing.empty?
        violation!("registered canonical reference is missing: #{missing.sort.join(', ')}")
      end

      true
    end

    def validate_no_orphan_references!
      registered = registered_reference_paths!.to_set
      actual = repository_files(REFERENCE_ROOT, '*.md').to_set
      return true if registered == actual

      orphans = (actual - registered).to_a.sort
      missing = (registered - actual).to_a.sort
      violation!(
        "canonical reference set mismatch: orphans=#{orphans.inspect} missing=#{missing.inspect}"
      )
    end

    def validate_source_ownership!
      owners = Hash.new { |paths, path| paths[path] = Set.new }
      register_source_owner!(owners, canonical_skill_path!, 'shared.skill')
      registered_reference_paths!.each do |reference|
        register_source_owner!(owners, reference, 'shared.references')
      end

      platform_records!.each_with_index do |platform, index|
        label = "platforms[#{index}]"
        register_source_owner!(
          owners,
          scalar_field!(platform, 'frontmatter_source', label),
          "#{label}.frontmatter_source"
        )
        array_field!(platform, 'adapter_sources', label).each do |adapter|
          register_source_owner!(owners, adapter, "#{label}.adapter_sources")
        end
        override_records!(platform, label).each do |override|
          register_source_owner!(
            owners,
            scalar_field!(override, 'source', "#{label}.reference_overrides"),
            "#{label}.reference_overrides.source"
          )
        end
      end

      conflicts = owners.select { |_path, roles| roles.length > 1 }
      unless conflicts.empty?
        details = conflicts.sort.map do |path, roles|
          "#{path} => #{roles.to_a.sort.join(', ')}"
        end
        violation!("incompatible canonical source ownership: #{details.join('; ')}")
      end

      true
    end

    def validate_destination_ownership!
      materialized_root_owners = Hash.new { |paths, path| paths[path] = [] }

      platform_records!.each_with_index do |platform, index|
        label = "platforms[#{index}]"
        materialized_root = normalize_repo_relative!(
          scalar_field!(platform, 'materialized_destination', label),
          "#{label}.materialized_destination"
        )
        materialized_root_owners[materialized_root] << label

        destination_owners = Hash.new { |paths, path| paths[path] = [] }
        register_destination_owner!(
          destination_owners,
          destination_path!(materialized_root, 'SKILL.md', "#{label}.SKILL.md"),
          "#{label}.materialized_skill"
        )

        registered_reference_paths!.each_with_index do |reference, reference_index|
          relative_destination = reference.delete_prefix("#{SHARED_ROOT}/")
          register_destination_owner!(
            destination_owners,
            destination_path!(materialized_root, relative_destination, reference),
            "shared.references[#{reference_index}]"
          )
        end

        override_records!(platform, label).each_with_index do |override, override_index|
          destination = scalar_field!(
            override,
            'destination',
            "#{label}.reference_overrides[#{override_index}]"
          )
          register_destination_owner!(
            destination_owners,
            destination_path!(materialized_root, destination, destination),
            "#{label}.reference_overrides[#{override_index}]"
          )
        end

        reject_duplicate_destinations!(destination_owners, "#{label} destination")
      end

      reject_duplicate_destinations!(materialized_root_owners, 'materialized root')
      true
    end

    private

    def canonical_skill_path!
      shared = shared_registry!
      unless shared.key?('skill') && shared['skill'].is_a?(String)
        violation!('shared.skill must exist as one scalar string value')
      end

      normalize_repo_relative!(shared.fetch('skill'), 'shared.skill')
    end

    def registered_reference_paths!
      shared = shared_registry!
      references = shared['references']
      unless references.is_a?(Array)
        violation!('shared.references must be an array')
      end

      references.map.with_index do |reference, index|
        path = normalize_repo_relative!(reference, "shared.references[#{index}]")
        unless path.start_with?("#{REFERENCE_ROOT}/")
          violation!("shared.references entry is outside #{REFERENCE_ROOT}: #{path}")
        end
        unless File.extname(path) == '.md'
          violation!("shared.references entry is not Markdown: #{path}")
        end
        path
      end
    end

    def shared_registry!
      unless @registry.is_a?(Hash)
        violation!('platforms.yaml root must be a mapping')
      end
      shared = @registry['shared']
      unless shared.is_a?(Hash)
        violation!('platforms.yaml shared must be a mapping')
      end

      shared
    end

    def platform_records!
      unless @registry.is_a?(Hash) && @registry['platforms'].is_a?(Array)
        violation!('platforms.yaml platforms must be an array')
      end

      @registry.fetch('platforms').map.with_index do |platform, index|
        unless platform.is_a?(Hash)
          violation!("platforms[#{index}] must be a mapping")
        end
        platform
      end
    end

    def override_records!(platform, label)
      array_field!(platform, 'reference_overrides', label).map.with_index do |override, index|
        unless override.is_a?(Hash)
          violation!("#{label}.reference_overrides[#{index}] must be a mapping")
        end
        override
      end
    end

    def scalar_field!(mapping, field, label)
      value = mapping[field]
      unless value.is_a?(String)
        violation!("#{label}.#{field} must be a scalar string")
      end
      value
    end

    def array_field!(mapping, field, label)
      value = mapping[field]
      unless value.is_a?(Array)
        violation!("#{label}.#{field} must be an array")
      end
      value
    end

    def normalize_repo_relative!(value, label)
      unless value.is_a?(String) && !value.empty?
        violation!("#{label} must be a non-empty string path")
      end

      path = Pathname.new(value)
      violation!("#{label} must be repo-relative: #{value}") if path.absolute?

      normalized = path.cleanpath.to_s
      if normalized == '.' || normalized == '..' || normalized.start_with?('../')
        violation!("#{label} escapes the repository: #{value}")
      end
      normalized
    end

    def register_source_owner!(owners, source, role)
      normalized = normalize_repo_relative!(source, role)
      owners[normalized] << role
    end

    def destination_path!(materialized_root, relative_destination, label)
      relative = normalize_repo_relative!(relative_destination, label)
      destination = normalize_repo_relative!(
        File.join(materialized_root, relative),
        "#{label} materialized destination"
      )
      unless destination.start_with?("#{materialized_root}/")
        violation!("#{label} escapes materialized destination #{materialized_root}")
      end
      destination
    end

    def register_destination_owner!(owners, destination, owner)
      owners[destination] << owner
    end

    def reject_duplicate_destinations!(owners, label)
      duplicates = owners.select { |_path, registered_owners| registered_owners.length > 1 }
      return if duplicates.empty?

      details = duplicates.sort.map do |path, registered_owners|
        "#{path} => #{registered_owners.sort.join(', ')}"
      end
      violation!("duplicate #{label} ownership: #{details.join('; ')}")
    end

    def repository_files(root, basename_pattern)
      pattern = @repository_root.join(root, '**', basename_pattern).to_s
      Dir.glob(pattern, File::FNM_DOTMATCH)
         .select { |path| File.file?(path) }
         .map { |path| Pathname.new(path).relative_path_from(@repository_root).cleanpath.to_s }
         .sort
    end

    def violation!(message)
      raise IntegrityViolation, message
    end
  end

  def setup
    @registry = Validator.load_registry(MANIFEST_PATH)
    @validator = validator_for(@registry)
  end

  def test_current_repository_has_one_canonical_shared_skill
    assert @validator.validate_canonical_entry!
  end

  def test_current_reference_registry_is_valid
    assert @validator.validate_reference_registry!
  end

  def test_current_repository_has_no_orphan_canonical_references
    assert @validator.validate_no_orphan_references!
  end

  def test_current_registry_has_no_incompatible_source_ownership
    assert @validator.validate_source_ownership!
  end

  def test_current_platforms_have_no_duplicate_destination_ownership
    assert @validator.validate_destination_ownership!
  end

  def test_negative_control_rejects_ambiguous_or_second_shared_skill
    ambiguous = deep_copy(@registry)
    ambiguous.fetch('shared')['skill'] = [
      CANONICAL_SKILL,
      'fable-method/shared/alternate/SKILL.md'
    ]
    error = assert_raises(IntegrityViolation) do
      validator_for(ambiguous).validate_canonical_entry!
    end
    assert_match(/one scalar string value/, error.message)

    with_minimal_fixture do |root, _registry|
      manifest = root.join('fable-method/platforms.yaml')
      FileUtils.mkdir_p(manifest.dirname)
      File.write(
        manifest,
        <<~YAML
          shared:
            skill: #{CANONICAL_SKILL}
            skill: fable-method/shared/alternate/SKILL.md
            references: []
          platforms: []
        YAML
      )
      error = assert_raises(IntegrityViolation) do
        Validator.load_registry(manifest)
      end
      assert_match(/duplicate YAML mapping key at platforms\.yaml\.shared\.skill/, error.message)
    end

    with_minimal_fixture do |root, registry|
      write_fixture_file(root, 'fable-method/shared/alternate/SKILL.md')
      error = assert_raises(IntegrityViolation) do
        validator_for(registry, repository_root: root).validate_canonical_entry!
      end
      assert_match(/canonical shared SKILL\.md set mismatch/, error.message)
    end
  end

  def test_negative_control_rejects_duplicate_canonical_reference_entry
    duplicate = deep_copy(@registry)
    duplicate.fetch('shared').fetch('references') <<
      'fable-method/shared/references/domains/../examples.md'

    error = assert_raises(IntegrityViolation) do
      validator_for(duplicate).validate_reference_registry!
    end
    assert_match(/entries must be unique/, error.message)
  end

  def test_negative_control_rejects_unregistered_orphan_reference
    with_minimal_fixture do |root, registry|
      write_fixture_file(root, 'fable-method/shared/references/orphan.md')
      error = assert_raises(IntegrityViolation) do
        validator_for(registry, repository_root: root).validate_no_orphan_references!
      end
      assert_match(/orphans=.*orphan\.md/, error.message)
    end
  end

  def test_negative_control_rejects_registered_missing_reference
    with_minimal_fixture do |root, registry|
      registry.fetch('shared')['references'] = [
        'fable-method/shared/references/missing.md'
      ]
      error = assert_raises(IntegrityViolation) do
        validator_for(registry, repository_root: root).validate_reference_registry!
      end
      assert_match(/registered canonical reference is missing/, error.message)
    end
  end

  def test_negative_control_rejects_incompatible_source_roles
    duplicate_owner = deep_copy(@registry)
    duplicate_owner.fetch('platforms').first['frontmatter_source'] = CANONICAL_SKILL

    error = assert_raises(IntegrityViolation) do
      validator_for(duplicate_owner).validate_source_ownership!
    end
    assert_match(/incompatible canonical source ownership/, error.message)
  end

  def test_negative_control_rejects_duplicate_platform_destination
    duplicate_destination = deep_copy(@registry)
    duplicate_destination.fetch('platforms').first['reference_overrides'] = [
      {
        'source' => 'fable-method/shared/platforms/test/references/first.md',
        'destination' => 'references/duplicate.md'
      },
      {
        'source' => 'fable-method/shared/platforms/test/references/second.md',
        'destination' => 'references/nested/../duplicate.md'
      }
    ]

    error = assert_raises(IntegrityViolation) do
      validator_for(duplicate_destination).validate_destination_ownership!
    end
    assert_match(/duplicate platforms\[0\] destination ownership/, error.message)
  end

  private

  def validator_for(registry, repository_root: REPOSITORY_ROOT)
    Validator.new(repository_root: repository_root, registry: registry)
  end

  def deep_copy(value)
    Marshal.load(Marshal.dump(value))
  end

  def with_minimal_fixture
    Dir.mktmpdir('fable-authoring-integrity-') do |directory|
      root = Pathname.new(directory)
      reference = 'fable-method/shared/references/registered.md'
      write_fixture_file(root, CANONICAL_SKILL)
      write_fixture_file(root, reference)
      registry = {
        'shared' => {
          'skill' => CANONICAL_SKILL,
          'references' => [reference]
        },
        'platforms' => []
      }
      yield root, registry
    end
  end

  def write_fixture_file(root, relative_path)
    path = root.join(relative_path)
    FileUtils.mkdir_p(path.dirname)
    File.write(path, "# fixture\n")
  end
end
