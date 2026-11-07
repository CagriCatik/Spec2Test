"""CSV and Excel export of the flat row shape.

This is the interim export that keeps the existing buttons working. A full
export with separate tables for test cases, steps, requirement links and open
questions is AP 6.
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Dict, Sequence

from ...application.legacy import LEGACY_KEYS, LEGACY_REQUIRED

COLUMNS = list(LEGACY_KEYS)


def _serialise(value) -> str:
    if isinstance(value, list):
        return "\n".join(map(str, value))
    return str(value)


def save_csv(rows: Sequence[Dict], path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _serialise(row.get(key, "")) for key in COLUMNS})
    return path


def save_rows_xlsx(rows: Sequence[Dict], path: Path | str) -> Path:
    import pandas as pd
    from openpyxl.utils import get_column_letter

    frame = pd.DataFrame(
        [{key: _serialise(row.get(key, "")) for key in COLUMNS} for row in rows],
        columns=COLUMNS,
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="TestCases")
        sheet = writer.sheets["TestCases"]
        for index, column in enumerate(frame.columns, 1):
            values = [str(v) for v in frame[column] if str(v).strip()]
            if not values:
                continue
            width = max(max(map(len, values)), len(column)) + 2
            sheet.column_dimensions[get_column_letter(index)].width = width
    return path


# ------------------------------------------------------------- analytics ----
def _normalise_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,;\n]+", value) if part.strip()]
    return []


def _count_steps(value) -> int:
    if isinstance(value, list):
        return len([v for v in value if str(v).strip()])
    if isinstance(value, str):
        return len([v for v in re.split(r"\n+|;", value) if v.strip()])
    return 0


def summarise_rows(rows: Sequence[Dict]) -> Dict[str, object]:
    """Quick analytics for the insight panel."""
    missing_counts = {field: 0 for field in COLUMNS}
    complete = 0
    requirement_counter: Counter[str] = Counter()
    name_counter: Counter[str] = Counter()
    action_lengths: list[int] = []
    result_lengths: list[int] = []

    for row in rows:
        incomplete = False
        for field in COLUMNS:
            value = row.get(field, "")
            if not str(value).strip():
                missing_counts[field] += 1
                if field in LEGACY_REQUIRED:
                    incomplete = True
        if not incomplete:
            complete += 1
        for requirement in _normalise_list(row.get("linked_requirements", [])):
            requirement_counter[requirement] += 1
        name = str(row.get("testcase_name", "")).strip()
        if name:
            name_counter[name] += 1
        action_lengths.append(_count_steps(row.get("actions")))
        result_lengths.append(_count_steps(row.get("expected_results")))

    avg_actions = mean([n for n in action_lengths if n]) if any(action_lengths) else 0
    avg_results = mean([n for n in result_lengths if n]) if any(result_lengths) else 0
    return {
        "total_cases": len(rows),
        "complete_cases": complete,
        "missing_fields": missing_counts,
        "unique_requirements": len(requirement_counter),
        "top_requirements": requirement_counter.most_common(5),
        "duplicate_names": [n for n, c in name_counter.items() if c > 1],
        "avg_actions": round(avg_actions, 2) if avg_actions else 0,
        "avg_expected_results": round(avg_results, 2) if avg_results else 0,
    }


#: deprecated: the name said nothing about which exporter it is, and the
#: specification exporter of the same name was imported by mistake. Remove
#: once no caller outside this repository uses it.
save_xlsx = save_rows_xlsx
