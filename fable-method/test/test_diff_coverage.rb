# frozen_string_literal: true

require 'minitest/autorun'
require 'json'
require 'open3'
require 'rbconfig'
require_relative '../scripts/diff_coverage'

# Focused coverage of the load-bearing diff/coverage contract: the diff text
# alone defines the changed-line universe, and a changed line is covered only
# when an exact (post-normalization) file+line match in the supplied
# LCOV/Cobertura/JaCoCo report carries a positive hit count. Every fixture is
# hand-verified against its own @@ hunk arithmetic; nothing here infers
# semantic executability from source text.
class DiffCoverageTest < Minitest::Test
  SCRIPT = File.expand_path('../scripts/diff_coverage.rb', __dir__)

  # foo.rb: line1 (context), line2 (replaced), line3 (added), line4 (context)
  # -> changed lines [2, 3].
  DIFF_SIMPLE = <<~DIFF
    --- a/foo.rb
    +++ b/foo.rb
    @@ -1,3 +1,4 @@
     line1
    -line2
    +line2 changed
    +line3 new
     line4
  DIFF

  # Same foo.rb hunk, plus a second, later hunk in the same file
  # (old_count=2,new_start=11,new_count=3) contributing changed line [12].
  DIFF_MULTI_HUNK = <<~DIFF
    --- a/foo.rb
    +++ b/foo.rb
    @@ -1,3 +1,4 @@
     line1
    -line2
    +line2 changed
    +line3 new
     line4
    @@ -10,2 +11,3 @@
     line10
    +line11 new
     line12
  DIFF

  # alpha.rb and beta.rb each get one appended line -> changed line [2] each.
  DIFF_MULTI_FILE = <<~DIFF
    --- a/alpha.rb
    +++ b/alpha.rb
    @@ -1,1 +1,2 @@
     a1
    +a2
    --- a/beta.rb
    +++ b/beta.rb
    @@ -1,1 +1,2 @@
     b1
    +b2
  DIFF

  # A file that does not exist on the old side at all.
  DIFF_NEW_FILE = <<~DIFF
    --- /dev/null
    +++ b/new_file.rb
    @@ -0,0 +1,3 @@
    +line1
    +line2
    +line3
  DIFF

  # 3 deletions and exactly 1 addition -> changed lines must be [1] only.
  DIFF_WITH_DELETIONS = <<~DIFF
    --- a/foo.rb
    +++ b/foo.rb
    @@ -1,4 +1,2 @@
    -line1
    -line2
    -line3
    +line1 merged
     line4
  DIFF

  # Purely context + one deletion, no additions at all -> zero changed lines.
  DIFF_NO_ADDED_LINES = <<~DIFF
    --- a/foo.rb
    +++ b/foo.rb
    @@ -1,3 +1,2 @@
     line1
    -line2
     line3
  DIFF

  # lib/foo.rb: same shape as DIFF_SIMPLE but nested under lib/, so its JaCoCo
  # package ("lib") + sourcefile ("foo.rb") identity matches the diff path
  # exactly -> changed lines [2, 3].
  DIFF_LIB_FOO = <<~DIFF
    --- a/lib/foo.rb
    +++ b/lib/foo.rb
    @@ -1,3 +1,4 @@
     line1
    -line2
    +line2 changed
    +line3 new
     line4
  DIFF

  # zeta.rb (changed [2]) appears before alpha.rb (changed [2, 3]) in the diff
  # itself, so a correctly-sorted result must reorder them.
  DIFF_ORDERING = <<~DIFF
    --- a/zeta.rb
    +++ b/zeta.rb
    @@ -1,1 +1,2 @@
     z1
    +z2
    --- a/alpha.rb
    +++ b/alpha.rb
    @@ -1,1 +1,3 @@
     a1
    +a3
    +a2
  DIFF

  # lib/foo.rb and lib/bar.rb, each with one appended line -> changed [2] each.
  DIFF_PREFIX = <<~DIFF
    --- a/lib/foo.rb
    +++ b/lib/foo.rb
    @@ -1,1 +1,2 @@
     x1
    +x2
    --- a/lib/bar.rb
    +++ b/lib/bar.rb
    @@ -1,1 +1,2 @@
     y1
    +y2
  DIFF

  # src/foo.rb with one appended line -> changed [2].
  DIFF_SUFFIX_ONLY = <<~DIFF
    --- a/src/foo.rb
    +++ b/src/foo.rb
    @@ -1,1 +1,2 @@
     s1
    +s2
  DIFF

  def lcov_report(files)
    files.map do |path, lines|
      da = lines.map { |line, hits| "DA:#{line},#{hits}" }.join("\n")
      "SF:#{path}\n#{da}\nend_of_record\n"
    end.join
  end

  def cobertura_report(files)
    classes = files.map do |path, lines|
      line_xml = lines.map { |line, hits| "<line number=\"#{line}\" hits=\"#{hits}\"/>" }.join
      "<class filename=\"#{path}\"><lines>#{line_xml}</lines></class>"
    end.join
    "<coverage><packages><package name=\"p\"><classes>#{classes}</classes></package></packages></coverage>"
  end

  def jacoco_report(sourcefiles)
    grouped = sourcefiles.group_by { |(package, _name), _lines| package }
    packages = grouped.map do |package, entries|
      files_xml = entries.map do |(_package, name), lines|
        line_xml = lines.map { |line, ci| "<line nr=\"#{line}\" mi=\"0\" ci=\"#{ci}\" mb=\"0\" cb=\"0\"/>" }.join
        "<sourcefile name=\"#{name}\">#{line_xml}</sourcefile>"
      end.join
      "<package name=\"#{package}\">#{files_xml}</package>"
    end.join
    "<report name=\"r\">#{packages}</report>"
  end

  # 1. Single file, single hunk: exactly the '+' lines are changed lines.
  def test_unified_diff_single_file_single_hunk
    result = Fable::DiffCoverage.measure(
      diff: DIFF_SIMPLE, format: 'LCOV', coverage: lcov_report('foo.rb' => { 2 => 1, 3 => 1 })
    )
    assert_equal 'MEASURED', result['STATUS']
    assert_equal 2, result['TOTAL_CHANGED_LINES']
    assert_equal 2, result['COVERED_CHANGED_LINES']
    assert_equal 0, result['UNCOVERED_CHANGED_LINES']
    assert_equal 100.0, result['DIFF_COVERAGE_PERCENT']
    assert_empty result['UNCOVERED']
  end

  # 2. A second, later hunk in the same file contributes its own changed lines.
  def test_unified_diff_multiple_hunks
    result = Fable::DiffCoverage.measure(
      diff: DIFF_MULTI_HUNK, format: 'LCOV', coverage: lcov_report('foo.rb' => { 2 => 1, 3 => 1, 12 => 1 })
    )
    assert_equal 3, result['TOTAL_CHANGED_LINES']
    assert_equal 3, result['COVERED_CHANGED_LINES']
  end

  # 3. Multiple files in one diff are each tracked under their own path.
  def test_unified_diff_multiple_files
    result = Fable::DiffCoverage.measure(diff: DIFF_MULTI_FILE, format: 'LCOV', coverage: '')
    assert_equal 2, result['TOTAL_CHANGED_LINES']
    assert_equal 0, result['COVERED_CHANGED_LINES']
    assert_equal(
      [{ 'file' => 'alpha.rb', 'line' => 2 }, { 'file' => 'beta.rb', 'line' => 2 }],
      result['UNCOVERED']
    )
  end

  # 4. A newly added file (old side /dev/null) has every '+' line counted.
  def test_newly_added_file
    result = Fable::DiffCoverage.measure(
      diff: DIFF_NEW_FILE, format: 'LCOV', coverage: lcov_report('new_file.rb' => { 1 => 1, 2 => 0, 3 => 1 })
    )
    assert_equal 3, result['TOTAL_CHANGED_LINES']
    assert_equal 2, result['COVERED_CHANGED_LINES']
    assert_equal [{ 'file' => 'new_file.rb', 'line' => 2 }], result['UNCOVERED']
  end

  # 5. Deleted lines never enter the changed-line universe.
  def test_deleted_lines_excluded_from_changed_line_universe
    result = Fable::DiffCoverage.measure(
      diff: DIFF_WITH_DELETIONS, format: 'LCOV', coverage: lcov_report('foo.rb' => { 1 => 1 })
    )
    assert_equal 1, result['TOTAL_CHANGED_LINES']
    assert_equal 1, result['COVERED_CHANGED_LINES']
  end

  # 6. LCOV: exact covered/uncovered split.
  def test_lcov_exact_covered_and_uncovered_result
    result = Fable::DiffCoverage.measure(
      diff: DIFF_SIMPLE, format: 'LCOV', coverage: lcov_report('foo.rb' => { 2 => 1, 3 => 0 })
    )
    assert_equal 2, result['TOTAL_CHANGED_LINES']
    assert_equal 1, result['COVERED_CHANGED_LINES']
    assert_equal [{ 'file' => 'foo.rb', 'line' => 3 }], result['UNCOVERED']
    assert_equal 50.0, result['DIFF_COVERAGE_PERCENT']
  end

  # 7. Cobertura: exact covered/uncovered split, from the class-level <lines>.
  def test_cobertura_exact_covered_and_uncovered_result
    result = Fable::DiffCoverage.measure(
      diff: DIFF_SIMPLE, format: 'COBERTURA', coverage: cobertura_report('foo.rb' => { 2 => 0, 3 => 5 })
    )
    assert_equal 1, result['COVERED_CHANGED_LINES']
    assert_equal [{ 'file' => 'foo.rb', 'line' => 2 }], result['UNCOVERED']
  end

  # 8. JaCoCo: package + sourcefile identity, covered when ci > 0.
  def test_jacoco_exact_covered_and_uncovered_result
    result = Fable::DiffCoverage.measure(
      diff: DIFF_LIB_FOO, format: 'JACOCO',
      coverage: jacoco_report({ %w[lib foo.rb] => { 2 => 0, 3 => 7 } })
    )
    assert_equal 1, result['COVERED_CHANGED_LINES']
    assert_equal [{ 'file' => 'lib/foo.rb', 'line' => 2 }], result['UNCOVERED']
  end

  # 9. Output ordering is always file-ascending then line-ascending, regardless
  # of the diff's own file encounter order.
  def test_deterministic_output_ordering
    result = Fable::DiffCoverage.measure(diff: DIFF_ORDERING, format: 'LCOV', coverage: '')
    assert_equal(
      [{ 'file' => 'alpha.rb', 'line' => 2 }, { 'file' => 'alpha.rb', 'line' => 3 },
       { 'file' => 'zeta.rb', 'line' => 2 }],
      result['UNCOVERED']
    )
  end

  # 10. Leading ./ and Git a/ diff-prefix normalization on the coverage side.
  def test_leading_prefix_normalization
    coverage = "SF:./lib/foo.rb\nDA:2,1\nend_of_record\nSF:a/lib/bar.rb\nDA:2,1\nend_of_record\n"
    result = Fable::DiffCoverage.measure(diff: DIFF_PREFIX, format: 'LCOV', coverage: coverage)
    assert_equal 2, result['TOTAL_CHANGED_LINES']
    assert_equal 2, result['COVERED_CHANGED_LINES']
    assert_empty result['UNCOVERED']
  end

  # 11. Zero changed lines is NOT_APPLICABLE, never a misleading 100%, and
  # needs no format/coverage at all.
  def test_zero_changed_line_case_is_not_applicable
    result = Fable::DiffCoverage.measure(diff: DIFF_NO_ADDED_LINES)
    assert_equal 'NOT_APPLICABLE', result['STATUS']
    assert_nil result['DIFF_COVERAGE_PERCENT']
    assert_equal 0, result['TOTAL_CHANGED_LINES']

    assert_equal 'NOT_APPLICABLE', Fable::DiffCoverage.measure(diff: '')['STATUS']
  end

  # 12. Malformed coverage input fails closed with an explicit diagnostic.
  def test_malformed_coverage_input_fails_closed
    assert_raises(Fable::DiffCoverage::InvalidInput) do
      Fable::DiffCoverage.measure(diff: DIFF_SIMPLE, format: 'LCOV', coverage: "SF:foo.rb\nDA:2,notanumber\nend_of_record\n")
    end
    assert_raises(Fable::DiffCoverage::InvalidInput) do
      Fable::DiffCoverage.measure(diff: DIFF_SIMPLE, format: 'LCOV', coverage: "SF:foo.rb\nDA:2,1\n")
    end
    assert_raises(Fable::DiffCoverage::InvalidInput) do
      Fable::DiffCoverage.measure(diff: DIFF_SIMPLE, format: 'COBERTURA', coverage: '<coverage><packages>')
    end
    assert_raises(Fable::DiffCoverage::InvalidInput) do
      Fable::DiffCoverage.measure(
        diff: DIFF_SIMPLE, format: 'JACOCO',
        coverage: '<report><package name=""><sourcefile name="foo.rb"><line nr="2"/></sourcefile></package></report>'
      )
    end
  end

  # 13. Ambiguous/unresolvable identity never falls back to fuzzy matching.
  def test_ambiguous_or_unresolvable_identity_does_not_fuzzy_match
    # A bare basename in the report is not the same file as src/foo.rb.
    suffix_only = Fable::DiffCoverage.measure(
      diff: DIFF_SUFFIX_ONLY, format: 'LCOV', coverage: lcov_report('foo.rb' => { 2 => 1 })
    )
    assert_equal 0, suffix_only['COVERED_CHANGED_LINES']
    assert_equal [{ 'file' => 'src/foo.rb', 'line' => 2 }], suffix_only['UNCOVERED']

    # An absolute coverage path with no repo root is unresolvable.
    assert_raises(Fable::DiffCoverage::InvalidInput) do
      Fable::DiffCoverage.measure(diff: DIFF_SIMPLE, format: 'LCOV', coverage: "SF:/abs/foo.rb\nDA:2,1\nend_of_record\n")
    end

    # An absolute coverage path IS resolved when it is inside a supplied root.
    resolved = Fable::DiffCoverage.measure(
      diff: DIFF_LIB_FOO, format: 'LCOV',
      coverage: "SF:/repo/lib/foo.rb\nDA:2,1\nDA:3,1\nend_of_record\n", repo_root: '/repo'
    )
    assert_equal 2, resolved['COVERED_CHANGED_LINES']
  end

  # 14. A changed file legitimately absent from the report is uncovered.
  def test_uncovered_changed_file_absent_from_report_is_counted_as_uncovered
    result = Fable::DiffCoverage.measure(
      diff: DIFF_MULTI_FILE, format: 'LCOV', coverage: lcov_report('alpha.rb' => { 2 => 1 })
    )
    assert_equal 2, result['TOTAL_CHANGED_LINES']
    assert_equal 1, result['COVERED_CHANGED_LINES']
    assert_equal [{ 'file' => 'beta.rb', 'line' => 2 }], result['UNCOVERED']
  end

  # 15. The result is measurement only: no PASS/FAIL/THRESHOLD verdict field
  # ever appears, at 0%, partial, or 100% coverage, and the API accepts no
  # threshold input at all.
  def test_no_implicit_threshold_or_pass_verdict
    expected_keys = %w[
      STATUS COVERAGE_FORMAT TOTAL_CHANGED_LINES COVERED_CHANGED_LINES
      UNCOVERED_CHANGED_LINES DIFF_COVERAGE_PERCENT UNCOVERED
    ].sort

    fully_covered = Fable::DiffCoverage.measure(diff: DIFF_SIMPLE, format: 'LCOV', coverage: lcov_report('foo.rb' => { 2 => 1, 3 => 1 }))
    fully_uncovered = Fable::DiffCoverage.measure(diff: DIFF_SIMPLE, format: 'LCOV', coverage: lcov_report('foo.rb' => { 2 => 0, 3 => 0 }))
    assert_equal expected_keys, fully_covered.keys.sort
    assert_equal expected_keys, fully_uncovered.keys.sort

    assert_raises(ArgumentError) do
      Fable::DiffCoverage.measure(diff: DIFF_SIMPLE, format: 'LCOV', coverage: lcov_report('foo.rb' => { 2 => 1, 3 => 1 }), threshold: 90)
    end
  end

  def test_cli_round_trip
    ruby = RbConfig.ruby
    payload = JSON.generate(
      'diff' => DIFF_SIMPLE, 'format' => 'LCOV', 'coverage' => lcov_report('foo.rb' => { 2 => 1, 3 => 0 })
    )
    out, _err, status = Open3.capture3(ruby, SCRIPT, stdin_data: payload)
    assert_equal 0, status.exitstatus
    result = JSON.parse(out)
    assert_equal 'MEASURED', result['STATUS']
    assert_equal 2, result['TOTAL_CHANGED_LINES']
    assert_equal 1, result['COVERED_CHANGED_LINES']

    _out, err, status = Open3.capture3(ruby, SCRIPT, stdin_data: 'not json')
    assert_equal 2, status.exitstatus
    assert_equal 'INVALID_INPUT', JSON.parse(err)['error']
  end
end
