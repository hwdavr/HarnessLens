"""Optional real WireMock 3 boundary test. Set WIREMOCK_JAR to enable it."""
import json
import os
import select
import socket
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@unittest.skipUnless(os.environ.get("WIREMOCK_JAR"), "Set WIREMOCK_JAR to run the real WireMock boundary")
class RealWireMockCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = "http://127.0.0.1:" + str(free_port())
        cls.env = dict(os.environ, PYTHONPATH=str(ROOT / "cli"))
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "harnesslens", "mock", "--url", cls.url,
             "--session", "server-owner", "--exclusive", "start", "--jar", cls.env["WIREMOCK_JAR"]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=cls.env)
        ready, _, _ = select.select([cls.server.stdout], [], [], 40)
        if not ready or json.loads(cls.server.stdout.readline()).get("status") != "READY":
            cls.stop_server()
            raise RuntimeError("Local WireMock failed its Admin API health check")
        cls.opener = build_opener(ProxyHandler({}))

    @classmethod
    def stop_server(cls):
        if cls.server.poll() is None:
            cls.server.terminate()
        try:
            cls.server.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.communicate()

    @classmethod
    def tearDownClass(cls):
        cls.stop_server()

    def setUp(self):
        self.session = "qa-" + uuid.uuid4().hex[:20]
        self.fixture = ROOT / "examples/wiremock/rest-query.json"
        self.command("apply", str(self.fixture))

    def command(self, *args):
        return subprocess.run([sys.executable, "-m", "harnesslens", "mock", "--url", self.url,
                               "--session", self.session, "--exclusive", *args],
                              capture_output=True, text=True, env=self.env)

    def request(self, path):
        try:
            with self.opener.open(self.url + path, timeout=5) as response:
                return response.status, response.read()
        except HTTPError as error:
            result = (error.code, error.read())
            error.close()
            return result

    def test_real_requests_switch_verify_and_session_cleanup(self):
        status, body = self.request("/accounts?page=0")
        self.assertEqual((status, json.loads(body)), (200, {"accounts": [], "nextPage": 1}))
        status, body = self.request("/accounts?page=1")
        self.assertEqual((status, json.loads(body)["accounts"][0]["id"]), (200, "synthetic-account"))
        verify = self.command("verify", "--name", "accounts-empty", "--min-hits", "1", "--max-hits", "1")
        self.assertEqual((verify.returncode, json.loads(verify.stdout)["status"]), (0, "PASS"))

        disable = self.command("disable", "--name", "accounts-empty")
        self.assertEqual((disable.returncode, self.request("/accounts?page=0")[0]), (0, 404))
        enable = self.command("enable", str(self.fixture))
        self.assertEqual((enable.returncode, self.request("/accounts?page=0")[0]), (0, 200))

        reset = self.command("reset")
        self.assertEqual((reset.returncode, json.loads(reset.stdout)["removed"]), (0, 2))
        self.assertEqual(self.command("reset").returncode, 0)


if __name__ == "__main__":
    unittest.main()
