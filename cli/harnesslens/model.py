"""NDJSON evidence parsing, state selection and contract evaluation."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any


class EvidenceError(ValueError):
    pass


def parse_evidence(data: bytes, *, expected_run: str | None = None) -> list[dict]:
    if not data.strip():
        raise EvidenceError("Empty capture (recorder not enabled or no events emitted)")
    records = []
    for n, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except (UnicodeDecodeError, ValueError) as exc:
            raise EvidenceError(f"Malformed evidence at line {n}: {exc}") from exc
        if not isinstance(r, dict) or r.get("type") not in ("capture_ready", "event", "state"):
            raise EvidenceError(f"Invalid record type at line {n}")
        if r.get("type") == "capture_ready" and r.get("version") != 1:
            raise EvidenceError(f"Unsupported readiness version at line {n}")
        if r.get("type") == "event" and (not isinstance(r.get("name"), str) or not isinstance(r.get("params"), dict)):
            raise EvidenceError(f"Invalid analytics event at line {n}")
        if r.get("type") == "state" and (not isinstance(r.get("scope"), str) or not isinstance(r.get("values"), dict)):
            raise EvidenceError(f"Invalid state entry at line {n}")
        if expected_run is not None and r.get("run_id") != expected_run:
            raise EvidenceError(f"Run ID mismatch at line {n}")
        records.append(r)
    if not any(r["type"] == "capture_ready" for r in records):
        raise EvidenceError("No capture_ready marker: recording could not be verified")
    return records


def read_file(path: Path, *, expected_run: str | None = None) -> list[dict]:
    try:
        return parse_evidence(path.read_bytes(), expected_run=expected_run)
    except OSError as exc:
        raise EvidenceError(f"Unable to read capture: {exc}") from exc


def state_history(records: list[dict], scope: str) -> list[dict]:
    return [r for r in records if r["type"] == "state" and r["scope"] == scope]


def state_get(records: list[dict], scope: str) -> dict:
    matches = state_history(records, scope)
    if not matches:
        raise EvidenceError(f"No state for '{scope}' (cannot conclude the state was false)")
    return matches[-1]


def field_value(mapping: dict, path: str) -> Any:
    if path in mapping:
        return mapping[path]
    value: Any = mapping
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise EvidenceError(f"State field not exported: {path}")
        value = value[part]
    return value


def analytics_events(records: list[dict]) -> list[dict]:
    return [r for r in records if r["type"] == "event"]


def state_contract(contract: dict, records: list[dict]) -> dict:
    if contract.get("schema_version") != 1 or not isinstance(contract.get("assertions"), list):
        raise EvidenceError("State contract requires schema_version:1 and assertions array")
    checks = []
    for rule in contract["assertions"]:
        if not isinstance(rule, dict) or not all(k in rule for k in ("scope", "field", "equals")):
            raise EvidenceError("Each assertion requires scope, field, equals")
        try:
            state = state_get(records, rule["scope"])
            actual = field_value(state["values"], rule["field"])
            ok = type(actual) is type(rule["equals"]) and actual == rule["equals"]
            checks.append({"scope": rule["scope"], "field": rule["field"], "passed": ok,
                           "expected": rule["equals"], "actual": actual})
        except EvidenceError as exc:
            checks.append({"scope": rule["scope"], "field": rule["field"], "passed": False,
                           "inconclusive": True, "reason": str(exc)})
    status = ("INCONCLUSIVE" if any(c.get("inconclusive") for c in checks) else
              "PASS" if all(c["passed"] for c in checks) else "FAIL")
    return {"status": status, "checks": checks}
