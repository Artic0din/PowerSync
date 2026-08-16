"""Deterministically gate support-fix pull requests on regression evidence."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def requested_pull_request(output_path: Path) -> bool:
    """Return whether agent safe output requests pull-request creation."""
    if not output_path.is_file():
        return False
    for line in output_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Agent safe output is not valid JSON") from error
        candidates: list[Any]
        if isinstance(item, dict) and isinstance(item.get("items"), list):
            candidates = item["items"]
        else:
            candidates = [item]
        if any(
            isinstance(candidate, dict)
            and candidate.get("type") == "create_pull_request"
            for candidate in candidates
        ):
            return True
    return False


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def changed_paths(repository: Path, base_sha: str) -> set[Path]:
    """Return committed, staged, working-tree, and untracked changes from base."""
    tracked = _git(repository, "diff", "--name-only", "-z", base_sha).split("\0")
    untracked = _git(
        repository,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    ).split("\0")
    return {Path(path) for path in tracked + untracked if path}


def _is_test_file(path: Path) -> bool:
    if len(path.parts) <= 1 or path.parts[0] != "tests":
        return False
    return (path.name.startswith("test_") and path.suffix == ".py") or (
        path.name.endswith(".test.mjs")
    )


def _run_tests(
    repository: Path, test_files: list[Path]
) -> list[subprocess.CompletedProcess[str]]:
    python_tests = [path for path in test_files if path.suffix == ".py"]
    node_tests = [path for path in test_files if path.name.endswith(".test.mjs")]
    commands = []
    if python_tests:
        commands.append(
            [sys.executable, "-m", "pytest", "-q", *(str(path) for path in python_tests)]
        )
    if node_tests:
        commands.append(["node", "--test", *(str(path) for path in node_tests)])
    return [
        subprocess.run(
            command,
            cwd=repository,
            capture_output=True,
            text=True,
        )
        for command in commands
    ]


def _format_results(results: list[subprocess.CompletedProcess[str]]) -> str:
    return "".join(result.stdout + result.stderr for result in results)


def validate_fix(repository: Path, base_sha: str) -> None:
    """Require changed tests to fail before the fix and pass after it."""
    paths = changed_paths(repository, base_sha)
    test_files = sorted(
        path for path in paths if _is_test_file(path) and (repository / path).is_file()
    )
    production_paths = [
        path for path in paths if path.parts and path.parts[0] != "tests"
    ]
    if not test_files:
        raise ValueError("A requested support fix has no changed regression test")
    if not production_paths:
        raise ValueError("A requested support fix has no production change")

    with tempfile.TemporaryDirectory(prefix="powersync-support-base-") as temporary:
        base_repository = Path(temporary) / "repository"
        _git(repository, "worktree", "add", "--detach", str(base_repository), base_sha)
        try:
            for path in paths:
                if not path.parts or path.parts[0] != "tests":
                    continue
                source = repository / path
                destination = base_repository / path
                if source.is_file():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                elif destination.exists():
                    destination.unlink()
            pre_fix_results = _run_tests(base_repository, test_files)
        finally:
            _git(repository, "worktree", "remove", "--force", str(base_repository))
    pre_fix_statuses = {result.returncode for result in pre_fix_results}
    if not pre_fix_statuses.issubset({0, 1}) or 1 not in pre_fix_statuses:
        raise ValueError(
            "The changed regression test did not fail against the pre-fix revision "
            "with a supported test-failure status "
            f"(got {sorted(pre_fix_statuses)}):\n"
            + _format_results(pre_fix_results)
        )

    final_results = _run_tests(repository, test_files)
    if any(result.returncode != 0 for result in final_results):
        raise ValueError(
            "The changed regression test does not pass on the proposed fix:\n"
            + _format_results(final_results)
        )


def main() -> int:
    output_path = Path(os.environ.get("GH_AW_SAFE_OUTPUTS", ""))
    if not requested_pull_request(output_path):
        return 0
    repository = Path(os.environ.get("GITHUB_WORKSPACE", ""))
    base_sha = os.environ.get("GITHUB_SHA", "")
    if not repository.is_dir() or not base_sha:
        raise ValueError("The workflow is missing support-fix validation metadata")
    validate_fix(repository, base_sha)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"::error title=Support fix validation failed::{error}", file=sys.stderr)
        raise SystemExit(1) from error
