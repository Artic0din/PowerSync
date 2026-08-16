from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.validate_support_fix import (
    changed_paths,
    requested_pull_request,
    validate_fix,
)


def git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def repository_with_regression(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.email", "tests@example.com")
    git(repository, "config", "user.name", "Tests")
    (repository / "calculator.py").write_text(
        "def total(values: list[int]) -> int:\n    return 0\n",
        encoding="utf-8",
    )
    tests = repository / "tests"
    tests.mkdir()
    (tests / "test_calculator.py").write_text(
        "from calculator import total\n\n\ndef test_total() -> None:\n"
        "    assert total([]) == 0\n",
        encoding="utf-8",
    )
    git(repository, "add", ".")
    git(repository, "commit", "-m", "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repository / "calculator.py").write_text(
        "def total(values: list[int]) -> int:\n    return sum(values)\n",
        encoding="utf-8",
    )
    (tests / "test_calculator.py").write_text(
        "from calculator import total\n\n\ndef test_total() -> None:\n"
        "    assert total([2, 3]) == 5\n",
        encoding="utf-8",
    )
    return repository, base_sha


def repository_with_node_regression(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "node-repository"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.email", "tests@example.com")
    git(repository, "config", "user.name", "Tests")
    (repository / "calculator.mjs").write_text(
        "export function total(values) { return 0; }\n",
        encoding="utf-8",
    )
    tests = repository / "tests"
    tests.mkdir()
    (tests / "calculator.test.mjs").write_text(
        'import assert from "node:assert/strict";\n'
        'import test from "node:test";\n'
        'import { total } from "../calculator.mjs";\n\n'
        'test("empty total", () => assert.equal(total([]), 0));\n',
        encoding="utf-8",
    )
    git(repository, "add", ".")
    git(repository, "commit", "-m", "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repository / "calculator.mjs").write_text(
        "export function total(values) { "
        "return values.reduce((sum, value) => sum + value, 0); }\n",
        encoding="utf-8",
    )
    (tests / "calculator.test.mjs").write_text(
        'import assert from "node:assert/strict";\n'
        'import test from "node:test";\n'
        'import { total } from "../calculator.mjs";\n\n'
        'test("total", () => assert.equal(total([2, 3]), 5));\n',
        encoding="utf-8",
    )
    return repository, base_sha


def test_pull_request_request_detection_accepts_ndjson(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs.jsonl"
    outputs.write_text(
        json.dumps({"type": "add_comment", "body": "checked"})
        + "\n"
        + json.dumps({"type": "create_pull_request", "title": "fix: example"})
        + "\n",
        encoding="utf-8",
    )

    assert requested_pull_request(outputs) is True


def test_changed_paths_include_committed_and_untracked_files(tmp_path: Path) -> None:
    repository, base_sha = repository_with_regression(tmp_path)
    git(repository, "add", "calculator.py")
    git(repository, "commit", "-m", "fix calculator")
    (repository / "tests" / "test_extra.py").write_text(
        "def test_extra() -> None:\n    assert True\n",
        encoding="utf-8",
    )

    assert changed_paths(repository, base_sha) == {
        Path("calculator.py"),
        Path("tests/test_calculator.py"),
        Path("tests/test_extra.py"),
    }


def test_validation_requires_regression_failure_before_fix(tmp_path: Path) -> None:
    repository, base_sha = repository_with_regression(tmp_path)

    validate_fix(repository, base_sha)


def test_validation_supports_node_regression_tests(tmp_path: Path) -> None:
    repository, base_sha = repository_with_node_regression(tmp_path)

    validate_fix(repository, base_sha)


def test_validation_preserves_git_context_in_pre_fix_worktree(tmp_path: Path) -> None:
    repository, base_sha = repository_with_regression(tmp_path)
    (repository / "tests" / "test_calculator.py").write_text(
        "import subprocess\n\n\ndef test_git_context() -> None:\n"
        "    result = subprocess.run(\n"
        "        ['git', 'rev-parse', '--is-inside-work-tree'],\n"
        "        check=True, capture_output=True, text=True,\n"
        "    )\n"
        "    assert result.stdout.strip() == 'true'\n"
        "    from calculator import total\n"
        "    assert total([]) == 0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="did not fail against the pre-fix revision"):
        validate_fix(repository, base_sha)


def test_validation_rejects_test_that_already_passed(tmp_path: Path) -> None:
    repository, base_sha = repository_with_regression(tmp_path)
    (repository / "tests" / "test_calculator.py").write_text(
        "from calculator import total\n\n\ndef test_empty_total() -> None:\n"
        "    assert total([]) == 0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="did not fail against the pre-fix revision"):
        validate_fix(repository, base_sha)


def test_validation_rejects_failing_final_regression(tmp_path: Path) -> None:
    repository, base_sha = repository_with_regression(tmp_path)
    (repository / "calculator.py").write_text(
        "def total(values: list[int]) -> int:\n    return 1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not pass on the proposed fix"):
        validate_fix(repository, base_sha)
