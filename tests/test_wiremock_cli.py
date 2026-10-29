import contextlib
import io
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import call, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))
from harnesslens.cli import main
from harnesslens.wiremock import Admin, WireMockError


class WireMockCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Path(self.temp.name) / "mapping.json"
        self.fixture.write_text(json.dumps({
            "name": "accounts-empty",
            "scenarioName": "accounts-retry",
            "request": {"method": "GET", "urlPath": "/accounts"},
            "response": {"status": 200, "jsonBody": {"accounts": []}},
        }))
        self.admin = Admin("http://127.0.0.1:8080", "qa-run", exclusive=True)

    def test_main_lists_mock_commands_without_server_access(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit_info:
                main(["mock", "--help"])
        self.assertEqual(exit_info.exception.code, 0)
        for command in ("start", "apply", "enable", "disable", "list", "requests", "verify", "reset"):
            self.assertIn(command, output.getvalue())

    def test_dedicated_server_is_required_during_fixture_validation(self):
        with self.assertRaisesRegex(ValueError, "--exclusive"):
            Admin("http://localhost:8080", "qa-run").prepare(self.fixture)

    def test_scope_header_and_scenario_are_namespaced(self):
        admin = Admin("http://127.0.0.1:8080", "qa-run", scope_header="X-QA-Session")
        mapping = admin.prepare(self.fixture)[0]
        self.assertEqual(mapping["request"]["headers"]["X-QA-Session"], {"equalTo": "qa-run"})
        self.assertEqual(mapping["scenarioName"], "hlens.qa-run.accounts-retry")

    def test_scope_header_cannot_replace_authentication(self):
        with self.assertRaisesRegex(ValueError, "non-authentication"):
            Admin("http://127.0.0.1:8080", "qa-run", scope_header="Authorization").prepare(self.fixture)

    def test_apply_upserts_stable_id_and_switches_response_without_reset(self):
        new_mapping = self.admin.prepare(self.fixture)[0]
        with patch.object(self.admin, "health", return_value=[]), patch.object(self.admin, "call") as call:
            applied = self.admin.apply(self.fixture)
        self.assertEqual(applied["mappings"][0]["id"], new_mapping["id"])
        call.assert_called_once_with("POST", "/mappings", new_mapping)

        old = dict(new_mapping)
        self.fixture.write_text(json.dumps({**json.loads(self.fixture.read_text()),
                                            "response": {"status": 503, "jsonBody": {"error": "synthetic"}}}))
        changed = self.admin.prepare(self.fixture)[0]
        self.assertEqual(changed["id"], old["id"])
        self.assertNotEqual(changed["metadata"]["hlensFixtureSha256"], old["metadata"]["hlensFixtureSha256"])
        with patch.object(self.admin, "health", return_value=[old]), patch.object(
                self.admin, "call", return_value={"requests": []}) as admin_call:
            self.admin.apply(self.fixture)
        admin_call.assert_any_call("PUT", "/mappings/" + old["id"], changed)

    def test_fixture_validates_before_any_admin_mutation(self):
        self.fixture.write_text('{"name":"broken"}')
        with patch.object(self.admin, "health") as health, patch.object(self.admin, "call") as call:
            with self.assertRaisesRegex(ValueError, "request and response"):
                self.admin.apply(self.fixture)
        health.assert_not_called()
        call.assert_not_called()

    def test_upstream_requires_authorized_distinct_backend(self):
        passthrough = Path(self.temp.name) / "proxy.json"
        passthrough.write_text(json.dumps({"name": "fallback", "request": {},
                                           "response": {"proxyBaseUrl": "__UPSTREAM__"}}))
        for upstream in (None, "http://user:secret@example.test", "http://localhost:8080",
                         "http://127.0.0.1:8080"):
            with self.subTest(upstream=bool(upstream)), self.assertRaises(ValueError):
                self.admin.prepare(passthrough, upstream)

    def test_cleanup_only_deletes_this_sessions_mappings(self):
        own = self.admin.prepare(self.fixture)[0]
        foreign = {**own, "id": str(uuid.uuid4()),
                   "metadata": {**own["metadata"], "hlensSession": "another-run"}}
        native = {"id": str(uuid.uuid4()), "request": {}, "response": {"status": 200}}
        with patch.object(self.admin, "health", return_value=[own, foreign, native]), patch.object(
                self.admin, "call", return_value={"requests": []}) as call:
            self.assertEqual(self.admin.reset()["removed"], 1)
        deletes = [args for args, _ in call.call_args_list if args[0] == "DELETE"]
        self.assertEqual(deletes, [("DELETE", "/mappings/" + own["id"])])

    def test_request_report_omits_urls_headers_bodies_and_queries(self):
        mapping = self.admin.prepare(self.fixture)[0]
        event = {"id": str(uuid.uuid4()), "wasMatched": True, "stubMapping": {"id": mapping["id"]},
                 "request": {"method": "GET", "url": "/accounts?token=secret", "headers": {"Authorization": "secret"},
                             "body": "private-body", "loggedDate": "synthetic-time"},
                 "responseDefinition": {"status": 200}, "timing": {"totalTime": 12}}
        with patch.object(self.admin, "health", return_value=[mapping]), patch.object(
                self.admin, "call", return_value={"requests": [event]}):
            report = json.dumps(self.admin.requests())
        self.assertIn("synthetic-time", report)
        self.assertIn('"status": 200', report)
        for secret in ("token", "Authorization", "private-body", "secret", "/accounts"):
            self.assertNotIn(secret, report)

    def test_verify_fails_when_expected_mapping_was_not_hit(self):
        mapping = self.admin.prepare(self.fixture)[0]
        with patch.object(self.admin, "select", return_value=[mapping]), patch.object(
                self.admin, "events", return_value=[]):
            report = self.admin.verify("accounts-empty", minimum=1)
        self.assertEqual((report["status"], report["hits"]), ("FAIL", 0))

    def test_cli_returns_failure_for_unmet_verification(self):
        with patch("harnesslens.wiremock.Admin.verify", return_value={"status": "FAIL", "hits": 0}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["mock", "--url", "http://127.0.0.1:8080", "--session", "qa-run",
                               "--exclusive", "verify", "--name", "accounts-empty"])
        self.assertEqual((result, json.loads(output.getvalue())["status"]), (1, "FAIL"))

    def test_admin_redirect_and_http_errors_are_redacted(self):
        admin = Admin("https://mock.example", "qa-run", exclusive=True)
        for status in (301, 422):
            error = HTTPError(admin.url + "/__admin/mappings", status, "invalid fixture", {},
                              io.BytesIO(b"secret fixture payload"))
            with patch.object(admin.opener, "open", side_effect=error):
                with self.assertRaisesRegex(WireMockError, f"HTTP {status}") as raised:
                    admin.call("GET", "/mappings")
            error.close()
            self.assertNotIn("secret fixture payload", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
