"""Before/after capture of AgentSwitch state, read with the agent's own seat.

A ``watch`` entry is ``{"tool", "args", "key", "fields"}``: call a list tool,
index its rows by ``key`` and keep only ``fields``. Taken through the
*un-wrapped* client, so snapshot reads never show up as agent tool calls.
If the tool reports more rows than were fetched, the entry is marked
incomplete and verifiers depending on it report infra_error, not pass.
"""
from __future__ import annotations

from typing import Any

_MAX_PAGES = 50


async def capture(client: Any, jurisdiction: str, watch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [await _capture_one(client, jurisdiction, entry) for entry in watch]


async def _capture_one(client: Any, jurisdiction: str, entry: dict[str, Any]) -> dict[str, Any]:
    args = dict(entry.get("args") or {})
    page_size = int(args.get("limit") or 100)
    args["limit"] = page_size
    key, fields = entry["key"], entry.get("fields") or []
    rows: dict[str, dict[str, Any]] = {}
    total: int | None = None
    offset = int(args.get("offset") or 0)
    fetched = 0
    for _ in range(_MAX_PAGES):
        page = await client.call_tool(entry["tool"], {**args, "offset": offset}, jurisdiction=jurisdiction)
        data = page.get("data") if isinstance(page, dict) else None
        if not isinstance(data, list):
            raise ValueError(f"watch tool {entry['tool']} did not return a list payload")
        total = page.get("total", total)
        for row in data:
            rows[str(row.get(key))] = {field: row.get(field) for field in fields}
        fetched += len(data)
        offset += len(data)
        if not data or total is None or fetched >= total:
            break
    complete = total is None or fetched >= total
    return {"tool": entry["tool"], "args": entry.get("args") or {}, "key": key, "fields": fields,
            "total": total, "fetched": fetched, "complete": complete, "rows": rows}
