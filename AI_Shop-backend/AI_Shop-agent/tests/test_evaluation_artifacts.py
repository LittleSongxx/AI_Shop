from __future__ import annotations

import subprocess

from app.evaluation.artifacts import workspace_sha256


def _git(repo, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_workspace_sha256_handles_git_quoted_unicode_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(
        repo,
        "-c",
        "user.name=AI Shop Test",
        "-c",
        "user.email=test@aishop.local",
        "commit",
        "-qm",
        "initial",
    )

    unicode_path = repo / (("中文评测文件" * 10) + ".md")
    unicode_path.write_text("first\n", encoding="utf-8")

    first = workspace_sha256(repo)
    unicode_path.write_text("second\n", encoding="utf-8")
    second = workspace_sha256(repo)

    assert len(first) == 64
    assert len(second) == 64
    assert first != second
