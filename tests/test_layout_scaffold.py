"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_agent_package_lives_at_the_repo_root_not_inside_harness():
    import checklist_agent

    assert Path(checklist_agent.__file__).resolve().parent == ROOT / "checklist_agent"
    assert not (ROOT / "harness" / "checklist_agent").exists()


def test_the_agent_never_imports_the_harness_that_checks_it():
    pattern = re.compile(r"^\s*(from|import) harness\b", re.M)
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "checklist_agent").rglob("*.py")
                 if pattern.search(p.read_text())]
    assert offenders == []


def test_the_harness_package_loads_its_tasks_and_scripts_from_its_own_folder():
    from harness.runner import EVALS, load_tasks

    assert EVALS == ROOT / "harness"
    tasks = load_tasks(str(EVALS / "tasks" / "*.jsonl"))
    assert {"scaffold_overdue_readonly", "scaffold_audit_preview", "scaffold_decline_publish"} <= {
        t["id"] for t in tasks}
    for task in tasks:
        if task.get("script"):
            assert (EVALS / task["script"]).is_file(), task["script"]


def test_the_env_file_is_read_from_the_repo_root():
    import checklist_agent.run as run

    assert run.ROOT == ROOT


def test_the_project_ships_both_packages():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["checklist_agent", "harness"]
