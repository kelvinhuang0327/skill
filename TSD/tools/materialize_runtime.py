#!/usr/bin/env python3
"""Deterministically materialize the TSD Codex runtime tree.

The runtime layout is intentionally different from the repository source layout:

    source                              runtime
    ------                              -------
    adapters/codex/SKILL.md      -->     SKILL.md        (promoted, paths rewritten)
    core/*.md                    -->     core/*.md
    <operator-supplied .docx>    -->     assets/base_template.docx

The base template is an operator-supplied local build input. It is NOT part of the
Git-owned canonical source and must be passed explicitly via --base-template.

Runtime identity is (source tree content) + (base-template content SHA-256).
Nothing in the produced tree depends on timestamps, randomness, or the host paths
used to build it.

Exit codes: 0 success, 2 fail-closed validation error.
"""

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

RUNTIME_ROOT_SOURCE = "adapters/codex/SKILL.md"
RUNTIME_BASE_TEMPLATE = "assets/base_template.docx"
SOURCE_CORE_PREFIX = "../../core/"
RUNTIME_CORE_PREFIX = "./core/"
REQUIRED_CORE = ("CHANGE_PROGRAM_CONTRACT.md", "TEMPLATE_MODE_RULES.md")


class FailClosed(Exception):
    """Raised for any validation failure; never degrade silently."""


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def promote_codex_adapter(source_root):
    """Return the runtime root SKILL.md text, with source-relative paths rewritten."""
    adapter = source_root / RUNTIME_ROOT_SOURCE
    if not adapter.is_file():
        raise FailClosed("runtime root source not found: %s" % RUNTIME_ROOT_SOURCE)
    text = adapter.read_text(encoding="utf-8")

    if not text.startswith("---"):
        raise FailClosed("%s has no YAML frontmatter; runtime root would lose "
                         "skill discovery metadata" % RUNTIME_ROOT_SOURCE)

    text = text.replace(SOURCE_CORE_PREFIX, RUNTIME_CORE_PREFIX)

    if ".." in text:
        raise FailClosed(
            "unresolved parent-directory reference remains in the promoted runtime "
            "root. Only %r is rewritten automatically; any other '..' target needs an "
            "explicit decision." % SOURCE_CORE_PREFIX)
    return text


def collect_core(source_root):
    core_dir = source_root / "core"
    if not core_dir.is_dir():
        raise FailClosed("source core/ directory not found")
    files = sorted(p.name for p in core_dir.glob("*.md"))
    if not files:
        raise FailClosed("source core/ contains no .md files")
    missing = [name for name in REQUIRED_CORE if name not in files]
    if missing:
        raise FailClosed("required core file(s) missing from source: %s"
                         % ", ".join(missing))
    return files


def validate_root_references(root_text, core_files):
    """Every ./core/<file> named by the runtime root must have been materialized."""
    referenced = set()
    marker = RUNTIME_CORE_PREFIX
    start = 0
    while True:
        i = root_text.find(marker, start)
        if i < 0:
            break
        j = i + len(marker)
        end = j
        while end < len(root_text) and root_text[end] not in ") \n\t\"'`":
            end += 1
        referenced.add(root_text[j:end])
        start = end
    unresolved = sorted(name for name in referenced if name not in core_files)
    if unresolved:
        raise FailClosed("runtime root references core file(s) that were not "
                         "materialized: %s" % ", ".join(unresolved))
    return sorted(referenced)


def materialize(source_root, base_template, out_dir):
    source_root = Path(source_root).resolve()
    base_template = Path(base_template)

    if not base_template.exists():
        raise FailClosed("--base-template does not exist")
    if not base_template.is_file():
        raise FailClosed("--base-template is not a regular file")
    try:
        with base_template.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        raise FailClosed("--base-template is not readable: %s" % exc)

    root_text = promote_codex_adapter(source_root)
    core_files = collect_core(source_root)
    referenced = validate_root_references(root_text, core_files)

    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FailClosed("--out must be an empty or non-existent directory")
    (out_dir / "core").mkdir(parents=True, exist_ok=True)
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)

    (out_dir / "SKILL.md").write_text(root_text, encoding="utf-8")
    for name in core_files:
        shutil.copyfile(source_root / "core" / name, out_dir / "core" / name)

    input_sha = sha256_file(base_template)
    shutil.copyfile(base_template, out_dir / RUNTIME_BASE_TEMPLATE)
    output_sha = sha256_file(out_dir / RUNTIME_BASE_TEMPLATE)
    if input_sha != output_sha:
        raise FailClosed("base template bytes changed during materialization")

    docx = sorted(p.relative_to(out_dir).as_posix() for p in out_dir.rglob("*.docx"))
    if docx != [RUNTIME_BASE_TEMPLATE]:
        raise FailClosed("runtime tree must contain exactly one .docx (%s); found %s"
                         % (RUNTIME_BASE_TEMPLATE, docx))

    manifest = []
    for path in sorted(p for p in out_dir.rglob("*") if p.is_file()):
        manifest.append((path.relative_to(out_dir).as_posix(), sha256_file(path)))
    manifest_hash = sha256_bytes(
        "\n".join("%s  %s" % (name, digest) for name, digest in manifest).encode())

    return {
        "manifest": manifest,
        "manifest_hash": manifest_hash,
        "base_template_input_sha256": input_sha,
        "materialized_base_template_sha256": output_sha,
        "core_files": core_files,
        "root_referenced_core": referenced,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-template", required=True,
                        help="path to the operator-supplied base template .docx")
    parser.add_argument("--out", required=True,
                        help="empty output directory for the runtime tree")
    parser.add_argument("--source-root", default=None,
                        help="TSD source root (default: parent of tools/)")
    args = parser.parse_args(argv)

    source_root = args.source_root or Path(__file__).resolve().parent.parent
    try:
        result = materialize(source_root, args.base_template, args.out)
    except FailClosed as exc:
        print("FAIL_CLOSED: %s" % exc, file=sys.stderr)
        return 2

    print("RUNTIME_ROOT_SOURCE: %s" % RUNTIME_ROOT_SOURCE)
    print("BASE_TEMPLATE_INPUT_SHA256: %s" % result["base_template_input_sha256"])
    print("MATERIALIZED_BASE_TEMPLATE_SHA256: %s"
          % result["materialized_base_template_sha256"])
    print("RUNTIME_BUILD_MANIFEST_HASH: %s" % result["manifest_hash"])
    print("FILES: %d" % len(result["manifest"]))
    for name, digest in result["manifest"]:
        print("  %s  %s" % (digest, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
