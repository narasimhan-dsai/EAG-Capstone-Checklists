"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from harness.runner import FAMILIES, load_tasks
from harness.verifiers import REGISTRY

ROOT = Path(__file__).resolve().parents[1]


def test_the_platform_config_is_at_the_repo_root_and_names_our_command():
    config = tomllib.loads((ROOT / "agentswitch-harness.toml").read_text())
    assert config["run"] == "python -m harness.runner --official"
    assert config["install"] == "pip install -r requirements.txt"
    assert config["results"] == "results.json" and config["instances"] == ["suryodaya"]
    assert 1 <= config["timeout_minutes"] <= 30


def test_requirements_match_the_project_dependencies():
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    required = [line.strip() for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip()]
    assert sorted(required) == sorted(declared)


def test_python_311_is_enough_because_the_runner_cannot_download_interpreters():
    floor = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
    assert re.fullmatch(r">=3\.(9|10|11)", floor), floor


def test_the_results_file_is_not_committed():
    assert "results.json" in (ROOT / ".gitignore").read_text().split()


def test_the_official_suite_is_well_formed_and_arms_only_the_tasks_that_write():
    tasks = load_tasks(str(ROOT / "harness" / "tasks" / "official.jsonl"))
    assert 8 <= len(tasks) <= 200 and len({t["id"] for t in tasks}) == len(tasks)
    assert tasks[0]["id"] == "seat_request"                       # the seat's own request goes first
    for task in tasks:
        assert task["transport"] == "live" and task["family"] in FAMILIES
        assert (task["mode"] == "live") == bool(task["allow"]), task["id"]
        assert all(spec["type"] in REGISTRY for spec in task["verifiers"]), task["id"]
        if task["allow"]:
            assert any(spec["type"] in {"sop.start_safety_audit", "sop.process_to_sop", "recurring_runs_created",
                                        "followups_raised", "template_drafted_inactive"} for spec in task["verifiers"])
        else:
            assert any(spec["type"] == "no_write_calls" for spec in task["verifiers"]), task["id"]


def test_a_plain_local_run_uses_only_the_safe_scaffold_tasks_and_never_the_official_mode():
    from harness.runner import build_parser
    args = build_parser().parse_args([])
    assert args.official is False and args.dry is False and args.out == "results.json"
    assert Path(args.tasks).name == "scaffold.jsonl"
    official = build_parser().parse_args(["--official", "--dry"])
    assert official.official is True and official.dry is True
