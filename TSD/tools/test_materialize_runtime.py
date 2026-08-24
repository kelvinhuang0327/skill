#!/usr/bin/env python3
"""Focused tests for the TSD runtime materializer.

The base template is treated as an opaque binary, so these tests use a tiny
synthetic payload. The real production template is never copied into fixtures
and never enters the repository.

Run: python3 tools/test_materialize_runtime.py
"""

import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import materialize_runtime as mr  # noqa: E402

SOURCE_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_BYTES = b"PK\x03\x04synthetic-opaque-base-template-fixture"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("%-58s %s%s" % (name, "PASS" if ok else "FAIL",
                          "  " + detail if detail and not ok else ""))


def fixture(tmp):
    path = Path(tmp) / "operator_supplied.docx"
    path.write_bytes(FIXTURE_BYTES)
    return path


def build(tmp, sub="out", template=None, source_root=None):
    return mr.materialize(source_root or SOURCE_ROOT,
                          template if template is not None else fixture(tmp),
                          Path(tmp) / sub)


def main():
    # 1. missing --base-template fails closed (argparse rejects before build)
    with tempfile.TemporaryDirectory() as tmp:
        rc = None
        try:
            rc = mr.main(["--out", str(Path(tmp) / "o")])
        except SystemExit as exc:
            rc = exc.code
        check("1  missing --base-template fails closed", rc not in (0, None), "rc=%r" % rc)

    # 2. nonexistent input fails closed
    with tempfile.TemporaryDirectory() as tmp:
        try:
            build(tmp, template=Path(tmp) / "absent.docx")
            check("2  nonexistent --base-template fails closed", False)
        except mr.FailClosed:
            check("2  nonexistent --base-template fails closed", True)

    # 2b. a directory passed as the template fails closed
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "adir.docx"
        d.mkdir()
        try:
            build(tmp, template=d)
            check("2b non-regular-file --base-template fails closed", False)
        except mr.FailClosed:
            check("2b non-regular-file --base-template fails closed", True)

    with tempfile.TemporaryDirectory() as tmp:
        res = build(tmp)
        out = Path(tmp) / "out"
        names = [n for n, _ in res["manifest"]]

        # 3. explicit input is materialized at the neutral canonical path
        check("3  input materialized as assets/base_template.docx",
              (out / "assets/base_template.docx").is_file()
              and "assets/base_template.docx" in names)

        # 4. byte identity preserved
        check("4  input/output SHA-256 identical",
              res["base_template_input_sha256"]
              == res["materialized_base_template_sha256"]
              == mr.sha256_bytes(FIXTURE_BYTES))
        check("4b manifest hash algorithm and purpose are explicit",
              res["manifest_hash_algorithm"] == mr.MATERIALIZER_MANIFEST_HASH_ALGORITHM
              and res["manifest_hash_purpose"] == mr.MATERIALIZER_MANIFEST_HASH_PURPOSE)

        # 5. exactly one .docx in the runtime tree
        check("5  exactly one runtime .docx",
              [n for n in names if n.endswith(".docx")]
              == ["assets/base_template.docx"])

        # 6. no historical/production source filename required or emitted
        blob = "\n".join(names) + (out / "SKILL.md").read_text(encoding="utf-8")
        check("6  no legacy production template filename in runtime",
              "EOS_v28_0421.docx" not in blob
              and "example_template_20260428" not in blob)

        # 8. Codex adapter promoted to root, frontmatter preserved
        root = (out / "SKILL.md").read_text(encoding="utf-8")
        adapter = (SOURCE_ROOT / "adapters/codex/SKILL.md").read_text(encoding="utf-8")
        check("8  runtime root promoted from adapters/codex/SKILL.md",
              root.startswith("---") and "name: TSD" in root
              and root == adapter.replace("../../core/", "./core/"))

        # 9. path transformation applied, no parent traversal remains
        check("9  ../../core/ rewritten to ./core/ with no '..' left",
              "../../core/" not in root and ".." not in root
              and "./core/CHANGE_PROGRAM_CONTRACT.md" in root)

        # 10. all source core/*.md materialized, required ones present
        src_core = sorted(p.name for p in (SOURCE_ROOT / "core").glob("*.md"))
        check("10 all core/*.md materialized",
              sorted(p.name for p in (out / "core").glob("*.md")) == src_core)
        check("11 CHANGE_PROGRAM_CONTRACT.md present in runtime",
              (out / "core/CHANGE_PROGRAM_CONTRACT.md").is_file())
        check("12 TEMPLATE_MODE_RULES.md present in runtime",
              (out / "core/TEMPLATE_MODE_RULES.md").is_file())

        # 13. source root SKILL.md is not the runtime entrypoint
        src_root_skill = (SOURCE_ROOT / "SKILL.md").read_text(encoding="utf-8")
        check("13 source root SKILL.md not used as runtime entrypoint",
              root != src_root_skill and not src_root_skill.startswith("---"))

        # 14. excluded source trees absent from runtime
        check("14 adapters/examples/scripts/tools/reports excluded",
              not any(n.startswith(p) for n in names for p in
                      ("adapters/", "examples/", "scripts/", "tools/"))
              and not any("SELF_CHECK" in n or "UPDATE_REPORT" in n or n == "README.md"
                          for n in names))

        # 15. every ./core reference in the runtime root resolves
        check("15 all runtime root core references resolve",
              all((out / "core" / n).is_file() for n in res["root_referenced_core"])
              and len(res["root_referenced_core"]) >= 5)

        # 16. no host build path leaked into runtime content
        check("16 no host/production absolute path in runtime content",
              not any("/Users/" in (out / n).read_text(encoding="utf-8", errors="ignore")
                      for n in names if n.endswith(".md")))

    # 7. two builds from the same source + same fixture are byte-identical
    with tempfile.TemporaryDirectory() as tmp:
        tpl = fixture(tmp)
        a = build(tmp, "a", template=tpl)
        b = build(tmp, "b", template=tpl)
        check("7  two builds byte-identical (manifest + hashes)",
              a["manifest"] == b["manifest"]
              and a["manifest_hash"] == b["manifest_hash"]
              and a["base_template_input_sha256"] == b["base_template_input_sha256"])

    # 7b. the CLI labels the materializer hash instead of conflating it with a
    # separately computed live-tree hash.
    with tempfile.TemporaryDirectory() as tmp:
        rendered = StringIO()
        template = fixture(tmp)
        with redirect_stdout(rendered):
            rc = mr.main(["--base-template", str(template),
                          "--out", str(Path(tmp) / "cli-out")])
        report = rendered.getvalue()
        legacy_label = "RUNTIME_BUILD_" + "MANIFEST_HASH:"
        check("7b CLI labels materializer hash algorithm and purpose",
              rc == 0
              and "MATERIALIZER_MANIFEST_HASH_ALGORITHM: " in report
              and "MATERIALIZER_MANIFEST_HASH_PURPOSE: " in report
              and "MATERIALIZER_MANIFEST_HASH: " in report
              and legacy_label not in report)

    # 17. non-empty --out refuses to build over existing content
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "dirty"
        out.mkdir()
        (out / "stale.txt").write_text("x", encoding="utf-8")
        try:
            build(tmp, "dirty")
            check("17 non-empty --out fails closed", False)
        except mr.FailClosed:
            check("17 non-empty --out fails closed", True)

    # 18. adapter without frontmatter fails closed (discovery-metadata guard)
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "src"
        shutil.copytree(SOURCE_ROOT / "core", fake / "core")
        (fake / "adapters/codex").mkdir(parents=True)
        (fake / "adapters/codex/SKILL.md").write_text("# no frontmatter\n",
                                                      encoding="utf-8")
        try:
            build(tmp, "o18", source_root=fake)
            check("18 root source without frontmatter fails closed", False)
        except mr.FailClosed:
            check("18 root source without frontmatter fails closed", True)

    # 19. unrewritable parent traversal fails closed
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "src"
        shutil.copytree(SOURCE_ROOT / "core", fake / "core")
        (fake / "adapters/codex").mkdir(parents=True)
        (fake / "adapters/codex/SKILL.md").write_text(
            "---\nname: X\n---\nsee [a](../../assets/other.docx)\n", encoding="utf-8")
        try:
            build(tmp, "o19", source_root=fake)
            check("19 unresolved '..' reference fails closed", False)
        except mr.FailClosed:
            check("19 unresolved '..' reference fails closed", True)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n%d/%d passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("FAILED: %s" % ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
