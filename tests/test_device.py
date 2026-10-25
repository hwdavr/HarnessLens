import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))
from harnesslens.device import Device, DeviceError
from harnesslens.cli import main


class DeviceTests(unittest.TestCase):
    def test_invalid_app_id_rejected(self):
        with self.assertRaisesRegex(DeviceError, "Invalid app"):
            Device("android", "com.test.app;rm -rf /")

    def test_android_control_write_uses_app_private_run_as(self):
        seen = []
        def fake_run(argv, *, data=None):
            seen.append((argv, data))
            return b""
        with patch("harnesslens.device.run", side_effect=fake_run):
            Device("android", "com.example.debug", "emulator-5554").write_control({"version": 1, "run_id": "a" * 32, "active": True})
        self.assertEqual(len(seen), 3)
        self.assertIn("run-as", seen[0][0])
        self.assertIn("exec-in", seen[1][0])
        self.assertEqual(json.loads(seen[1][1])["run_id"], "a" * 32)

    def test_ios_controls_use_atomic_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Device("ios", "com.example.debug")
            with patch.object(device, "_ios_dir", return_value=Path(tmp)):
                device.write_control({"version": 1, "run_id": "b" * 32, "active": True})
                self.assertTrue(device.read_control()["active"])
                self.assertFalse((Path(tmp) / "control.tmp").exists())

    def test_capture_start_mock_ios_then_status_without_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Device, "_ios_dir", return_value=Path(tmp)):
                self.assertEqual(main(["capture", "start", "--platform", "ios", "--app-id", "com.example.debug", "--run-id", "a" * 32]), 0)
                self.assertEqual(main(["capture", "status", "--platform", "ios", "--app-id", "com.example.debug"]), 2)

    def test_exfiltration_path_rejected(self):
        d = Device("android", "com.example")
        with self.assertRaisesRegex(DeviceError, "Unsafe"):
            d._read("../../etc/passwd")


if __name__ == "__main__":
    unittest.main()
