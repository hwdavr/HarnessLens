import contextlib
import io
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))
from harnesslens.cli import main
from harnesslens.device import Device


class SimulatedIOSLifecycle(unittest.TestCase):
    def test_capture_start_write_stop_export_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandbox"
            run_id = "c" * 32
            artifacts = Path(tmp) / "evidence.ndjson"
            cmd = ["--platform", "ios", "--app-id", "com.example.qa"]
            with patch.object(Device, "_ios_dir", return_value=root), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["capture", "start", *cmd, "--run-id", run_id]), 0)
                control = json.loads((root / "control.json").read_text())
                self.assertTrue(control["active"])
                target = root / "runs" / (run_id + ".ndjson")
                target.parent.mkdir(parents=True, exist_ok=True)
                # Simulate SDK writing app-private NDJSON, as in Swift XCTest.
                target.write_bytes((ROOT / "examples/evidence.example.ndjson").read_bytes().replace(b"a" * 32, b"c" * 32))
                self.assertEqual(main(["capture", "status", *cmd]), 0)
                self.assertEqual(main(["capture", "stop", *cmd, "--settle", "0"]), 0)
                self.assertFalse(json.loads((root / "control.json").read_text())["active"])
                self.assertEqual(main(["capture", "export", *cmd, "--out", str(artifacts)]), 0)
                self.assertEqual(main(["state", "verify", "--file", str(artifacts),
                    "--contract", str(ROOT / "contracts/state.example.json")]), 0)
                self.assertEqual(main(["analytics", "verify", "--file", str(artifacts),
                    "--contract", str(ROOT / "contracts/analytics.example.json"),
                    "--report-dir", str(Path(tmp) / "report")]), 0)


if __name__ == "__main__": unittest.main()
