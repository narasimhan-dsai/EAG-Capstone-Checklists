"""Check what a model wrote against the data it was given. Pure and deterministic.

A model's wording is not evidence. Before an answer reaches the user, every figure,
date and record id in it must be one the evidence (or the user's own request) contains.
When it is not, the answer is corrected once and then replaced by a plain rendering of
the data, so an invented number never ships.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?!\d)")
# The time of day in a timestamp ("T17:07:07.123456+00:00") is a clock reading, not a figure anyone claims.
_TIME = re.compile(r"(?<=\d)T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")
_ID = re.compile(r"\b[A-Z]{2,}-\d{4}-\d+\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
# A step number in a list ("1." or "2)") is formatting, not a claim.
_MARKER = re.compile(r"(?m)(?:^|(?<=\s))\d{1,2}[.)](?=\s)")
# Numbers glued to a word ("5S", "Q4", "74mm") are labels, not figures.
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
_EPSILON = 1e-9
_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_WORD_NUMBERS = {**_UNITS, **_TENS, "hundred": 100, "thousand": 1000}
_WORDS = re.compile(r"[a-z]+")
_COMPOUND = re.compile(rf"\b({'|'.join(_TENS)})[ -]({'|'.join(u for u in _UNITS if 0 < _UNITS[u] < 10)})\b")


def _word_values(text: str) -> list[float]:
    """Figures written as words ("one minute", "twenty-five"): a model may write them as digits."""
    lowered = text.lower()
    values = [float(_TENS[t] + _UNITS[u]) for t, u in _COMPOUND.findall(lowered)]
    return values + [float(_WORD_NUMBERS[w]) for w in _WORDS.findall(lowered) if w in _WORD_NUMBERS]
# Models often write the hyphens inside dates and ids as non-breaking hyphens or en dashes; they are the
# same characters to a reader. An em dash (U+2014) is real punctuation and is left alone.
_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2212": "-"})


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_TEXT_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_TEXT_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")


def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _month(word: str) -> int | None:
    return _MONTHS.index(word[:3].lower()) + 1 if word[:3].lower() in _MONTHS else None


def _written_dates(text: str) -> tuple[str, list[tuple[str, set[str]]]]:
    """Dates written other than YYYY-MM-DD, as (text, the ISO dates it could mean), and the text without them."""
    found: list[tuple[str, set[str]]] = []

    def take(match: re.Match[str], candidates: list[str | None]) -> str:
        options = {c for c in candidates if c}
        if not options:
            return match.group(0)  # not a real date (a count, a version): leave it to the number check
        found.append((match.group(0), options))
        return " "

    def dmy(m: re.Match[str]) -> str:
        month = _month(m.group(2))
        return take(m, [_iso(int(m.group(3)), month, int(m.group(1)))] if month else [])

    def mdy(m: re.Match[str]) -> str:
        month = _month(m.group(1))
        return take(m, [_iso(int(m.group(3)), month, int(m.group(2)))] if month else [])

    def numeric(m: re.Match[str]) -> str:
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return take(m, [_iso(year, b, a), _iso(year, a, b)])  # day/month or month/day

    text = _NUMERIC_DATE.sub(numeric, _TEXT_DMY.sub(dmy, _TEXT_MDY.sub(mdy, text)))
    return text, found


def _tokens(text: str) -> tuple[set[str], list[str]]:
    ids = set(_ID.findall(text)) | set(_DATE.findall(text))
    rest = _MARKER.sub(" ", _DATE.sub(" ", _ID.sub(" ", text)))
    return ids, [token.replace(",", "") for token in _NUMBER.findall(rest)]


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def ungrounded(text: str, evidence: Any, extra: str = "") -> dict[str, list[str]]:
    """Figures and ids in ``text`` that appear nowhere in ``evidence`` or ``extra``.

    A rounded figure is accepted (67 for 66.7); a different one is not."""
    text = _TIME.sub(" ", text.translate(_HYPHENS))
    evidence_text = _TIME.sub(" ", (json.dumps(evidence, ensure_ascii=False, default=str) + " " + extra)
                              .translate(_HYPHENS))
    known_ids, known_tokens = _tokens(evidence_text)
    known = [float(token) for token in known_tokens] + _word_values(evidence_text)
    fractional = [value for value in known if abs(value - round(value)) > _EPSILON]
    text, written = _written_dates(text)
    ids, numbers = _tokens(text)
    # A date written another way ("7 Oct 2026") is grounded when that exact date is in the evidence.
    unknown_dates = [shown for shown, options in written if not options & known_ids]

    def grounded(token: str) -> bool:
        value = float(token)
        return (any(abs(value - k) < _EPSILON for k in known)
                or any(abs(round(f, _decimals(token)) - value) < _EPSILON for f in fractional))

    seen: set[str] = set()
    bad_numbers = [t for t in numbers if not grounded(t) and not (t in seen or seen.add(t))]
    return {"numbers": bad_numbers, "ids": sorted(ids - known_ids) + unknown_dates}


_SCALARS = (str, int, float, bool, type(None))
_DEPTH = 3
_LIST_SHOWN = 5


def _brief(item: Any) -> str:
    if isinstance(item, dict):
        label = next((str(item[k]) for k in ("name", "title", "id") if item.get(k)), "")
        ident = f" ({item['id']})" if item.get("id") and label != str(item["id"]) else ""
        extras = [f"{k}: {v}" for k, v in item.items()
                  if k not in ("name", "title", "id") and isinstance(v, _SCALARS) and v not in (None, "")]
        return f"{label}{ident}" + (f" [{', '.join(extras)}]" if extras else "")
    return str(item)


def _lines(value: Any, depth: int, indent: str = "  ") -> list[str]:
    out: list[str] = []
    if not isinstance(value, dict):
        return [f"{indent}{_brief(value)}"]
    for key, item in value.items():
        if isinstance(item, _SCALARS):
            out.append(f"{indent}{key}: {item}")
        elif isinstance(item, dict) and all(isinstance(v, _SCALARS) for v in item.values()):
            out.append(f"{indent}{key}: " + ", ".join(f"{k}: {v}" for k, v in item.items()))
        elif isinstance(item, dict) and depth < _DEPTH:
            out.append(f"{indent}{key}:")
            out.extend(_lines(item, depth + 1, indent + "  "))
        elif isinstance(item, list):
            out.append(f"{indent}{key}:")
            out.extend(f"{indent}  - {_brief(entry)}" for entry in item[:_LIST_SHOWN])
    return out


def render_evidence(evidence: list[dict[str, Any]]) -> str:
    """The evidence as plain text, built without a model so it cannot contain an invented figure."""
    blocks = []
    for item in evidence:
        result = item.get("result")
        if isinstance(result, dict) and result.get("error"):
            blocks.append(f"{item['capability']}: FAILED ({result.get('code')}): {result.get('message')}")
        else:
            blocks.append(f"{item['capability']}:\n" + "\n".join(_lines(result, 1)))
    return "\n\n".join(blocks)


# What the model is shown of a result: the totals, the splits and a few identifying rows, never whole rows.
_ROW_KEYS = ("id", "number", "name", "title", "template", "template_id", "status", "due_date", "days_late",
             "days_waiting", "blocked_on", "owner", "reviewer", "frequency", "period", "period_end", "category",
             "action", "run_id", "code", "message", "reason", "error", "blockers_pending", "assigned",
             "severity", "subject", "issue", "runs", "assignee")
_ROWS_SHOWN = 8
_TEXT_SHOWN = 160


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _compact(item) for key, item in value.items()}
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            rows: list[Any] = [{key: _compact(row[key]) for key in _ROW_KEYS if key in row}
                               for row in value[:_ROWS_SHOWN]]
            if len(value) > _ROWS_SHOWN:
                rows.append({"_omitted": len(value) - _ROWS_SHOWN})
            return rows
        return [_compact(item) for item in value[: _ROWS_SHOWN * 3]]
    if isinstance(value, str) and len(value) > _TEXT_SHOWN:
        return value[:_TEXT_SHOWN] + "…"
    return value


def compact_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The evidence as the model should see it. Each capability's result keeps its counts and splits; a list of
    rows becomes its first few rows reduced to identifying fields, plus ``{"_omitted": n}``. The answer is still
    checked against the full evidence, so shortening what the model reads cannot let an invented figure through."""
    return [{**entry, "input": _compact(entry.get("input")), "result": _compact(entry.get("result"))}
            for entry in evidence]
