"""Session-scoped control of a WireMock 3 server through its Admin API."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .device import DeviceError


class WireMockError(DeviceError):
    """The server is unavailable or request verification cannot be established."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
        raise ValueError(f"{label} must contain 1-64 letters, digits, dots, underscores or hyphens")
    return value


def validate_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Use an HTTP(S) URL without credentials, query or fragment")
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Use an HTTP(S) URL without credentials, query or fragment")
    _ = parsed.port
    return value.rstrip("/")


class Admin:
    def __init__(self, url: str, session: str, scope_header: str | None = None, exclusive: bool = False):
        self.url = validate_url(url)
        self.session = safe_name(session, "session")
        self.scope_header = scope_header
        self.exclusive = exclusive
        if scope_header and not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", scope_header):
            raise ValueError("Invalid scope header name")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(self, method: str, path: str, body: object | None = None, missing_ok: bool = False) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("HLS_WIREMOCK_ADMIN_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(self.url + "/__admin" + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=10) as response:
                raw = response.read()
            value = json.loads(raw) if raw else {}
            if not isinstance(value, dict):
                raise WireMockError("WireMock Admin API returned an unexpected response")
            return value
        except HTTPError as exc:
            if missing_ok and exc.code == 404:
                exc.close()
                return {}
            code = exc.code
            exc.close()
            raise WireMockError(f"WireMock Admin API HTTP {code}; check server version/configuration") from None
        except (URLError, TimeoutError, OSError):
            raise WireMockError("WireMock unavailable; check URL, listener, TLS trust and Admin API access") from None
        except (json.JSONDecodeError, UnicodeError):
            raise WireMockError("WireMock Admin API returned invalid JSON") from None

    def health(self) -> list[dict]:
        mappings = self.call("GET", "/mappings").get("mappings")
        if not isinstance(mappings, list) or any(not isinstance(item, dict) for item in mappings):
            raise WireMockError("Endpoint is not a compatible WireMock Admin API")
        return mappings

    def owned(self, mapping: dict) -> bool:
        metadata = mapping.get("metadata")
        return isinstance(metadata, dict) and metadata.get("hlensSession") == self.session

    def session_mappings(self) -> list[dict]:
        return [item for item in self.health() if self.owned(item)]

    @staticmethod
    def summary(mapping: dict) -> dict:
        meta = mapping["metadata"]
        return {"id": mapping["id"], "name": meta["hlensName"], "fixture_sha256": meta["hlensFixtureSha256"],
                "priority": mapping.get("priority", 5)}

    def prepare(self, fixture: Path, upstream: str | None = None) -> list[dict]:
        try:
            raw = fixture.read_bytes()
        except OSError as exc:
            raise ValueError("Cannot read WireMock fixture; check its path and permissions") from exc
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid WireMock fixture JSON at line {exc.lineno}, column {exc.colno}") from None
        if not isinstance(document, dict):
            raise ValueError("Fixture must be a native mapping or a mappings bundle")
        entries = document.get("mappings", [document])
        if not isinstance(entries, list) or not entries:
            raise ValueError("Fixture mappings must be a nonempty array")
        if not self.scope_header and not self.exclusive:
            raise ValueError("Specify --exclusive for a dedicated server or --scope-header for shared use")

        prepared, names = [], set()
        digest = hashlib.sha256(raw).hexdigest()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Each WireMock mapping must be an object")
            mapping = copy.deepcopy(entry)
            name = safe_name(mapping.get("name"), "mapping name")
            if name in names:
                raise ValueError("Mapping names in one fixture must be unique")
            names.add(name)
            if not isinstance(mapping.get("request"), dict) or not isinstance(mapping.get("response"), dict):
                raise ValueError(f"Mapping {name} requires request and response objects")
            priority = mapping.get("priority", 5)
            if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 10000:
                raise ValueError("Priority must be an integer from 1 to 10000")
            response = mapping["response"]
            if "proxyBaseUrl" in response:
                target = upstream if response["proxyBaseUrl"] == "__UPSTREAM__" else response["proxyBaseUrl"]
                if not target:
                    raise ValueError("Passthrough mapping requires --upstream with the authorized backend URL")
                response["proxyBaseUrl"] = validate_url(target)
                upstream_parts = urlsplit(response["proxyBaseUrl"])
                server_parts = urlsplit(self.url)
                def listener(parts):
                    host = parts.hostname.lower() if parts.hostname else None
                    if host == "localhost":
                        host = "127.0.0.1"
                    port = parts.port or (443 if parts.scheme == "https" else 80)
                    return host, port
                if listener(upstream_parts) == listener(server_parts):
                    raise ValueError("Upstream must not point back to this WireMock instance")
            if self.scope_header:
                if self.scope_header.lower() in {"authorization", "proxy-authorization", "cookie", "host",
                                                 "content-length", "content-type", "connection"}:
                    raise ValueError("Use a dedicated non-authentication X- header for session isolation")
                request_headers = mapping["request"].setdefault("headers", {})
                if not isinstance(request_headers, dict):
                    raise ValueError("Mapping request headers must be an object")
                if any(isinstance(key, str) and key.lower() == self.scope_header.lower() for key in request_headers):
                    raise ValueError("Fixture cannot override the session scope header")
                request_headers[self.scope_header] = {"equalTo": self.session}
                if "proxyBaseUrl" in response:
                    removed_headers = response.setdefault("removeProxyRequestHeaders", [])
                    if not isinstance(removed_headers, list) or any(not isinstance(name, str) for name in removed_headers):
                        raise ValueError("Mapping removeProxyRequestHeaders must be an array of header names")
                    removed_headers.append(self.scope_header)
            if "scenarioName" in mapping:
                mapping["scenarioName"] = "hlens." + self.session + "." + safe_name(mapping["scenarioName"], "scenario name")
            mapping["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, self.session + "/" + name))
            mapping.pop("uuid", None)
            metadata = mapping.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ValueError(f"Mapping {name} metadata must be an object")
            mapping["metadata"] = {**metadata, "hlensSession": self.session, "hlensName": name,
                                   "hlensFixtureSha256": digest, "hlensScopeHeader": self.scope_header}
            prepared.append(mapping)
        return prepared

    def events(self, mapping: dict) -> list[dict]:
        result = self.call("GET", "/requests?" + urlencode({"matchingStub": mapping["id"]}))
        if result.get("requestJournalDisabled") is True:
            raise WireMockError("Request journal is disabled; expected request verification is unavailable")
        events = result.get("requests")
        if not isinstance(events, list):
            raise WireMockError("WireMock returned an incompatible request journal")
        return [event for event in events if event.get("wasMatched") is True
                and event.get("stubMapping", {}).get("id") == mapping["id"]]

    def clear_events(self, mapping: dict) -> None:
        for event in self.events(mapping):
            try:
                event_id = str(uuid.UUID(event["id"]))
            except (KeyError, ValueError, TypeError):
                raise WireMockError("WireMock journal returned an invalid event ID") from None
            self.call("DELETE", "/requests/" + event_id, missing_ok=True)

    def reset_scenario(self, mapping: dict) -> None:
        scenario = mapping.get("scenarioName")
        if scenario:
            self.call("PUT", "/scenarios/" + quote(scenario, safe="") + "/state", {"state": "Started"}, missing_ok=True)

    def apply(self, fixture: Path, upstream: str | None = None) -> dict:
        prepared = self.prepare(fixture, upstream)
        current = self.health()
        for other in current:
            metadata = other.get("metadata") or {}
            owner, scope = metadata.get("hlensSession"), metadata.get("hlensScopeHeader")
            if owner and owner != self.session and (not self.scope_header or scope != self.scope_header):
                raise ValueError("Conflicting adapter session; use separate servers or a shared scope header")
        by_id = {item.get("id"): item for item in current}
        for item in prepared:
            old = by_id.get(item["id"])
            if old and not self.owned(old):
                raise ValueError("Mapping ID collides with another owner")
        for item in prepared:
            old = by_id.get(item["id"])
            if old:
                self.clear_events(old)
                self.reset_scenario(old)
                self.call("PUT", "/mappings/" + item["id"], item)
            else:
                self.call("POST", "/mappings", item)
        return {"session": self.session, "mappings": [self.summary(item) for item in prepared]}

    def select(self, name: str | None = None) -> list[dict]:
        if name is not None:
            safe_name(name, "mapping name")
        return [item for item in self.session_mappings()
                if name is None or item.get("metadata", {}).get("hlensName") == name]

    def disable(self, name: str) -> dict:
        selected = self.select(name)
        if len(selected) > 1:
            raise ValueError("Mapping name is ambiguous in this session")
        if not selected:
            return {"session": self.session, "disabled": 0}
        mapping = selected[0]
        self.clear_events(mapping)
        self.reset_scenario(mapping)
        self.call("DELETE", "/mappings/" + mapping["id"], missing_ok=True)
        return {"session": self.session, "disabled": 1}

    def reset(self) -> dict:
        selected = self.session_mappings()
        incomplete = False
        for mapping in selected:
            try:
                self.clear_events(mapping)
                self.reset_scenario(mapping)
            except WireMockError:
                incomplete = True
            try:
                self.call("DELETE", "/mappings/" + mapping["id"], missing_ok=True)
            except WireMockError:
                incomplete = True
        if incomplete:
            raise WireMockError("Session cleanup incomplete; mapping removal attempted, check Admin API and journal access")
        return {"session": self.session, "removed": len(selected)}

    def requests(self, name: str | None = None) -> dict:
        output = []
        for mapping in self.select(name):
            for event in self.events(mapping):
                request = event.get("request", {})
                output.append({"mapping": mapping["metadata"]["hlensName"], "event_id": event.get("id"),
                               "method": request.get("method"), "status": event.get("responseDefinition", {}).get("status"),
                               "logged_date": request.get("loggedDate"),
                               "duration_ms": event.get("timing", {}).get("totalTime")})
        return {"session": self.session, "requests": output}

    def verify(self, name: str, minimum: int = 1, maximum: int | None = None,
               fixture_sha256: str | None = None) -> dict:
        safe_name(name, "mapping name")
        if isinstance(minimum, bool) or minimum < 1 or (maximum is not None and maximum < minimum):
            raise ValueError("Verification requires min-hits >= 1 and max-hits >= min-hits")
        selected = self.select(name)
        if len(selected) != 1:
            raise WireMockError("No single active mapping owned by this session; apply it before verifying")
        mapping = selected[0]
        if fixture_sha256 and mapping["metadata"]["hlensFixtureSha256"] != fixture_sha256:
            raise WireMockError("Active mapping fixture hash differs from expected fixture")
        hits = len(self.events(mapping))
        passed = hits >= minimum and (maximum is None or hits <= maximum)
        return {**self.summary(mapping), "session": self.session, "hits": hits,
                "min_hits": minimum, "max_hits": maximum, "status": "PASS" if passed else "FAIL"}


def run_start(admin: Admin, jar: Path | None) -> int:
    if jar is None:
        admin.health()
        print(json.dumps({"status": "READY", "mode": "external", "session": admin.session}))
        return 0
    parts = urlsplit(admin.url)
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.path not in ("", "/"):
        raise ValueError("Local start requires http://127.0.0.1:<port>; connect to external HTTPS/LAN servers instead")
    if not jar.is_file():
        raise ValueError("WireMock JAR does not exist or is not a file")
    try:
        admin.health()
    except WireMockError:
        pass
    else:
        raise ValueError("Port already serves WireMock; omit --jar to connect without owning it")
    with tempfile.TemporaryDirectory(prefix="hlens-wiremock-") as root:
        try:
            child = subprocess.Popen(["java", "-jar", str(jar.resolve()), "--port", str(parts.port or 80),
                                      "--bind-address", "127.0.0.1", "--root-dir", root,
                                      "--local-response-templating", "--disable-request-logging",
                                      "--max-request-journal-entries", "1000"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise WireMockError("Could not start Java; install Java 17+ or connect to an existing server") from None
        try:
            deadline = time.monotonic() + 30
            while True:
                if child.poll() is not None:
                    raise WireMockError("WireMock failed to start; check Java 17+ and port availability")
                try:
                    admin.health()
                    break
                except WireMockError:
                    if time.monotonic() >= deadline:
                        raise WireMockError("WireMock startup health check timed out") from None
                    time.sleep(0.1)
            print(json.dumps({"status": "READY", "mode": "foreground", "pid": child.pid,
                              "session": admin.session}), flush=True)
            child.wait()
        finally:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return 0


def add_parser(root: argparse.ArgumentParser) -> None:
    parser = root.add_parser("mock", help="Manage session-owned WireMock API mocks")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="WireMock base URL")
    parser.add_argument("--session", required=True, help="Unique test/run ID; all operations are scoped to it")
    isolation = parser.add_mutually_exclusive_group()
    isolation.add_argument("--scope-header", help="App must send this header with the session ID")
    isolation.add_argument("--exclusive", action="store_true", help="Declare this a dedicated WireMock server")
    sub = parser.add_subparsers(dest="mock_command", required=True)
    start = sub.add_parser("start", help="Start a local WireMock or health-check an existing server")
    default_jar = os.environ.get("HLS_WIREMOCK_JAR")
    start.add_argument("--jar", type=Path,
                       default=Path(default_jar) if default_jar else None,
                       help="WireMock 3 standalone JAR; defaults to HLS_WIREMOCK_JAR and runs in foreground")
    apply = sub.add_parser("apply", help="Apply or switch to native WireMock JSON mappings")
    apply.add_argument("fixture", type=Path)
    apply.add_argument("--upstream", help="Authorized backend base URL for proxy mappings")
    enable = sub.add_parser("enable", help="Enable mappings by reapplying their fixture")
    enable.add_argument("fixture", type=Path)
    enable.add_argument("--upstream")
    disable = sub.add_parser("disable", help="Disable and remove one session-owned mapping")
    disable.add_argument("--name", required=True)
    listings = sub.add_parser("list", help="List mappings owned by this session")
    requests = sub.add_parser("requests", help="Show sanitized requests matched to session mappings")
    requests.add_argument("--name")
    verify = sub.add_parser("verify", help="Assert expected hit bounds for one mapping")
    verify.add_argument("--name", required=True)
    verify.add_argument("--min-hits", type=int, default=1)
    verify.add_argument("--max-hits", type=int)
    verify.add_argument("--fixture-sha256")
    sub.add_parser("reset", help="Remove only this session's mappings and journal entries")


def dispatch(args: argparse.Namespace) -> int:
    if args.mock_command == "start":
        return run_start(Admin(args.url, args.session, args.scope_header, args.exclusive), args.jar)
    admin = Admin(args.url, args.session, args.scope_header, args.exclusive)
    if args.mock_command in ("apply", "enable"):
        result = admin.apply(args.fixture, args.upstream)
    elif args.mock_command == "list":
        result = {"session": args.session, "mappings": [Admin.summary(item) for item in admin.session_mappings()]}
    elif args.mock_command == "disable":
        result = admin.disable(args.name)
    elif args.mock_command == "requests":
        result = admin.requests(args.name)
    elif args.mock_command == "verify":
        result = admin.verify(args.name, args.min_hits, args.max_hits, args.fixture_sha256)
    else:
        result = admin.reset()
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 1 if result.get("status") == "FAIL" else 0
