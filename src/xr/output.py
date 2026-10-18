"""uip-style output: every command prints one envelope {Result, Code, Data} (or {Result, Code, Message, Instructions}).

--output json (default) | table | plain, and --output-filter <JMESPath> on Data, as in `uip`.
"""
from __future__ import annotations

import json
import sys
from typing import Any

FORMATS = ("json", "table", "plain")


class Output:
    def __init__(self, fmt: str = "json", filter_expr: str | None = None) -> None:
        self.fmt = fmt
        self.filter_expr = filter_expr

    def success(self, code: str, data: Any) -> None:
        if self.filter_expr:
            import jmespath

            data = jmespath.search(self.filter_expr, data)
        if self.fmt == "json":
            self._print({"Result": "Success", "Code": code, "Data": data})
        elif self.fmt == "plain":
            print(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))
        else:
            print(table(data))

    def failure(self, code: str, message: str, instructions: str | None = None, data: Any = None) -> None:
        envelope: dict[str, Any] = {"Result": "Failure", "Code": code, "Message": message}
        if instructions:
            envelope["Instructions"] = instructions
        if data is not None:
            envelope["Data"] = data
        if self.fmt == "json":
            self._print(envelope)
        else:
            print(f"{code}: {message}" + (f"\n{instructions}" if instructions else ""), file=sys.stderr)

    @staticmethod
    def _print(envelope: dict) -> None:
        print(json.dumps(envelope, indent=2, ensure_ascii=False))


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = text.replace("\n", " ")
    return text if len(text) <= 60 else text[:57] + "..."


def table(data: Any) -> str:
    if isinstance(data, dict):
        rows = [{"Key": k, "Value": v} for k, v in data.items()]
    elif isinstance(data, list):
        rows = [r if isinstance(r, dict) else {"Value": r} for r in data]
    else:
        return _cell(data)
    if not rows:
        return "(no rows)"
    columns: list[str] = []
    for row in rows:
        columns += [c for c in row if c not in columns]
    cells = [[_cell(row.get(c)) for c in columns] for row in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(columns)]
    line = lambda values: "  ".join(v.ljust(w) for v, w in zip(values, widths)).rstrip()
    return "\n".join([line(columns), line(["-" * w for w in widths]), *map(line, cells)])
