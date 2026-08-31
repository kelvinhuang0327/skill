#!/usr/bin/env ruby
# frozen_string_literal: true

require 'open3'
require 'pathname'
require 'set'
require 'yaml'

# Authoring-time structural integrity for the Fable Skill source tree: does the
# canonical entrypoint/reference registry declared in platforms.yaml match what
# is actually on disk, and do managed Markdown files only link to files that
# exist. Materialization drift across platforms is intentionally NOT
# reimplemented here; it is delegated to the existing sync-platforms.sh
# authority so there is exactly one place that knows how to render/compare
# per-platform output.
module Fable
  module SkillAuthoringIntegrity
    class IntegrityViolation < StandardError; end

    CANONICAL_SKILL = 'fable-method/shared/SKILL.md'
    SHARED_ROOT = 'fable-method/shared'
    REFERENCE_ROOT = 'fable-method/shared/references'
    SYNC_SCRIPT_RELATIVE = 'fable-method/scripts/sync-platforms.sh'

    Outcome = Struct.new(:report, :sync_result, keyword_init: true)
    SyncResult = Struct.new(:ok, :stdout, :stderr, :exit_status, keyword_init: true)

    class Report
      def initialize
        @failures = []
      end

      def record(check, message)
        @failures << "#{check}: #{message}"
      end

      def ok?
        @failures.empty?
      end

      attr_reader :failures
    end

    # Structural checks over platforms.yaml and the canonical shared/
    # source tree. Every check is a mechanical set/path comparison; none of
    # them evaluate prose quality or use text-similarity heuristics.
    class Validator
      class << self
        def load_registry(manifest_path)
          yaml = File.read(manifest_path, encoding: 'UTF-8')
          document = Psych.parse(yaml)
          raise IntegrityViolation, 'platforms.yaml must contain one YAML document' unless document&.root

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

      attr_reader :markdown_link_count

      # Check 1: canonical Skill entrypoint exists, and is the only one.
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

      # Check 2: every reference platforms.yaml declares actually exists.
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

      # Check 4a: every reference file on disk is declared (no orphans).
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

      # Check 4b: no repository path is claimed by two incompatible source roles
      # (shared.skill, shared.references, a platform's frontmatter/adapter/override
      # source). Ownership here is exactly what platforms.yaml declares, so a
      # conflict is mechanically provable without any semantic judgment.
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
          details = conflicts.sort.map { |path, roles| "#{path} => #{roles.to_a.sort.join(', ')}" }
          violation!("incompatible canonical source ownership: #{details.join('; ')}")
        end

        true
      end

      # Check 4c: within the declared registry, no two sources are mapped onto
      # the same per-platform materialized destination, and no two platforms
      # share one materialized root. This is registry-internal (it never stats
      # the materialized filesystem); actual on-disk drift is check 6, via
      # sync-platforms.sh.
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

      # Check 5: relative Markdown links in every canonical/materialized
      # managed surface resolve to a real file within their own tree.
      # External (http/https/mailto) links and fenced code examples are
      # ignored; targets are re-resolved through realpath so a symlink cannot
      # be used to point outside the intended root.
      def validate_markdown_links!
        @markdown_link_count = 0
        managed_markdown_surfaces.each do |relative_path, intended_root|
          validate_markdown_surface!(relative_path, intended_root)
        end
        true
      end

      private

      def managed_markdown_surfaces
        surfaces = repository_files(SHARED_ROOT, '*.md').map { |path| [path, SHARED_ROOT] }
        surfaces << [canonical_skill_path!, SHARED_ROOT]
        registered_reference_paths!.each { |reference| surfaces << [reference, SHARED_ROOT] }

        platform_records!.each_with_index do |platform, index|
          label = "platforms[#{index}]"
          materialized_root = normalize_repo_relative!(
            scalar_field!(platform, 'materialized_destination', label),
            "#{label}.materialized_destination"
          )
          repository_files(materialized_root, '*.md').each { |path| surfaces << [path, materialized_root] }
          surfaces << [
            destination_path!(materialized_root, 'SKILL.md', "#{label}.SKILL.md"),
            materialized_root
          ]

          registered_reference_paths!.each do |reference|
            relative_destination = reference.delete_prefix("#{SHARED_ROOT}/")
            surfaces << [
              destination_path!(materialized_root, relative_destination, reference),
              materialized_root
            ]
          end

          override_records!(platform, label).each_with_index do |override, override_index|
            destination = scalar_field!(
              override,
              'destination',
              "#{label}.reference_overrides[#{override_index}]"
            )
            surfaces << [destination_path!(materialized_root, destination, destination), materialized_root]
          end
        end

        surfaces.uniq
      end

      def validate_markdown_surface!(relative_path, intended_root)
        path = @repository_root.join(relative_path)
        violation!("managed Markdown surface is missing: #{relative_path}") unless path.file?

        markdown_targets(File.read(path, encoding: 'UTF-8')).each do |target, line_number|
          next if markdown_external_target?(target)

          file_target = target.split('#', 2).first
          next if file_target.empty?

          @markdown_link_count += 1
          validate_markdown_target!(relative_path, intended_root, target, line_number)
        end
      end

      def markdown_targets(content)
        lines = markdown_lines_outside_fences(content)
        definitions = markdown_reference_definitions(lines)
        targets = definitions.values.dup

        lines.each do |line, line_number|
          line.scan(/(?<!!)\[[^\]\n]+\]\(\s*(?:<([^>\n]+)>|([^\s)]+))/) do
            targets << [Regexp.last_match(1) || Regexp.last_match(2), line_number]
          end

          line.scan(/(?<!!)\[([^\]\n]+)\]\[([^\]\n]*)\]/) do |label, reference|
            target = definitions[normalize_reference_label(reference.empty? ? label : reference)]
            targets << target if target
          end
        end

        targets.compact.uniq
      end

      def markdown_lines_outside_fences(content)
        lines = []
        fence = nil

        content.each_line.with_index(1) do |line, line_number|
          if fence
            fence = nil if markdown_fence_closes?(line, fence)
            next
          end

          opening = line.match(/\A {0,3}(`{3,}|~{3,})/)
          if opening
            fence = { marker: opening[1][0], length: opening[1].length }
            next
          end

          lines << [line, line_number]
        end

        lines
      end

      def markdown_fence_closes?(line, fence)
        marker = Regexp.escape(fence.fetch(:marker))
        minimum = fence.fetch(:length)
        line.match?(Regexp.new("\\A {0,3}#{marker}{#{minimum},}\\s*\\z"))
      end

      def markdown_reference_definitions(lines)
        lines.each_with_object({}) do |(line, line_number), definitions|
          match = line.match(/\A {0,3}\[([^\]\n]+)\]:\s*(?:<([^>\n]+)>|(\S+))/)
          next unless match

          target = match[2] || match[3]
          definitions[normalize_reference_label(match[1])] = [target, line_number]
        end
      end

      def normalize_reference_label(label)
        label.strip.downcase.gsub(/\s+/, ' ')
      end

      def markdown_external_target?(target)
        target.match?(/\A(?:https?|mailto):/i)
      end

      def validate_markdown_target!(source_path, intended_root, target, line_number)
        file_target = target.split('#', 2).first
        intended_root_path = @repository_root.join(intended_root).cleanpath
        source_file = @repository_root.join(source_path)
        target_path = Pathname.new(file_target).absolute? ?
          Pathname.new(file_target).cleanpath :
          source_file.dirname.join(file_target).cleanpath

        unless path_within_tree?(target_path, intended_root_path)
          violation!(
            "Markdown link path-safety violation at #{source_path}:#{line_number}: " \
            "#{target} resolves outside #{intended_root}"
          )
        end

        unless target_path.file?
          violation!(
            "broken Markdown local link at #{source_path}:#{line_number}: " \
            "#{target} resolves to #{target_path.relative_path_from(@repository_root)}"
          )
        end

        real_target = Pathname.new(File.realpath(target_path.to_s))
        real_root = Pathname.new(File.realpath(intended_root_path.to_s))
        unless path_within_tree?(real_target, real_root)
          violation!(
            "Markdown link path-safety violation at #{source_path}:#{line_number}: " \
            "#{target} resolves outside #{intended_root}"
          )
        end
      end

      def path_within_tree?(path, root)
        path == root || path.to_s.start_with?("#{root}/")
      end

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
        violation!('shared.references must be an array') unless references.is_a?(Array)

        references.map.with_index do |reference, index|
          path = normalize_repo_relative!(reference, "shared.references[#{index}]")
          unless path.start_with?("#{REFERENCE_ROOT}/")
            violation!("shared.references entry is outside #{REFERENCE_ROOT}: #{path}")
          end
          violation!("shared.references entry is not Markdown: #{path}") unless File.extname(path) == '.md'
          path
        end
      end

      def shared_registry!
        violation!('platforms.yaml root must be a mapping') unless @registry.is_a?(Hash)
        shared = @registry['shared']
        violation!('platforms.yaml shared must be a mapping') unless shared.is_a?(Hash)

        shared
      end

      def platform_records!
        unless @registry.is_a?(Hash) && @registry['platforms'].is_a?(Array)
          violation!('platforms.yaml platforms must be an array')
        end

        @registry.fetch('platforms').map.with_index do |platform, index|
          violation!("platforms[#{index}] must be a mapping") unless platform.is_a?(Hash)
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
        violation!("#{label}.#{field} must be a scalar string") unless value.is_a?(String)
        value
      end

      def array_field!(mapping, field, label)
        value = mapping[field]
        violation!("#{label}.#{field} must be an array") unless value.is_a?(Array)
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

        details = duplicates.sort.map { |path, registered_owners| "#{path} => #{registered_owners.sort.join(', ')}" }
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

    # Check 3 + 6 + 7 (materialized-where-required, per-platform drift, and the
    # SKILL.md line-budget) are the existing sync-platforms.sh --check
    # authority's job. This never re-renders or re-diffs platform output
    # itself; it only shells out and reports what that authority found.
    module SyncPlatformsCheck
      def self.run(repository_root:)
        script = repository_root.join(SYNC_SCRIPT_RELATIVE)
        raise IntegrityViolation, "sync authority is missing: #{SYNC_SCRIPT_RELATIVE}" unless script.file?

        stdout, stderr, status = Open3.capture3('bash', script.to_s, '--check', chdir: repository_root.to_s)
        SyncResult.new(ok: status.success?, stdout: stdout, stderr: stderr, exit_status: status.exitstatus)
      end
    end

    def self.run_check(report, name, validator, method)
      validator.public_send(method)
    rescue IntegrityViolation => error
      report.record(name, error.message)
    end
    private_class_method :run_check

    STRUCTURAL_CHECKS = {
      'CANONICAL_ENTRYPOINT' => :validate_canonical_entry!,
      'REFERENCE_REGISTRY' => :validate_reference_registry!,
      'ORPHAN_REFERENCES' => :validate_no_orphan_references!,
      'SOURCE_OWNERSHIP' => :validate_source_ownership!,
      'DESTINATION_OWNERSHIP' => :validate_destination_ownership!,
      'MARKDOWN_LINKS' => :validate_markdown_links!
    }.freeze

    def self.check(repository_root: File.expand_path('../..', __dir__))
      repository_root = Pathname.new(repository_root).expand_path.cleanpath
      report = Report.new
      registry = nil

      begin
        registry = Validator.load_registry(repository_root.join('fable-method/platforms.yaml'))
      rescue IntegrityViolation => error
        report.record('MANIFEST', error.message)
      end

      if registry
        validator = Validator.new(repository_root: repository_root, registry: registry)
        STRUCTURAL_CHECKS.each do |name, method|
          run_check(report, name, validator, method)
        end
      end

      sync_result = nil
      begin
        sync_result = SyncPlatformsCheck.run(repository_root: repository_root)
        report.record('MATERIALIZATION_DRIFT', sync_result.stdout.strip) unless sync_result.ok
      rescue IntegrityViolation => error
        report.record('SYNC_AUTHORITY', error.message)
      end

      Outcome.new(report: report, sync_result: sync_result)
    end
  end
end

if $PROGRAM_NAME == __FILE__
  unless ARGV.length == 1 && ARGV[0] == '--check'
    warn "usage: #{$PROGRAM_NAME} --check"
    exit 2
  end

  outcome = Fable::SkillAuthoringIntegrity.check
  if outcome.sync_result
    print outcome.sync_result.stdout
    warn outcome.sync_result.stderr unless outcome.sync_result.stderr.empty?
  end

  if outcome.report.ok?
    puts 'SKILL_AUTHORING_INTEGRITY: PASS'
    exit 0
  end

  outcome.report.failures.each { |failure| puts "SKILL_AUTHORING_INTEGRITY_FAIL: #{failure}" }
  puts 'SKILL_AUTHORING_INTEGRITY: FAIL'
  exit 1
end
