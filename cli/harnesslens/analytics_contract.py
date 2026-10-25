#!/usr/bin/env python3
"""Validate debug-only app analytics capture against a declared event contract.

Only the Python standard library is required. Exit 0 PASS, 1 FAIL,
2 INCONCLUSIVE (capture issue), 3 INVALID_INPUT (bad contract/report I/O).
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

TYPES = {"string", "integer", "number", "boolean", "null"}


def _primitive_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "unsupported"


def _equivalent(actual: Any, expected: Any) -> bool:
    return _primitive_type(actual) == _primitive_type(expected) and actual == expected


def load_contract(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Contract must be a JSON object with schema_version=1")
    if not isinstance(value.get("scenario"), str) or not value["scenario"].strip():
        raise ValueError("Contract must have non-empty scenario")
    expected = value.get("expected_events")
    if not isinstance(expected, list) or not expected:
        raise ValueError("expected_events must be a non-empty array")
    names: set[str] = set()
    for item in expected:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
            raise ValueError("Every expected event must have a name")
        if item["name"] in names:
            raise ValueError(f"Repeated event spec for {item['name']} (use count instead)")
        names.add(item["name"])
        count = item.get("count")
        if not isinstance(count, dict) or not count or set(count).difference({"exact", "min", "max"}):
            raise ValueError(f"{item['name']}: count must declare exact or min/max")
        if "exact" in count and len(count) != 1:
            raise ValueError(f"{item['name']}: exact cannot be combined with min/max")
        if any(type(v) is not int or v < 0 for v in count.values()):
            raise ValueError(f"{item['name']}: count values must be nonnegative integers")
        if "min" in count and "max" in count and count["min"] > count["max"]:
            raise ValueError(f"{item['name']}: min cannot exceed max")
        rules = item.get("params", {})
        if not isinstance(rules, dict):
            raise ValueError(f"{item['name']}: params must be a map")
        for param, rule in rules.items():
            if not isinstance(param, str) or not isinstance(rule, dict):
                raise ValueError(f"{item['name']}: invalid parameter rule")
            if set(rule).difference({"required", "type", "equals", "one_of", "matches"}):
                raise ValueError(f"{item['name']}.{param}: unknown rule key")
            if "required" in rule and type(rule["required"]) is not bool:
                raise ValueError(f"{item['name']}.{param}: required must be boolean")
            if "type" in rule and rule["type"] not in TYPES:
                raise ValueError(f"{item['name']}.{param}: unsupported type")
            if "one_of" in rule and (not isinstance(rule["one_of"], list) or not rule["one_of"]):
                raise ValueError(f"{item['name']}.{param}: one_of must be non-empty array")
            if "matches" in rule:
                if not isinstance(rule["matches"], str):
                    raise ValueError(f"{item['name']}.{param}: matches must be regex string")
                re.compile(rule["matches"])
    forbidden = value.get("forbidden_events", [])
    if not isinstance(forbidden, list) or any(not isinstance(v, str) for v in forbidden):
        raise ValueError("forbidden_events must be string array")
    if not isinstance(value.get("allow_unexpected", True), bool):
        raise ValueError("allow_unexpected must be boolean")
    order = value.get("order", [])
    if not isinstance(order, list) or any(not isinstance(v, str) for v in order):
        raise ValueError("order must be string array")
    for name in order:
        if name not in names:
            raise ValueError(f"order references undeclared expected event: {name}")
    return value


def read_capture(path: Path) -> tuple[list[dict], str | None]:
    if not path.is_file():
        return [], "Capture file missing; check debug recorder and device extraction"
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return [], "Capture file empty; app recorder may not have initialized"
    items = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            return [], f"Capture malformed on line {line_number}"
        if not isinstance(item, dict) or item.get("type") not in {"capture_ready", "event"}:
            return [], f"Unsupported capture record on line {line_number}"
        if item["type"] == "event" and (
            not isinstance(item.get("name"), str) or not isinstance(item.get("params"), dict)
        ):
            return [], f"Invalid event record on line {line_number}"
        items.append(item)
    if not any(x.get("type") == "capture_ready" and x.get("version") == 1 for x in items):
        return [], "No version-1 capture_ready marker; cannot verify recorder health"
    return items, None


def _check_param(value: Any, rule: dict) -> bool:
    if "type" in rule:
        actual_type = _primitive_type(value)
        if actual_type != rule["type"] and not (rule["type"] == "number" and actual_type == "integer"):
            return False
    if "equals" in rule and not _equivalent(value, rule["equals"]):
        return False
    if "one_of" in rule and not any(_equivalent(value, expected) for expected in rule["one_of"]):
        return False
    if "matches" in rule and (not isinstance(value, str) or re.fullmatch(rule["matches"], value) is None):
        return False
    return True


def evaluate(contract: dict, capture_path: Path) -> dict:
    items, issue = read_capture(capture_path)
    report = {
        "schema_version": 1,
        "scenario": contract["scenario"],
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "INCONCLUSIVE" if issue else "PASS",
        "capture_issue": issue,
        "event_count": 0,
        "timeline": [],
        "checks": [],
        "limitations": ["App-side instrumentation verification does not prove GA4 ingestion or reporting"],
    }
    if issue:
        return report
    events = [record for record in items if record["type"] == "event"]
    report["event_count"] = len(events)
    report["timeline"] = [event["name"] for event in events]  # No captured values in report
    checks = report["checks"]

    def add(check_id: str, passed: bool, detail: str) -> None:
        checks.append({"id": check_id, "status": "PASS" if passed else "FAIL", "detail": detail})

    for rule in contract["expected_events"]:
        name = rule["name"]
        matching = [event for event in events if event["name"] == name]
        count = rule["count"]
        if "exact" in count:
            count_ok = len(matching) == count["exact"]
            wanted = f"exactly {count['exact']}"
        else:
            count_ok = len(matching) >= count.get("min", 0) and len(matching) <= count.get("max", float("inf"))
            wanted = f"min={count.get('min', 0)}, max={count.get('max', 'unbounded')}"
        add(f"count:{name}", count_ok, f"Expected {wanted}; observed {len(matching)}")
        for param, spec in rule.get("params", {}).items():
            missing = sum(param not in evt["params"] for evt in matching)
            invalid = sum(
                param in evt["params"] and not _check_param(evt["params"][param], spec)
                for evt in matching
            )
            required = spec.get("required", True)
            passes = (not required or missing == 0) and invalid == 0
            # No matching event is handled by count check; do not duplicate the failure.
            if matching:
                add(f"param:{name}.{param}", passes,
                    f"Across {len(matching)} matching events: missing={missing}, invalid={invalid}; required={required}")

    forbidden = set(contract.get("forbidden_events", []))
    for name in sorted(forbidden):
        count = sum(event["name"] == name for event in events)
        add(f"forbidden:{name}", count == 0, f"Observed {count} forbidden occurrences")

    if not contract.get("allow_unexpected", True):
        expected_names = {event["name"] for event in contract["expected_events"]}
        unexpected = sorted({event["name"] for event in events} - expected_names)
        add("no-unexpected-events", not unexpected,
            "Unexpected event names: " + (", ".join(unexpected) if unexpected else "none"))

    order = contract.get("order", [])
    if order:
        names = [event["name"] for event in events]
        cursor = 0
        for name in names:
            if cursor < len(order) and name == order[cursor]:
                cursor += 1
        add("order", cursor == len(order), f"Expected ordered subsequence: {' → '.join(order)}")

    if any(c["status"] == "FAIL" for c in checks):
        report["status"] = "FAIL"
    return report


def render_markdown(report: dict) -> str:
    lines = [f"# Analytics Contract Report — {report['scenario']}", "",
             f"**Status:** {report['status']}", f"**Captured events:** {report['event_count']}", ""]
    if report["capture_issue"]:
        lines.extend([f"**Capture issue:** {report['capture_issue']}", ""])
    lines.extend(["## Checks", "", "| Check | Status | Detail |", "|---|---|---|"])
    for check in report["checks"]:
        def safe(value: str) -> str:
            return value.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {safe(check['id'])} | {check['status']} | {safe(check['detail'])} |")
    if not report["checks"]:
        lines.append("| Capture readiness | INCONCLUSIVE | No assertions evaluated |")
    lines.extend(["", "## Event timeline (names only)", "",
                  ", ".join(report["timeline"]) if report["timeline"] else "None captured", "",
                  "## Limitations", ""])
    for limitation in report["limitations"]:
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        report = evaluate(contract, args.capture)
        args.report_dir.mkdir(parents=True, exist_ok=True)
        (args.report_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (args.report_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    except (ValueError, OSError, json.JSONDecodeError, re.error) as exc:
        print(f"INVALID_INPUT: {exc}", file=sys.stderr)
        return 3
    print(f"{report['status']}: {report['scenario']} — {report['event_count']} events; report: {args.report_dir}")
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
