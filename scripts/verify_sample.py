#!/usr/bin/env python3
"""Validate the fictional CSV handoff release using only Python's standard library.

Does not upload, modify, decode customer data, or execute any CSV cells.
Package text may use CRLF while GitHub text uses LF; semantic text must match.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import sys
import zipfile

EXPECTED_MEMBERS = frozenset(
    {
        "report.html",
        "issues.csv",
        "manifest.json",
        "review-notes.md",
        "READ-ME-FIRST.txt",
        "order-alias.txt",
    }
)
TRACKED_MEMBERS = ("report.html", "issues.csv", "manifest.json", "review-notes.md")
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def normalized_text(data: bytes) -> str:
    """Accept CRLF/LF formatting only, without changing actual content."""
    text = data.decode("utf-8")
    require("\r" not in text.replace("\r\n", ""), "unexpected bare carriage return")
    return text.replace("\r\n", "\n")


def validate(root: Path) -> dict[str, int]:
    source = (root / "synthetic-input.csv").read_bytes()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    require(isinstance(manifest, dict), "manifest is not a JSON object")
    require(manifest.get("source_sha256") == hashlib.sha256(source).hexdigest(),
            "input SHA-256 differs from manifest")
    require(manifest.get("source_bytes") == len(source),
            "input byte length differs from manifest")
    require(manifest.get("delimiter") == "comma", "unexpected delimiter")
    require(manifest.get("omitted_issues") == 0, "the sample must have no omitted issues")

    records = list(csv.reader(io.StringIO(normalized_text(source), newline="")))
    require(len(records) >= 2, "synthetic input must contain header and data")
    column_count = len(records[0])
    data_count = len(records) - 1
    require(column_count == manifest.get("columns"), "column count differs from manifest")
    require(data_count == manifest.get("data_records"), "record count differs from manifest")

    reader = csv.DictReader(io.StringIO(
        normalized_text((root / "issues.csv").read_bytes()), newline=""
    ))
    require(reader.fieldnames == ["severity", "record", "code", "column", "message"],
            "issue-list header changed unexpectedly")
    issues = list(reader)
    require(issues, "issues.csv is unexpectedly empty")
    for issue in issues:
        require(None not in issue and all(value is not None for value in issue.values()),
                "malformed issue row")
        require(issue["severity"] in {"error", "warning"}, "unknown issue severity")
        require(issue["record"].isdigit()
                and 1 <= int(issue["record"]) <= data_count, "invalid record position")
        require(bool(issue["code"]) and bool(issue["message"]), "missing issue code/message")
        column = issue["column"]
        if column:
            match = re.fullmatch(r"C([1-9][0-9]*)", column)
            require(match is not None and int(match.group(1)) <= column_count,
                    "issue column out of range")

    errors = sum(item["severity"] == "error" for item in issues)
    warnings = sum(item["severity"] == "warning" for item in issues)
    require(errors == manifest.get("errors"), "error count differs from manifest")
    require(warnings == manifest.get("warnings"), "warning count differs from manifest")
    require(len(issues) == manifest.get("reported_issues"),
            "reported issue count differs from manifest")
    require(manifest.get("result") == "review_required", "unexpected sample result")

    archive = root / "synthetic-delivery.zip"
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        names = [member.filename for member in members]
        require(len(names) == len(set(names)), "duplicate ZIP member names")
        require(set(names) == EXPECTED_MEMBERS, "unexpected/missing ZIP member")
        require(sum(member.file_size for member in members) <= MAX_ARCHIVE_BYTES,
                "ZIP uncompressed contents exceed bounded sample size")
        for member in members:
            require(not member.filename.startswith(("/", "\\")), "absolute archive path")
            require("/" not in member.filename and "\\" not in member.filename,
                    "nested or traversal ZIP member")
            require(not (member.flag_bits & 0x1), "encrypted ZIP member")
            require(member.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                    "unsupported ZIP compression")
            require(stat.S_IFMT(member.external_attr >> 16) != stat.S_IFLNK,
                    "symlink ZIP member")
        require(package.testzip() is None, "corrupt ZIP member or CRC mismatch")
        for name in TRACKED_MEMBERS:
            require(normalized_text(package.read(name))
                    == normalized_text((root / name).read_bytes()),
                    f"packaged {name} differs from tracked release")

    return {
        "records": data_count,
        "columns": column_count,
        "errors": errors,
        "warnings": warnings,
        "zip_members": len(EXPECTED_MEMBERS),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1],
        help="directory containing the synthetic sample and ZIP",
    )
    args = parser.parse_args()
    try:
        result = validate(args.root.resolve())
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError,
            csv.Error, zipfile.BadZipFile, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: synthetic release verified "
        f"({result['records']} records, {result['errors']} errors, "
        f"{result['warnings']} warnings, {result['zip_members']} ZIP members)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
