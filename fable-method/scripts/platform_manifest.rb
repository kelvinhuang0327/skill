#!/usr/bin/env ruby
# frozen_string_literal: true

require 'yaml'
require 'pathname'
require 'set'
require 'fileutils'
require 'digest'

module Fable
  # Closed deployment schema for these two skills, not a plugin registry.
  # All consumers use this model, including rendering and Markdown projection.
  class PlatformManifest
    class Error < StandardError; end
    REPOSITORY = '/Users/kelvin/VibeCoding-WorkSpace/skill'
    MATERIALIZED = 'fable-method/platforms'
    PAIRS = {
      'fable-method' => %w[codex claude gemini antigravity],
      'fable-judge' => %w[codex claude gemini antigravity]
    }.freeze
    DEPTH_SOURCE = 'fable-method/shared/references/judge-handoff.md'
    DEPTH_DESTINATION = 'references/judge-depth-contract.md'
    DEPTH_SECTIONS = ['Depth and evidence reuse', 'Depth reconciliation', 'Remediation limit'].freeze
    RUNTIME_ASSET_FIELDS = {
      'task_checkpoint' => %w[id role source source_commit git_blob sha256 mode destination purpose],
      'paused_run_continuation' => %w[id role source source_commit git_blob sha256 mode destination purpose],
      'artifact_controls' => %w[id role source sha256 mode destination purpose]
    }.freeze
    CODEX_RUNTIME_ASSETS = {
      'task_checkpoint' => {
        'role' => 'launcher',
        'source' => 'fable-method/scripts/task_checkpoint.rb',
        'mode' => '100755',
        'destination' => 'scripts/task_checkpoint.rb',
        'purpose' => 'canonical_task_checkpoint_launcher'
      },
      'paused_run_continuation' => {
        'role' => 'required_companion',
        'source' => 'fable-method/scripts/paused_run_continuation.rb',
        'mode' => '100644',
        'destination' => 'scripts/paused_run_continuation.rb',
        'purpose' => 'required_only_for_continue_paused_run_transition'
      },
      'artifact_controls' => {
        'role' => 'supporting_helper',
        'source' => 'fable-method/scripts/fable_artifact_controls.py',
        'sha256' => 'b08f5085c8b8a529ba931d8d7d5c82a199c04eb0ef71654615e64c1aeb937482',
        'mode' => '100644',
        'destination' => 'scripts/fable_artifact_controls.py',
        'purpose' => 'local_codex_artifact_logs_and_worktree_lifecycle'
      }
    }.freeze
    LIVE = {
      'fable-method' => {
        'codex' => '/Users/kelvin/.codex/skills/fable-method',
        'claude' => '/Users/kelvin/.claude/skills/fable-method',
        'gemini' => '/Users/kelvin/.gemini/skills/fable-method',
        'antigravity' => '/Users/kelvin/.gemini/config/skills/fable-method'
      },
      'fable-judge' => {
        'codex' => '/Users/kelvin/.codex/skills/fable-judge',
        'claude' => '/Users/kelvin/.claude/skills/fable-judge',
        'gemini' => '/Users/kelvin/.gemini/skills/fable-judge',
        'antigravity' => '/Users/kelvin/.gemini/config/skills/fable-judge'
      }
    }.freeze

    attr_reader :data

    def self.yaml(text)
      stream = Psych.parse_stream(text)
      fail Error, 'manifest must contain exactly one YAML document' unless stream.children.length == 1
      visit = lambda do |node|
        if node.is_a?(Psych::Nodes::Mapping)
          seen = Set.new
          node.children.each_slice(2) do |key, value|
            fail Error, 'non-scalar YAML mapping key' unless key.is_a?(Psych::Nodes::Scalar)
            fail Error, "duplicate YAML mapping key: #{key.value}" unless seen.add?(key.value)
            visit.call(value)
          end
        elsif node.respond_to?(:children) && node.children
          node.children.each { |child| visit.call(child) }
        end
      end
      visit.call(stream)
      YAML.safe_load(text, permitted_classes: [], aliases: false)
    rescue Psych::Exception => e
      raise Error, "invalid YAML: #{e.message}"
    end

    def self.load(path)
      safe_file!(path)
      new(yaml(File.binread(path)))
    end

    def initialize(data)
      @data = data
      validate!
    end

    def skills
      data.fetch('skills').keys
    end

    def skill(name)
      fail Error, "unknown skill: #{name}" unless PAIRS.key?(name)
      data.fetch('skills').fetch(name)
    end

    def platforms(name)
      skill(name).fetch('platforms')
    end

    def pair(name, platform)
      platforms(name).find { |p| p['name'] == platform } ||
        (fail Error, "unsupported skill/platform pair: #{name}/#{platform}")
    end

    def shared(name)
      skill(name).fetch('shared')
    end

    def self.relative!(value, label = 'path')
      unless value.is_a?(String) && value.match?(%r{\A[^/\x00-\x20\\]+(?:/[^/\x00-\x20\\]+)*\z}) &&
             value.split('/').none? { |part| %w[. ..].include?(part) }
        fail Error, "unsafe relative #{label}: #{value.inspect}"
      end
      value
    end

    def mapping!(value, keys, label)
      fail Error, "#{label} must be a mapping" unless value.is_a?(Hash)
      fail Error, "#{label} missing required field or unknown field" unless value.keys.all? { |key| key.is_a?(String) } && value.keys.sort == keys.sort
    end

    def array!(value, label)
      fail Error, "#{label} must be an array" unless value.is_a?(Array)
      value
    end

    def owned!(path, root)
      self.class.relative!(path)
      fail Error, "undeclared source ownership: #{path} outside #{root}" unless path.start_with?(root + '/')
      path
    end

    def no_overlap!(paths, label)
      paths.each_with_index do |path, i|
        paths[0...i].each do |other|
          if path == other || path.start_with?(other + '/') || other.start_with?(path + '/')
            fail Error, "#{label} collision/overlap: #{path}, #{other}"
          end
        end
      end
    end

    def validate!
      mapping!(data, %w[schema_version repository_root materialized_root skills], 'manifest')
      fail Error, 'schema_version must be 3' unless data['schema_version'].is_a?(Integer) && data['schema_version'] == 3
      fail Error, 'repository_root mismatch' unless data['repository_root'] == REPOSITORY
      fail Error, 'materialized_root mismatch' unless data['materialized_root'] == MATERIALIZED
      mapping!(data['skills'], PAIRS.keys, 'skills')
      data['skills'].each do |name, entry|
        mapping!(entry, %w[shared platforms], name)
        mapping!(entry['shared'], %w[skill references projections], "#{name}.shared")
      end
      sources = []
      destinations = []
      live_paths = []
      skills.each do |name|
        entry = skill(name)
        mapping!(entry, %w[shared platforms], name)
        common = entry['shared']
        mapping!(common, %w[skill references projections], "#{name}.shared")
        fail Error, 'canonical shared skill mismatch' unless common['skill'] == "#{name}/shared/SKILL.md"
        sources << common['skill']
        refs = array!(common['references'], 'shared.references')
        refs.each { |r| sources << owned!(r, "#{name}/shared/references") }
        array!(common['projections'], 'shared.projections').each do |projection|
          mapping!(projection, %w[source destination sections], 'projection')
          fail Error, 'unsupported projection owner/source' unless name == 'fable-judge' && projection['source'] == DEPTH_SOURCE
          fail Error, 'unsupported projection destination' unless projection['destination'] == DEPTH_DESTINATION
          selectors = array!(projection['sections'], 'projection.sections')
          fail Error, 'duplicate projection selector' unless selectors.uniq == selectors
          fail Error, 'unsupported selector or selector ordering' unless selectors == DEPTH_SECTIONS
          unless data['skills'].dig('fable-method', 'shared', 'references').is_a?(Array) &&
                 data['skills']['fable-method']['shared']['references'].include?(projection['source'])
            fail Error, 'undeclared projection reference ownership'
          end
        end
        expected_projection_count = name == 'fable-judge' ? 1 : 0
        fail Error, 'required projection count mismatch' unless common['projections'].length == expected_projection_count
        records = array!(entry['platforms'], 'platforms')
        names = records.map do |p|
          platform_fields = %w[name frontmatter_source adapter_sources reference_overrides materialized_destination live_installation_path live_installation_role]
          platform_fields << 'runtime_assets' if name == 'fable-method' && p.is_a?(Hash) && p['name'] == 'codex'
          mapping!(p, platform_fields, 'platform')
          p['name']
        end
        fail Error, "unsupported or duplicate skill/platform pair: #{name}" unless names.all? { |n| n.is_a?(String) } && names.sort == PAIRS.fetch(name).sort
        records.each do |p|
          platform = p['name']
          area = "#{name}/shared/platforms/#{platform}"
          sources << owned!(p['frontmatter_source'], area)
          array!(p['adapter_sources'], 'adapter_sources').each { |a| sources << owned!(a, area) }
          targets = ['SKILL.md'] + refs.map { |r| r.delete_prefix("#{name}/shared/") } + common['projections'].map { |r| r['destination'] }
          array!(p['reference_overrides'], 'reference_overrides').each do |r|
            mapping!(r, %w[source destination], 'reference override')
            sources << owned!(r['source'], area)
            targets << owned!(r['destination'], 'references')
          end
          runtime_assets = if name == 'fable-method' && platform == 'codex'
                             array!(p['runtime_assets'], 'fable-method.codex.runtime_assets')
                           else
                             []
                           end
          if name == 'fable-method' && platform == 'codex'
            fail Error, 'Codex runtime asset set mismatch' unless runtime_assets.map { |asset| asset.is_a?(Hash) && asset['id'] }.compact == CODEX_RUNTIME_ASSETS.keys
            runtime_assets.each do |asset|
              fields = RUNTIME_ASSET_FIELDS[asset['id']]
              fail Error, 'unsupported Codex runtime asset' unless fields
              mapping!(asset, fields, 'runtime asset')
              contract = CODEX_RUNTIME_ASSETS.fetch(asset['id'])
              contract.each do |field, expected|
                fail Error, "Codex runtime asset #{asset['id']} #{field} mismatch" unless asset[field] == expected
              end
              if asset.key?('source_commit') || asset.key?('git_blob')
                %w[source_commit git_blob].each do |field|
                  fail Error, "Codex runtime asset #{asset['id']} #{field} must be a Git SHA-1" unless asset[field].is_a?(String) && asset[field].match?(/\A[0-9a-f]{40}\z/)
                end
              end
              fail Error, "Codex runtime asset #{asset['id']} sha256 must be a SHA-256" unless asset['sha256'].is_a?(String) && asset['sha256'].match?(/\A[0-9a-f]{64}\z/)
              fail Error, "Codex runtime asset #{asset['id']} mode mismatch" unless asset['mode'] == contract['mode']
              sources << owned!(asset['source'], 'fable-method/scripts')
              targets << self.class.relative!(asset['destination'], 'runtime asset destination')
            end
          end
          no_overlap!(targets, 'reference destination')
          destination = self.class.relative!(p['materialized_destination'], 'materialized destination')
          destinations << destination
          fail Error, 'materialized destination mismatch' unless destination == "#{MATERIALIZED}/#{platform}/#{name}"
          live = p['live_installation_path']
          fail Error, 'live installation path must be absolute' unless live.is_a?(String) && live.start_with?('/')
          self.class.relative!(live.delete_prefix('/'), 'live installation')
          fail Error, 'live installation path mismatch' unless live == LIVE.fetch(name).fetch(platform)
          live_paths << live
          fail Error, 'live installation role mismatch' unless p['live_installation_role'] == 'deployment_metadata_only'
        end
      end
      no_overlap!(sources, 'source ownership')
      no_overlap!(destinations, 'materialized destination')
      no_overlap!(live_paths, 'live installation')
      sources.each do |source|
        fail Error, 'source/destination collision' if destinations.any? { |dest| source == dest || source.start_with?(dest + '/') || dest.start_with?(source + '/') }
      end
      true
    end

    # lstat every component before reads, comparisons, mkdir, or cleanup.
    def self.safe_path!(path, allow_missing: false)
      absolute = File.expand_path(path)
      parts = absolute.split('/').reject(&:empty?)
      current = ''
      parts.each_with_index do |part, i|
        current += '/' + part
        begin
          stat = File.lstat(current)
        rescue Errno::ENOENT
          return nil if allow_missing
          raise Error, "missing path: #{current}"
        end
        fail Error, "symlink is not allowed: #{current}" if stat.symlink?
        fail Error, "wrong-type path: #{current}" unless stat.directory? || (i == parts.length - 1 && stat.file?)
        fail Error, "unreadable path: #{current}" unless File.readable?(current) && (stat.mode & 0o444).positive?
        fail Error, "unsearchable directory: #{current}" if stat.directory? && !(stat.mode & 0o111).positive?
      end
      File.lstat(absolute)
    rescue SystemCallError => e
      raise Error, "unresolved path: #{path}: #{e.message}"
    end

    def self.safe_file!(path)
      fail Error, "wrong-type file: #{path}" unless safe_path!(path)&.file?
    end

    def self.inventory(root, allow_missing: false)
      stat = safe_path!(root, allow_missing: allow_missing)
      return {} unless stat
      fail Error, "wrong-type directory: #{root}" unless stat.directory?
      result = {}
      walk = lambda do |dir|
        Dir.children(dir).sort.each do |name|
          path = File.join(dir, name)
          child = safe_path!(path)
          rel = path.delete_prefix(root + '/')
          result[rel] = child.directory? ? :directory : :file
          walk.call(path) if child.directory?
        end
      end
      walk.call(root)
      result
    rescue SystemCallError => e
      raise Error, "unresolved inventory: #{e.message}"
    end

    def source_paths(name)
      common = shared(name)
      [common['skill'], *common['references'], *common['projections'].map { |p| p['source'] },
       *platforms(name).flat_map do |p|
         [p['frontmatter_source'], *p['adapter_sources'], *p['reference_overrides'].map { |r| r['source'] },
          *p.fetch('runtime_assets', []).map { |asset| asset['source'] }]
       end]
    end

    def runtime_asset_specs(name, platform)
      pair(name, platform).fetch('runtime_assets', [])
    end

    def runtime_file_modes(name, platform)
      runtime_asset_specs(name, platform).to_h do |asset|
        [asset['destination'], Integer(asset['mode'].delete_prefix('100'), 8)]
      end
    end

    def runtime_asset_bytes(root, asset)
      path = File.join(root, asset['source'])
      stat = self.class.safe_path!(path)
      fail Error, "runtime asset source is not a file: #{asset['source']}" unless stat.file?
      content = File.binread(path)
      if asset.key?('git_blob')
        git_blob = Digest::SHA1.hexdigest("blob #{content.bytesize}\0#{content}")
        fail Error, "runtime asset Git blob mismatch: #{asset['source']}" unless git_blob == asset['git_blob']
      end
      sha256 = Digest::SHA256.hexdigest(content)
      fail Error, "runtime asset SHA-256 mismatch: #{asset['source']}" unless sha256 == asset['sha256']
      expected_mode = Integer(asset['mode'].delete_prefix('100'), 8)
      fail Error, "runtime asset mode mismatch: #{asset['source']}" unless (stat.mode & 0o777) == expected_mode
      content
    end

    def read(root, relative)
      path = File.join(root, relative)
      self.class.safe_file!(path)
      File.binread(path)
    end

    def self.unfenced_lines(content)
      fence = nil
      content.each_line.with_index.map do |line, index|
        if fence
          fence = nil if line.match?(Regexp.new("\\A {0,3}#{Regexp.escape(fence[0])}{#{fence[1]},}\\s*\\z"))
          next
        end
        opening = line.match(/\A {0,3}(`{3,}|~{3,})/)
        if opening
          fence = [opening[1][0], opening[1].length]
          next
        end
        [line, index]
      end.compact
    end

    def self.project(content, selectors)
      fail Error, 'unsupported selector or selector ordering' unless selectors == DEPTH_SECTIONS
      lines = content.lines
      headings = unfenced_lines(content).map do |line, index|
        match = line.match(/\A {0,3}(\#{1,6})[ \t]+(.+?)(?:[ \t]+\#+)?[ \t]*\r?\n?\z/)
        [match[1].length, match[2], index] if match
      end
      headings.compact!
      selected = selectors.map do |selector|
        matches = headings.select { |level, title, _| level == 2 && title == selector }
        fail Error, "missing or duplicate selected heading: #{selector}" unless matches.length == 1
        matches.first
      end
      fail Error, 'projection source ordering conflict' unless selected.map(&:last) == selected.map(&:last).sort
      if headings.any? { |level, _, index| level == 1 && index > selected.first.last && index < selected.last.last }
        fail Error, 'invalid projection heading structure: sections cross an H1 boundary'
      end
      selected.map do |_, _, start|
        ending = headings.find { |level, _, index| level <= 2 && index > start }
        lines[start...(ending ? ending.last : lines.length)].join
      end.join
    end

    def self.validate_links!(files)
      files.each do |source, content|
        lines = unfenced_lines(content).map(&:first)
        targets = lines.flat_map do |line|
          inline = line.scan(/!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))/).map { |a, b| a || b }
          definition = line.match(/\A {0,3}\[[^\]\n]+\]:\s*(?:<([^>\n]+)>|(\S+))/)
          inline << (definition[1] || definition[2]) if definition
          inline
        end
        targets.each do |target|
          next if target.match?(/\A(?:https?|mailto):/i)
          path, fragment = target.split('#', 2)
          fail Error, "Markdown link path-safety violation: #{source}: #{target}" if path.start_with?('/')
          resolved = path.empty? ? source : Pathname.new(File.join(File.dirname(source), path)).cleanpath.to_s
          fail Error, "broken Markdown local link: #{source}: #{target}" unless files.key?(resolved)
          next unless fragment && !fragment.empty?
          seen = Hash.new(0)
          anchors = unfenced_lines(files[resolved]).map do |line, _|
            match = line.match(/\A {0,3}\#{1,6}\s+(.+?)\s*#*\s*$/)
            next unless match
            slug = match[1].downcase.gsub(/[^\p{L}\p{N}_\-\s]/, '').gsub(/\s/, '-')
            suffix = seen[slug]
            seen[slug] += 1
            suffix.zero? ? slug : "#{slug}-#{suffix}"
          end
          fail Error, "broken Markdown fragment: #{source}: #{target}" unless anchors.compact.include?(fragment)
        end
      end
    end

    def render(root, name, platform)
      record = pair(name, platform)
      common = shared(name)
      body = read(root, common['skill'])
      match = body.match(/\A---\n(.*?)\n---\n/m)
      fail Error, 'canonical skill frontmatter missing' unless match
      body = body[match[0].bytesize..-1]
      fail Error, 'shared SKILL.md is not below 500 lines' unless body.lines.length < 500
      frontmatter = read(root, record['frontmatter_source'])
      block = frontmatter.match(/\A---\n(.*?)\n---\n?\z/m)
      fail Error, 'frontmatter must be a complete block' unless block
      fields = self.class.yaml(block[1])
      allowed = platform == 'gemini' ? %w[name description trigger] : %w[name description]
      fail Error, 'unexpected frontmatter keys' unless fields.is_a?(Hash) && (fields.keys - allowed).empty?
      fail Error, 'frontmatter name mismatch' unless fields['name'] == name
      description = fields['description']
      fail Error, 'invalid frontmatter description' unless description.is_a?(String) && !description.empty? && description.length <= 1024 && !description.match?(/[<>]/)
      fail Error, 'Gemini trigger mismatch' if platform == 'gemini' && fields['trigger'] != "/#{name}"
      rendered = frontmatter + body
      record['adapter_sources'].each { |adapter| rendered += "\n" + read(root, adapter) }
      files = {'SKILL.md' => rendered}
      common['references'].each { |ref| files[ref.delete_prefix("#{name}/shared/")] = read(root, ref) }
      common['projections'].each { |p| files[p['destination']] = self.class.project(read(root, p['source']), p['sections']) }
      record['reference_overrides'].each { |r| files[r['destination']] = read(root, r['source']) }
      runtime_assets = runtime_asset_specs(name, platform)
      runtime_assets.each do |asset|
        fail Error, "runtime asset destination collision: #{asset['destination']}" if files.key?(asset['destination'])
        files[asset['destination']] = runtime_asset_bytes(root, asset)
      end
      unless runtime_assets.empty?
        launcher = runtime_assets.find { |asset| asset['role'] == 'launcher' }
        companion = runtime_assets.find { |asset| asset['role'] == 'required_companion' }
        artifact_controls = runtime_assets.find { |asset| asset['id'] == 'artifact_controls' }
        files['SKILL.md'] += <<~RUNTIME

          ## Codex Runtime Materialization

          - **Source-owned Script:** `#{launcher['source']}` is pinned to Git blob `#{launcher['git_blob']}` from commit `#{launcher['source_commit']}`.
          - **Materialized Runtime Asset:** the launcher is written byte-for-byte as `#{launcher['destination']}` with the manifest-declared executable mode.

          - **Required Companion:** `#{companion['destination']}` is loaded only for `--continue-paused-run`, pinned to Git blob `#{companion['git_blob']}` from commit `#{companion['source_commit']}`.
          - **Local Artifact Controls:** `#{artifact_controls['destination']}` is a standard-library helper copied from `#{artifact_controls['source']}` with SHA-256 `#{artifact_controls['sha256']}`.
          - **Installed Runtime:** the Codex installation path in `platforms.yaml` is deployment metadata only; this generator does not write there.
          - **Live Activation:** NOT RUN. Activation is a separate operation outside this source candidate.

          A Codex bundle is Runtime Distribution Ready only when every manifest-declared asset matches its declared SHA-256 and mode; pinned assets also match their Git blob. The platform generator's `--check` verifies that state. A documentation-only bundle is not Runtime Distribution Ready.
        RUNTIME
      end
      # Projection bundles must resolve both paths and fragments entirely within
      # their rendered boundary. Existing Method link checks retain their policy.
      self.class.validate_links!(files) if name == 'fable-judge'
      files
    end

    def sync(root, name, write: false)
      skill(name)
      # Finish the whole selected preflight before the first content comparison
      # or write, so an unsafe later platform cannot leave earlier ones changed.
      source_paths(name).each { |path| self.class.safe_file!(File.join(root, path)) }
      inventories = platforms(name).to_h do |p|
        [p['name'], self.class.inventory(File.join(root, p['materialized_destination']), allow_missing: true)]
      end
      bundles = platforms(name).to_h { |p| [p['name'], render(root, name, p['name'])] }
      runtime_modes = platforms(name).to_h { |p| [p['name'], runtime_file_modes(name, p['name'])] }
      # Validate the expected file/directory roles for EVERY selected platform
      # before comparing any existing bytes or writing any earlier platform.
      bundles.each do |platform, files|
        expected = {}
        files.each_key do |path|
          expected[path] = :file
          parts = path.split('/')[0...-1]
          (1..parts.length).each { |length| expected[parts.take(length).join('/')] = :directory }
        end
        expected.each do |path, kind|
          actual = inventories.fetch(platform)[path]
          fail Error, "wrong-type managed path: #{platform}/#{path}" if actual && actual != kind
        end
      end
      drift = false
      platforms(name).each do |p|
        destination = File.join(root, p['materialized_destination'])
        files = bundles.fetch(p['name'])
        inventory = inventories.fetch(p['name'])
        modes = runtime_modes.fetch(p['name'])
        expected_dirs = files.keys.flat_map do |path|
          parts = path.split('/')[0...-1]
          (1..parts.length).map { |length| parts.take(length).join('/') }
        end.uniq
        mismatch = inventory.any? { |path, kind| kind == :directory ? !expected_dirs.include?(path) : !files.key?(path) }
        files.each do |path, content|
          if inventory[path] == :directory
            fail Error, "wrong-type managed file: #{destination}/#{path}"
          end
          mismatch ||= inventory[path] != :file || File.binread(File.join(destination, path)) != content
          if inventory[path] == :file && modes.key?(path)
            mismatch ||= (File.stat(File.join(destination, path)).mode & 0o777) != modes.fetch(path)
          end
        end
        if write
          self.class.inventory(destination, allow_missing: true)
          FileUtils.mkdir_p(destination)
          inventory.each { |path, kind| File.unlink(File.join(destination, path)) if kind == :file && !files.key?(path) }
          inventory.keys.sort_by { |path| -path.count('/') }.each do |path|
            full = File.join(destination, path)
            Dir.rmdir(full) if inventory[path] == :directory && !expected_dirs.include?(path) && Dir.empty?(full)
          end
          files.each do |path, content|
            target = File.join(destination, path)
            FileUtils.mkdir_p(File.dirname(target))
            self.class.safe_path!(target, allow_missing: true)
            File.binwrite(target, content) unless File.file?(target) && File.binread(target) == content
            File.chmod(modes.fetch(path), target) if modes.key?(path) && (File.stat(target).mode & 0o777) != modes.fetch(path)
          end
          puts "WRITTEN: #{p['name']} skill=#{name}"
        else
          puts "#{mismatch ? 'DRIFT' : 'NO_DRIFT'}: #{p['name']} skill=#{name}"
          drift ||= mismatch
        end
      end
      !drift
    end
  end
end

if $PROGRAM_NAME == __FILE__
  begin
    command, manifest_path, name, *rest = ARGV
    model = Fable::PlatformManifest.load(manifest_path)
    case command
    when '--records'
      model.platforms(name).each { |p| puts [p['name'], p['materialized_destination'], p['live_installation_path']].join("\t") }
    when '--sync'
      root, mode = rest
      fail Fable::PlatformManifest::Error, 'invalid sync mode' unless %w[--check --write].include?(mode)
      exit(model.sync(root, name, write: mode == '--write') ? 0 : 1)
    else
      fail Fable::PlatformManifest::Error, 'unknown manifest command'
    end
  rescue Fable::PlatformManifest::Error, SystemCallError => e
    warn "MANIFEST_ERROR: #{e.message}"
    exit 2
  end
end
