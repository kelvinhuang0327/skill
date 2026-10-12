#!/usr/bin/env ruby
# frozen_string_literal: true

require 'json'
require 'rexml/document'

# Deterministic, dependency-free changed-line execution-adequacy measurement:
# which added/modified lines in a unified diff were exercised by an
# already-produced LCOV/Cobertura/JaCoCo coverage report. This is an
# execution-adequacy measurement only - never a correctness or pass/fail
# verdict, and it never installs or configures a coverage generator itself.
module Fable
  module DiffCoverage
    class InvalidInput < ArgumentError; end

    FORMATS = %w[LCOV COBERTURA JACOCO].freeze

    # diff: unified diff text (required).
    # format/coverage: required only when the diff actually changes lines.
    # repo_root: optional, used only to relativize an absolute coverage path.
    def self.measure(diff:, format: nil, coverage: nil, repo_root: nil)
      changed = DiffParser.parse(diff)
      total_lines = changed.values.reduce(0) { |sum, lines| sum + lines.length }
      return empty_result if total_lines.zero?

      fail!('format is required once the diff changes lines') if format.nil?
      fail!("format must be one of #{FORMATS.join(', ')}") unless FORMATS.include?(format)
      fail!('coverage report text is required once the diff changes lines') if coverage.nil?

      coverage_map = parse_coverage(format, coverage, repo_root)
      build_result(changed, format, coverage_map)
    end

    def self.parse_coverage(format, coverage, repo_root)
      case format
      when 'LCOV' then LcovParser.parse(coverage, repo_root: repo_root)
      when 'COBERTURA' then CoberturaParser.parse(coverage, repo_root: repo_root)
      when 'JACOCO' then JacocoParser.parse(coverage, repo_root: repo_root)
      end
    end
    private_class_method :parse_coverage

    def self.empty_result
      {
        'STATUS' => 'NOT_APPLICABLE', 'COVERAGE_FORMAT' => nil, 'TOTAL_CHANGED_LINES' => 0,
        'COVERED_CHANGED_LINES' => 0, 'UNCOVERED_CHANGED_LINES' => 0,
        'DIFF_COVERAGE_PERCENT' => nil, 'UNCOVERED' => []
      }
    end
    private_class_method :empty_result

    # Deterministic ordering: file path ascending, then line number ascending.
    def self.build_result(changed, format, coverage_map)
      covered = 0
      uncovered = []
      changed.keys.sort.each do |file|
        file_hits = coverage_map[file]
        changed.fetch(file).sort.each do |line|
          hits = file_hits && file_hits[line]
          if hits && hits > 0
            covered += 1
          else
            uncovered << { 'file' => file, 'line' => line }
          end
        end
      end
      total = covered + uncovered.length
      {
        'STATUS' => 'MEASURED', 'COVERAGE_FORMAT' => format, 'TOTAL_CHANGED_LINES' => total,
        'COVERED_CHANGED_LINES' => covered, 'UNCOVERED_CHANGED_LINES' => uncovered.length,
        'DIFF_COVERAGE_PERCENT' => (covered * 100.0 / total).round(2), 'UNCOVERED' => uncovered
      }
    end
    private_class_method :build_result

    def self.fail!(message)
      raise InvalidInput, message
    end
    private_class_method :fail!

    # Shared repo-relative identity for both diff paths and coverage-report
    # paths: strip a leading Git a/ or b/ diff prefix or a leading ./, and
    # relativize an absolute path only against an explicitly supplied repo
    # root that actually contains it. Never fuzzy or basename matched - an
    # absolute path with no supplied root, or one outside the supplied root,
    # fails closed rather than guessing.
    module PathNormalizer
      def self.normalize(path, repo_root: nil)
        return nil if path.nil?

        candidate = path.strip
        return nil if candidate.empty? || candidate == '/dev/null'

        candidate = candidate.delete_prefix('./')
        candidate = candidate[2, candidate.length - 2] if candidate.start_with?('a/', 'b/')

        return candidate unless candidate.start_with?('/')

        if repo_root.nil?
          raise InvalidInput, "absolute coverage path #{path.inspect} requires an explicit repo root to resolve"
        end

        root = File.expand_path(repo_root)
        absolute = File.expand_path(candidate)
        return '' if absolute == root
        return absolute[(root.length + 1)..-1] if absolute.start_with?("#{root}/")

        raise InvalidInput, "absolute coverage path #{path.inspect} is outside repo root #{repo_root.inspect}"
      end
    end
    private_constant :PathNormalizer

    # Insert one (file, line) hit-count observation shared by every coverage
    # parser. A second, conflicting observation for the exact same key is an
    # unresolvable ambiguity and fails closed rather than picking either one.
    module CoverageMap
      def self.record_hit!(map, file, line, hits, context)
        map[file] ||= {}
        existing = map[file][line]
        if existing && existing != hits
          raise InvalidInput, "conflicting coverage records for #{file}:#{line} (#{existing} vs #{hits}) at #{context}"
        end

        map[file][line] = hits
      end
    end
    private_constant :CoverageMap

    # Shared malformed-XML-fails-closed wrapper for the two XML coverage
    # formats. A missing root element is treated the same as a parse error.
    module XmlDocument
      def self.parse(text, label)
        doc = REXML::Document.new(text)
        raise InvalidInput, "malformed #{label} XML: no root element" unless doc.root

        doc
      rescue REXML::ParseException => e
        raise InvalidInput, "malformed #{label} XML: #{e.message}"
      end
    end
    private_constant :XmlDocument

    # Consumes a unified diff and returns { normalized_new_path => [line, ...] }
    # for every added/modified line on the new-file side. Deletions and
    # unchanged context never enter the changed-line universe. Hunk content is
    # classified purely by the declared @@ old/new counts, never by sniffing
    # line prefixes for file/hunk headers, so a deleted or added source line
    # that happens to start with "---"/"+++"/"@@" can never be mistaken for one.
    module DiffParser
      NEW_FILE_HEADER = /\A\+\+\+ (.+)\z/.freeze
      HUNK_HEADER = /\A@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.freeze

      def self.parse(diff_text)
        changed = Hash.new { |hash, key| hash[key] = [] }
        current_file = :none
        old_remaining = 0
        new_remaining = 0
        new_line = nil

        diff_text.each_line do |raw_line|
          line = raw_line.chomp("\n").chomp("\r")

          if old_remaining.positive? || new_remaining.positive?
            if line.start_with?('\\')
              # "\ No newline at end of file" consumes no counter slot.
            elsif line.start_with?('+')
              raise InvalidInput, 'hunk contains more added lines than its @@ header declared' unless new_remaining.positive?
              raise InvalidInput, 'added line in a hunk with no associated new-file path' if current_file.nil? || current_file == :none

              changed[current_file] << new_line
              new_line += 1
              new_remaining -= 1
            elsif line.start_with?('-')
              raise InvalidInput, 'hunk contains more deleted lines than its @@ header declared' unless old_remaining.positive?

              old_remaining -= 1
            elsif line.start_with?(' ') || line.empty?
              unless old_remaining.positive? && new_remaining.positive?
                raise InvalidInput, 'hunk contains more context lines than its @@ header declared'
              end

              new_line += 1
              old_remaining -= 1
              new_remaining -= 1
            else
              raise InvalidInput, "unrecognized diff content line inside a hunk: #{line.inspect}"
            end
            next
          end

          if (match = NEW_FILE_HEADER.match(line))
            current_file = PathNormalizer.normalize(strip_diff_timestamp(match[1]))
            next
          end

          next unless (match = HUNK_HEADER.match(line))

          raise InvalidInput, 'hunk header encountered before any +++ file path' if current_file == :none

          old_remaining = (match[1] || '1').to_i
          new_line = match[2].to_i
          new_remaining = (match[3] || '1').to_i
        end

        raise InvalidInput, 'diff ends mid-hunk: declared hunk line counts were not fully consumed' \
          if old_remaining.positive? || new_remaining.positive?

        changed.each_with_object({}) { |(file, lines), result| result[file] = lines }
      end

      def self.strip_diff_timestamp(path)
        path.split("\t", 2).first.rstrip
      end
      private_class_method :strip_diff_timestamp
    end
    private_constant :DiffParser

    # SF:/DA:/end_of_record only; all other LCOV directives (TN, FN, FNDA,
    # FNF, FNH, BRDA, BRF, BRH, LF, LH) are recognized-but-irrelevant and
    # tolerated so a real project's existing LCOV output parses unmodified.
    module LcovParser
      def self.parse(text, repo_root: nil)
        coverage = {}
        current_file = nil
        text.each_line do |raw|
          line = raw.chomp("\n").chomp("\r")
          next if line.empty?

          if line.start_with?('SF:')
            raise InvalidInput, 'LCOV SF record opened before the previous one was closed' if current_file

            path = PathNormalizer.normalize(line.delete_prefix('SF:'), repo_root: repo_root)
            raise InvalidInput, 'LCOV SF record has an empty path' if path.nil? || path.empty?

            current_file = path
            coverage[current_file] ||= {}
          elsif line.start_with?('DA:')
            raise InvalidInput, 'LCOV DA record appears before any SF record' unless current_file

            fields = line.delete_prefix('DA:').split(',')
            unless fields.length >= 2 && /\A\d+\z/.match?(fields[0]) && /\A\d+\z/.match?(fields[1])
              raise InvalidInput, "malformed LCOV DA record: #{line.inspect}"
            end

            CoverageMap.record_hit!(coverage, current_file, fields[0].to_i, fields[1].to_i, "LCOV #{line.inspect}")
          elsif line == 'end_of_record'
            raise InvalidInput, 'end_of_record without a preceding SF record' unless current_file

            current_file = nil
          end
        end
        raise InvalidInput, 'LCOV input ended with an unterminated SF record' if current_file

        coverage
      end
    end
    private_constant :LcovParser

    # Cobertura <class filename="..."><lines><line number="" hits=""/> - reads
    # only the class-level <lines> child (not method-nested duplicates).
    module CoberturaParser
      def self.parse(text, repo_root: nil)
        doc = XmlDocument.parse(text, 'Cobertura')
        coverage = {}
        REXML::XPath.each(doc, '//class') do |class_element|
          filename = class_element.attributes['filename']
          raise InvalidInput, 'Cobertura <class> element is missing a filename attribute' unless filename

          file = PathNormalizer.normalize(filename, repo_root: repo_root)
          raise InvalidInput, "Cobertura <class> filename normalizes to empty: #{filename.inspect}" if file.nil? || file.empty?

          lines_element = class_element.elements['lines']
          next unless lines_element

          lines_element.each_element('line') do |line_element|
            number = line_element.attributes['number']
            hits = line_element.attributes['hits']
            unless number && hits && /\A\d+\z/.match?(number) && /\A\d+\z/.match?(hits)
              raise InvalidInput, "malformed Cobertura <line> entry for #{file}"
            end

            CoverageMap.record_hit!(coverage, file, number.to_i, hits.to_i, "Cobertura #{file}:#{number}")
          end
        end
        coverage
      end
    end
    private_constant :CoberturaParser

    # JaCoCo <package name="a/b"><sourcefile name="File.java"><line nr="" ci=""/>
    # File identity is package name + sourcefile name; a line is covered when
    # its covered-instruction count (ci) is greater than zero.
    module JacocoParser
      def self.parse(text, repo_root: nil)
        doc = XmlDocument.parse(text, 'JaCoCo')
        coverage = {}
        REXML::XPath.each(doc, '//package') do |package_element|
          package_name = package_element.attributes['name'].to_s
          package_element.elements.each('sourcefile') do |sourcefile_element|
            source_name = sourcefile_element.attributes['name']
            raise InvalidInput, 'JaCoCo <sourcefile> is missing a name attribute' unless source_name

            identity = package_name.empty? ? source_name : "#{package_name}/#{source_name}"
            file = PathNormalizer.normalize(identity, repo_root: repo_root)
            raise InvalidInput, "JaCoCo sourcefile identity normalizes to empty: #{identity.inspect}" if file.nil? || file.empty?

            sourcefile_element.elements.each('line') do |line_element|
              nr = line_element.attributes['nr']
              ci = line_element.attributes['ci']
              unless nr && ci && /\A\d+\z/.match?(nr) && /\A\d+\z/.match?(ci)
                raise InvalidInput, "malformed JaCoCo <line> entry for #{file}"
              end

              CoverageMap.record_hit!(coverage, file, nr.to_i, ci.to_i, "JaCoCo #{file}:#{nr}")
            end
          end
        end
        coverage
      end
    end
    private_constant :JacocoParser
  end
end

if $PROGRAM_NAME == __FILE__
  begin
    input = JSON.parse($stdin.read)
    raise Fable::DiffCoverage::InvalidInput, 'input must be a JSON object' unless input.is_a?(Hash)

    allowed = %w[diff format coverage repo_root]
    unknown = input.keys - allowed
    raise Fable::DiffCoverage::InvalidInput, "unknown input fields: #{unknown.join(', ')}" unless unknown.empty?
    raise Fable::DiffCoverage::InvalidInput, 'diff must be a string' unless input['diff'].is_a?(String)

    result = Fable::DiffCoverage.measure(
      diff: input['diff'], format: input['format'], coverage: input['coverage'], repo_root: input['repo_root']
    )
    puts JSON.pretty_generate(result)
  rescue JSON::ParserError, Fable::DiffCoverage::InvalidInput => e
    warn JSON.generate('error' => 'INVALID_INPUT', 'message' => e.message)
    exit 2
  end
end
