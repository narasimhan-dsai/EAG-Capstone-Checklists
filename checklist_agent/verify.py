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
    known = [float(token) for token in known_tokens]
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
