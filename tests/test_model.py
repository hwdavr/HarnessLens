import json
import sys
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))
from harnesslens.model import (EvidenceError, parse_evidence, read_file, state_history, state_get,
                               field_value, state_contract, analytics_events)
from harnesslens.cli import main

SAMPLE = ROOT / "examples/evidence.example.ndjson"
RUN_ID = "a" * 32


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.records = read_file(SAMPLE, expected_run=RUN_ID)

    def test_state_latest_and_history(self):
        self.assertEqual(state_get(self.records, "ev.payment")["values"]["status"], "confirmed")
        self.assertEqual(len(state_history(self.records, "ev.payment")), 2)

    def test_state_contract_pass(self):
        contract = json.loads((ROOT / "contracts/state.example.json").read_text())
        self.assertEqual(state_contract(contract, self.records)["status"], "PASS")

    def test_state_contract_fail(self):
        contract = {"schema_version": 1, "assertions": [{"scope": "ev.payment", "field": "status", "equals": "declined"}]}
        self.assertEqual(state_contract(contract, self.records)["status"], "FAIL")

    def test_state_contract_inconclusive_if_unexposed(self):
        contract = {"schema_version": 1, "assertions": [{"scope": "ev.payment", "field": "accessToken", "equals": "secret"}]}
        self.assertEqual(state_contract(contract, self.records)["status"], "INCONCLUSIVE")

    def test_analytics(self):
        self.assertEqual(analytics_events(self.records)[0]["name"], "charging_completed")

    def test_rejects_missing_capture_ready(self):
        with self.assertRaisesRegex(EvidenceError, "capture_ready"):
            parse_evidence(b'{"type":"state","scope":"hi","values":{}}\n')

    def test_rejects_wrong_run(self):
        with self.assertRaisesRegex(EvidenceError, "Run ID mismatch"):
            read_file(SAMPLE, expected_run="b" * 32)

    def test_rejects_truncated_ndjson(self):
        with self.assertRaisesRegex(EvidenceError, "Malformed"):
            parse_evidence(b'{"type":"capture_ready","version":1}\n{"type":"state"')

    def test_flattened_dot_name(self):
        self.assertEqual(field_value({"payment.status": "ok"}, "payment.status"), "ok")

    def test_cli_state_get(self):
        self.assertEqual(main(["state", "get", "--file", str(SAMPLE), "--scope", "ev.payment"]), 0)

    def test_cli_state_await(self):
        self.assertEqual(main(["state", "await", "--file", str(SAMPLE), "--scope", "ev.payment", "--field", "status", "--equals", "confirmed", "--timeout", "0"]), 0)

    def test_cli_state_verify(self):
        self.assertEqual(main(["state", "verify", "--file", str(SAMPLE), "--contract", str(ROOT / "contracts/state.example.json")]), 0)

    def test_cli_analytics_verify_uses_existing_validator_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            status = main(["analytics", "verify", "--file", str(SAMPLE),
                           "--contract", str(ROOT / "contracts/analytics.example.json"),
                           "--report-dir", temp])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads((Path(temp) / "report.json").read_text())["status"], "PASS")

    def test_no_analytics_events_is_inconclusive_not_product_bug(self):
        records = [r for r in self.records if r["type"] != "event"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state_only.ndjson"
            path.write_text("".join(json.dumps(r) + "\n" for r in records))
            status = main(["analytics", "verify", "--file", str(path),
                "--contract", str(ROOT / "contracts/analytics.example.json"),
                "--report-dir", str(Path(temp) / "report")])
            self.assertEqual(status, 2)

    def test_analytics_bad_event_fails(self):
        records = [dict(r) for r in self.records]
        for r in records:
            if r["type"] == "event":
                r["params"] = {"payment_method": "invalid"}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.ndjson"
            path.write_text("".join(json.dumps(r) + "\n" for r in records))
            status = main(["analytics", "verify", "--file", str(path),
                           "--contract", str(ROOT / "contracts/analytics.example.json"),
                           "--report-dir", str(Path(temp) / "report")])
            self.assertEqual(status, 1)


if __name__ == "__main__":
    unittest.main()
