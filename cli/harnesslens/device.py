"""Transport for debug Android app-private sandbox or iOS Simulator."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class DeviceError(RuntimeError):
    pass


def run(argv: list[str], *, data: bytes | None = None) -> bytes:
    try:
        p = subprocess.run(argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=35)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise DeviceError(f"Command failed to start/finish: {argv[0]}: {exc}") from exc
    if p.returncode:
        raise DeviceError(f"{' '.join(argv[:3])}: {p.stderr.decode(errors='replace').strip()}")
    return p.stdout


class Device:
    def __init__(self, platform: str, app_id: str, device_id: str | None = None):
        if not NAME_RE.fullmatch(app_id):
            raise DeviceError("Invalid app/bundle ID")
        if platform not in ("android", "ios"):
            raise DeviceError("platform must be android or ios")
        self.platform, self.app_id, self.device_id = platform, app_id, device_id

    def _adb(self, *args: str, data: bytes | None = None) -> bytes:
        command = ["adb"]
        if self.device_id:
            command += ["-s", self.device_id]
        return run(command + list(args), data=data)

    def _ios_dir(self) -> Path:
        cmd = ["xcrun", "simctl", "get_app_container", self.device_id or "booted", self.app_id, "data"]
        return Path(run(cmd).decode().strip()) / "Library" / "Caches" / "harnesslens"

    def write_control(self, doc: dict) -> None:
        data = (json.dumps(doc, separators=(",", ":")) + "\n").encode()
        if self.platform == "ios":
            root = self._ios_dir()
            root.mkdir(parents=True, exist_ok=True)
            tmp = root / "control.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, root / "control.json")
            return
        self._adb("shell", "run-as", self.app_id, "mkdir", "-p", "files/harnesslens/runs")
        # No shell interpolation: feed the JSON over stdin to tee as the app UID.
        self._adb("exec-in", "run-as", self.app_id, "tee", "files/harnesslens/control.tmp", data=data)
        self._adb("shell", "run-as", self.app_id, "mv", "files/harnesslens/control.tmp", "files/harnesslens/control.json")

    def read_control(self) -> dict:
        content = self._read("control.json")
        try:
            value = json.loads(content)
        except ValueError as exc:
            raise DeviceError(f"Invalid control file: {exc}") from exc
        if not isinstance(value, dict) or value.get("version") != 1:
            raise DeviceError("Missing or invalid control.json (schema version)")
        return value

    def _read(self, relative: str) -> bytes:
        if not re.fullmatch(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", relative) or ".." in relative:
            raise DeviceError("Unsafe relative filename")
        if self.platform == "ios":
            try:
                return (self._ios_dir() / relative).read_bytes()
            except OSError as exc:
                raise DeviceError(f"Cannot read iOS Simulator file: {exc}") from exc
        return self._adb("exec-out", "run-as", self.app_id, "cat", f"files/harnesslens/{relative}")

    def read_capture(self, run_id: str) -> bytes:
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise DeviceError("Invalid run_id")
        return self._read(f"runs/{run_id}.ndjson")
