#!/usr/bin/env ruby
# frozen_string_literal: true

require 'open3'
require 'pathname'
require 'set'
require_relative 'platform_manifest'

# Authoring-time structural integrity for the Fable Skill source tree: does the
# canonical entrypoint/reference registry declared in platforms.yaml match what
# is actually on disk, and do managed Markdown files only link to files that
# exist. Materialization drift across platforms is intentionally NOT
# reimplemented here; it is delegated to the existing sync-platforms.sh
# authority so there is exactly one place that knows how to render/compare
# per-platform output.
module Fable
  module SkillAuthoringIntegrity
    IntegrityViolation = PlatformManifest::Error

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
      def self.load_registry(manifest_path)
        PlatformManifest.load(manifest_path).data
      end

      def initialize(repository_root:, registry:, skill: 'fable-method')
        @repository_root = Pathname.new(repository_root).expand_path.cleanpath
        @model = PlatformManifest.new(registry)
        @skill = skill
        @registry = @model.skill(skill)
        @shared_root = "#{skill}/shared"
        @reference_root = "#{@shared_root}/references"
        @canonical_skill = "#{@shared_root}/SKILL.md"
      end

      attr_reader :markdown_link_count

      # Check 1: canonical Skill entrypoint exists, and is the only one.
      def validate_canonical_entry!
        canonical_skill = canonical_skill_path!
        unless canonical_skill == @canonical_skill
          violation!("shared.skill must resolve to #{@canonical_skill}, got #{canonical_skill}")
        end
        unless @repository_root.join(canonical_skill).file?
          violation!("canonical shared skill is missing: #{canonical_skill}")
        end

        actual_skills = repository_files(@shared_root, 'SKILL.md')
        expected_skills = [@canonical_skill]
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
        actual = repository_files(@reference_root, '*.md').to_set
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
        @model.source_paths(@skill).each do |path|
          PlatformManifest.safe_file!(@repository_root.join(path))
        end
        @model.validate!
      end

      def validate_destination_ownership!
        @model.validate!
      end

      # Check 5: relative Markdown links in every canonical/materialized
      # managed surface resolve to a real file within their own tree.
      # External (http/https/mailto) links and fenced code examples are
      # ignored; targets are re-resolved through realpath so a symlink cannot
      # be used to point outside the intended root.
      def validate_markdown_links!
        @markdown_link_count = 0
        if @skill == 'fable-judge'
          @model.platforms(@skill).each do |platform|
            @model.render(@repository_root.to_s, @skill, platform['name'])
            root = @repository_root.join(platform['materialized_destination']).to_s
            inventory = PlatformManifest.inventory(root)
            files = inventory.select { |_, kind| kind == :file }.to_h do |path, _|
              [path, File.binread(File.join(root, path))]
            end
            PlatformManifest.validate_links!(files)
            @markdown_link_count += files.length
          end
          return true
        end
        managed_markdown_surfaces.each do |relative_path, intended_root|
          validate_markdown_surface!(relative_path, intended_root)
        end
        true
      end

      private

      def managed_markdown_surfaces
        surfaces = repository_files(@shared_root, '*.md').map { |path| [path, @shared_root] }
        surfaces << [canonical_skill_path!, @shared_root]
        registered_reference_paths!.each { |reference| surfaces << [reference, @shared_root] }

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
            relative_destination = reference.delete_prefix("#{@shared_root}/")
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
        PlatformManifest.safe_file!(path)
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

        PlatformManifest.safe_file!(target_path) if target_path.exist?
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
          unless path.start_with?("#{@reference_root}/")
            violation!("shared.references entry is outside #{@reference_root}: #{path}")
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
      def self.run(repository_root:, skill: nil)
        script = repository_root.join(SYNC_SCRIPT_RELATIVE)
        raise IntegrityViolation, "sync authority is missing: #{SYNC_SCRIPT_RELATIVE}" unless script.file?

        names = skill ? [skill] : PlatformManifest.load(repository_root.join('fable-method/platforms.yaml')).skills
        results = names.map do |name|
          Open3.capture3('bash', script.to_s, '--check', '--skill', name, chdir: repository_root.to_s)
        end
        ok = results.all? { |_, _, status| status.success? }
        SyncResult.new(ok: ok, stdout: results.map(&:first).join, stderr: results.map { |r| r[1] }.join,
                       exit_status: ok ? 0 : results.find { |r| !r[2].success? }[2].exitstatus)
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
        registry.fetch('skills').each_key do |skill|
          validator = Validator.new(repository_root: repository_root, registry: registry, skill: skill)
          STRUCTURAL_CHECKS.each do |name, method|
            run_check(report, "#{skill}/#{name}", validator, method)
          end
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
