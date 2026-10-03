"""Turn a run record and verifier results into exactly one of pass / fail / infra_error.

``infra_error`` means the run could not be judged (gateway quota, upstream
outage, login failure, incomplete snapshot, a verifier that could not read
state). It never counts as ``fail``, so a quota outage is not read as the
agent being wrong.
"""
from __future__ import annotations

import re
from typing import Any

PASS, FAIL, INFRA = "pass", "fail", "infra_error"

# Gateway/transport trouble, as it appears in a planner "failed visibly" reason
# or an exception message.
_INFRA = re.compile(r"returned (?:429|502|503)\b|TimeoutError|ReadTimeout|ConnectError|ConnectTimeout|"
                    r"ScriptExhausted|quota|login failed|ReadError|RemoteProtocolError|PoolTimeout|WriteError", re.I)


_INFRA_CODES = {"transport_error", "incomplete_read"}


def infra_reason(record: dict[str, Any]) -> str | None:
    """Why the run itself was infrastructure-broken, or None."""
    if record.get("preflight_error"):
        return f"preflight: {record['preflight_error']}"
    if record.get("exception") and _INFRA.search(record["exception"]["message"]):
        return f"run raised: {record['exception']['message'][:200]}"
    for node in ((record.get("result") or {}).get("nodes") or {}).values():
        outcome = node.get("result")
        if isinstance(outcome, dict) and outcome.get("code") in _INFRA_CODES:
            return f"{node.get('skill')} could not read AgentSwitch: {str(outcome.get('message'))[:200]}"
    for event in (record.get("result") or {}).get("patch_events", []):
        reason = event.get("reason") or ""
        if "planner call failed visibly" in reason and _INFRA.search(reason):
            return reason[:240]
    return None


def score(record: dict[str, Any], verifier_results: list[dict[str, Any]]) -> dict[str, Any]:
    """``verifier_results`` entries are ``{claim, ok, observed}`` or ``{claim, infra_error}``."""
    reason = infra_reason(record)
    if reason:
        return {"state": INFRA, "reason": reason}
    broken = [r for r in verifier_results if "infra_error" in r]
    if broken:
        return {"state": INFRA, "reason": broken[0]["infra_error"]}
    if not verifier_results:
        return {"state": INFRA, "reason": "task has no verifiers; nothing to judge"}
    failed = [r["claim"] for r in verifier_results if not r["ok"]]
    return {"state": FAIL, "failed_claims": failed} if failed else {"state": PASS}


def exit_code(states: list[str]) -> int:
    """0 all pass (or nothing ran); 1 any fail; 2 infra errors only."""
    if FAIL in states:
        return 1
    return 2 if INFRA in states else 0
