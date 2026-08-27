from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import tokenize
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from evaluation.core.contracts import EvaluationCase
from evaluation.core.io import REPO_ROOT

_SOURCE_SUFFIXES = frozenset(
    {
        ".cfg",
        ".css",
        ".graphql",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsonl",
        ".jsx",
        ".kt",
        ".kts",
        ".md",
        ".properties",
        ".proto",
        ".py",
        ".scss",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".vue",
        ".xml",
        ".yaml",
        ".yml",
    }
)
_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".holdouts",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".runs",
        ".state",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "evaluation-evidence",
        "final-inputs",
        "node_modules",
        "run",
        "target",
    }
)
_JSON_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')
_MIN_EXACT_INPUT_CHARS = 4
_MIN_PARTIAL_FRAGMENT_CHARS = 12
_MIN_PARTIAL_INPUT_COVERAGE = 0.60


@dataclass(frozen=True)
class _SourceLiteral:
    path: Path
    line: int
    normalized: str


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if not character.isspace())


def _case_inputs(case: EvaluationCase) -> Iterator[tuple[str, str]]:
    query = case.input.get("query")
    if isinstance(query, str) and query.strip():
        yield "query", query
    turns = case.input.get("turns")
    if not isinstance(turns, list):
        return
    for index, turn in enumerate(turns, 1):
        if not isinstance(turn, dict):
            continue
        message = turn.get("message")
        if isinstance(message, str) and message.strip():
            yield f"turn:{index}", message


def _repository_source_paths(root: Path) -> Iterator[Path]:
    for current, directories, filenames in os.walk(root):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory not in _EXCLUDED_DIRECTORY_NAMES
        )
        current_path = Path(current)
        for filename in sorted(filenames):
            path = current_path / filename
            if path.suffix.casefold() in _SOURCE_SUFFIXES:
                yield path


def _python_literals(path: Path) -> Iterator[_SourceLiteral]:
    try:
        source = tokenize.open(path).read()
    except (OSError, UnicodeError, SyntaxError):
        return
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        # A dirty worktree can temporarily contain an incomplete Python file.
        # Token-level decoding still finds valid string literals. A separate
        # visible-line pass below also catches comments and incomplete text.
        try:
            tokens = tokenize.generate_tokens(iter(source.splitlines(keepends=True)).__next__)
            for token in tokens:
                if token.type != tokenize.STRING:
                    continue
                try:
                    value = ast.literal_eval(token.string)
                except (SyntaxError, ValueError):
                    continue
                if isinstance(value, str):
                    normalized = _normalize(value)
                    if len(normalized) >= _MIN_EXACT_INPUT_CHARS:
                        yield _SourceLiteral(path, token.start[0], normalized)
        except (IndentationError, tokenize.TokenError):
            return
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        normalized = _normalize(node.value)
        if len(normalized) >= _MIN_EXACT_INPUT_CHARS:
            yield _SourceLiteral(path, int(getattr(node, "lineno", 1)), normalized)


def _json_literals(path: Path) -> Iterator[_SourceLiteral]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return
    for line_number, line in enumerate(lines, 1):
        for match in _JSON_STRING_RE.finditer(line):
            try:
                value = json.loads(match.group(0))
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(value, str):
                continue
            normalized = _normalize(value)
            if len(normalized) >= _MIN_EXACT_INPUT_CHARS:
                yield _SourceLiteral(path, line_number, normalized)


def _visible_text_lines(path: Path) -> Iterator[_SourceLiteral]:
    """Scan all developer-visible text, including comments and documentation."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return
    for line_number, line in enumerate(lines, 1):
        normalized = _normalize(line)
        if len(normalized) >= _MIN_EXACT_INPUT_CHARS:
            yield _SourceLiteral(path, line_number, normalized)


def _source_literals(paths: Iterable[Path]) -> list[_SourceLiteral]:
    literals: list[_SourceLiteral] = []
    for path in paths:
        if path.suffix.casefold() == ".py":
            literals.extend(_python_literals(path))
        elif path.suffix.casefold() in {".json", ".jsonl"}:
            literals.extend(_json_literals(path))
        literals.extend(_visible_text_lines(path))
    return literals


def _exposed_fragment(input_text: str, source_text: str) -> str | None:
    if len(input_text) < _MIN_EXACT_INPUT_CHARS:
        return None
    if input_text in source_text:
        return input_text
    if (
        source_text in input_text
        and len(source_text) >= _MIN_PARTIAL_FRAGMENT_CHARS
        and len(source_text) / len(input_text) >= _MIN_PARTIAL_INPUT_COVERAGE
    ):
        return source_text
    return None


def _portable_source_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def audit_final_input_exposure(
    cases: Sequence[EvaluationCase],
    *,
    dataset_path: Path | None = None,
    repository_root: Path = REPO_ROOT,
    source_paths: Iterable[Path] | None = None,
) -> list[dict[str, object]]:
    """Find final inputs recoverable from developer-visible repository text.

    The returned records intentionally omit all matched text. They are safe to
    include in lifecycle errors and audit logs without disclosing a new final
    input to a developer who did not already have access to it.
    """

    excluded = dataset_path.resolve() if dataset_path is not None else None
    paths = source_paths if source_paths is not None else _repository_source_paths(repository_root)
    scanned_paths = [
        path.resolve()
        for path in paths
        if path.is_file() and (excluded is None or path.resolve() != excluded)
    ]
    literals = _source_literals(scanned_paths)
    findings: list[dict[str, object]] = []
    seen: set[tuple[str, str, int, str]] = set()
    for case in cases:
        for input_kind, raw_input in _case_inputs(case):
            normalized_input = _normalize(raw_input)
            for literal in literals:
                fragment = _exposed_fragment(normalized_input, literal.normalized)
                if fragment is None:
                    continue
                digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
                source_path = _portable_source_path(literal.path, repository_root)
                identity = (case.case_id, source_path, literal.line, digest)
                if identity in seen:
                    continue
                seen.add(identity)
                findings.append(
                    {
                        "caseId": case.case_id,
                        "inputKind": input_kind,
                        "sourcePath": source_path,
                        "line": literal.line,
                        "matchSha256": digest,
                        "matchChars": len(fragment),
                    }
                )
    return sorted(
        findings,
        key=lambda item: (
            str(item["caseId"]),
            str(item["sourcePath"]),
            int(item["line"]),
            str(item["matchSha256"]),
        ),
    )


def exposure_error_summary(findings: Sequence[dict[str, object]], *, limit: int = 20) -> str:
    rendered = [
        f"{item['caseId']}@{item['sourcePath']}:{item['line']}"
        f"#{str(item['matchSha256'])[:12]}"
        for item in findings[:limit]
    ]
    if len(findings) > limit:
        rendered.append(f"... and {len(findings) - limit} more")
    return ", ".join(rendered)


def final_exposure_audit_report(
    cases: Sequence[EvaluationCase],
    findings: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Project exposure findings without final input text or match metadata."""

    safe_findings = [
        {
            "caseId": str(item["caseId"]),
            "sourcePath": str(item["sourcePath"]),
            "line": int(item["line"]),
            "matchSha256": str(item["matchSha256"]),
        }
        for item in findings
    ]
    return {
        "schemaVersion": "aishop-final-source-exposure-audit/v1",
        "auditStatus": "HISTORICAL_FINAL_SOURCE_EXPOSED",
        "counts": {
            "auditedCases": len(cases),
            "exposedCases": len({item["caseId"] for item in safe_findings}),
            "findings": len(safe_findings),
            "sourceFiles": len({item["sourcePath"] for item in safe_findings}),
        },
        "findings": safe_findings,
    }
