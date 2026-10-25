# HarnessLens

**Read-only, low-boilerplate runtime evidence for AI agents testing the real Android and iOS app.**

HarnessLens is **not** the previous headless `mobile-harness-cli`. It does not create ViewModels, dispatch intents, mock APIs or replace normal unit tests. Use **agent-device** to operate the actual installed app (with real authorized staging APIs), and HarnessLens to capture *only the selected internal state and analytics events* that UI testing cannot see.

## Deliverables

- `android/harnesslens/` — Kotlin/Android recorder; optional `@field:LensField` and `StateFlow` observer (no KSP)
- `ios/` — Swift Package runtime recorder
- `cli/` — stdlib Python `hlens` command line
- `contracts/` — examples for analytics and state verification
- `examples/` — portable NDJSON evidence for offline testing
- `.agents/skills/harnesslens/SKILL.md` — agent workflow
- `tests/` and `ios/Tests/` — automated validation

## How it works

```text
agent-device → installed Android/iOS app → real staging APIs
                         │
                  selected state + analytics
                         │
         app-private harnesslens/runs/<run-id>.ndjson
                         │
              hlens capture export
                         │
            state / analytics validation → PASS | FAIL | INCONCLUSIVE
```

CLI writes an app-private `control.json` via **ADB `run-as`** (debuggable Android app) or **`simctl get_app_container`** (iOS Simulator). The recorder checks this file on each call. When active, it writes `capture_ready` and NDJSON records to a unique run-scoped file. No server, port, MCP dependency, KSP or changes to the real backend are required.

## Install CLI

From this directory:

```bash
uv tool install --editable .
hlens --help
```

Or without installation: `uv run hlens --help` (or `PYTHONPATH=cli python3 -m harnesslens --help`).

Requires Python 3.10+, `adb` for Android, or Xcode with `xcrun simctl` for iOS Simulator. Swift Package requires Xcode/Swift 5.9+; Android module uses Java/Kotlin and Android Gradle Plugin.

## App integration: Android

See [android/README.md](android/README.md) for Gradle source-set/security considerations. In a dedicated **debug / internal QA** build:

```kotlin
// One-time initialization in Application.onCreate
HarnessLens.install(this)

// In your existing ViewModel; uses the REAL StateFlow instance
init { HarnessLens.watch("ev.payment", uiState, viewModelScope) }
```

Optional annotations on your existing model to select visible fields:

```kotlin
data class PaymentUiState(
    @field:LensField val status: String,
    @field:LensField val retryCount: Int,
    val accessToken: String // not annotated, never recorded
)
```

**Simplest alternative, no annotations at all:**

```kotlin
HarnessLens.state("ev.payment", mapOf("status" to uiState.value.status,
                                      "retryCount" to uiState.value.retryCount))
```

Record analytics once in the existing debug analytics-tracker decorator:

```kotlin
HarnessLens.analytics(name, params)
delegate.track(name, params) // still uses actual Firebase SDK
```

Do not add HarnessLens to production release code simply to use annotations. If source-set isolation is important, prefer explicit field mapping in the debug source set.

## App integration: iOS Simulator

Add `ios/Package.swift` as a local Swift package to your **test/debug** app target. There is no automatic observation of arbitrary SwiftUI `@Observable` properties. Call the state method when the actual state holder changes:

```swift
#if DEBUG
HarnessLens.shared.state("ev.payment", fields: [
    "status": paymentState.status.rawValue,
    "retryCount": paymentState.retryCount
])
HarnessLens.shared.analytics("charging_completed", params: ["payment_method": "linkpoints"])
#endif
```

The default storage location is `Library/Caches/harnesslens`. See `ios/IntegrationExample.swift`.

## Run a scenario on the actual app

```bash
# Arm capture BEFORE launching/navigating app. Unique run ID is generated.
hlens capture start --platform android --app-id com.example.debug

# Use your exploratory-qa agent/agent-device to drive the installed app:
agent-device open com.example.debug --platform android
# ... inspect, tap, navigate, assert UI ...

# Inspect the currently recorded internal state while app is running:
hlens state get --platform android --app-id com.example.debug --scope ev.payment
hlens state await --platform android --app-id com.example.debug \
  --scope ev.payment --field status --equals confirmed --timeout 20

# Stop, extract and verify:
hlens capture stop --platform android --app-id com.example.debug
hlens capture export --platform android --app-id com.example.debug --out artifacts/evidence.ndjson
hlens state history --file artifacts/evidence.ndjson --scope ev.payment
hlens state verify --file artifacts/evidence.ndjson --contract contracts/state.example.json
hlens analytics verify --file artifacts/evidence.ndjson \
  --contract contracts/analytics.example.json --report-dir artifacts/analytics
```

For iOS Simulator, change `--platform ios` and supply its bundle ID. To target a specific emulator/simulator, use `--device-id`.

**Do not use `capture start` as a substitute for app initialization.** If the app is already running but emits no state or analytics after capture starts, there will be no `capture_ready` marker and the CLI returns INCONCLUSIVE. Launch, reload or interact with the instrumented app after arming capture.

## Offline demonstration (no device)

```bash
PYTHONPATH=cli python3 -m harnesslens state get --file examples/evidence.example.ndjson --scope ev.payment
PYTHONPATH=cli python3 -m harnesslens state verify --file examples/evidence.example.ndjson --contract contracts/state.example.json
PYTHONPATH=cli python3 -m harnesslens analytics verify --file examples/evidence.example.ndjson --contract contracts/analytics.example.json --report-dir /tmp/hlens-demo
```

## Exit codes and evidence

| Code | Meaning | CI recommendation |
|---|---|---|
| `0` | PASS / command succeeded | Pass |
| `1` | FAIL: actual evidence violates a check | Fail |
| `2` | INCONCLUSIVE: missing capture, recorder health or relevant scope unknown | Infrastructure retry/investigate |
| `3` | INVALID_INPUT: malformed input, contract or output path | Fix test configuration |

Records include `type`, `run_id`, `timestamp_ms`, `sequence` (events and state), and selected `name/params` or `scope/values`. State history is sampled; Kotlin `StateFlow` is conflated and **not** a lossless transition journal.

## Security & limitations

- **Do not integrate into production release builds.** Debuggable Android packages and accessible Simulator sandboxes are intentional developer privileges; release builds/physical iOS devices are not supported by this initial CLI transport.
- Store only consciously allowlisted, low-sensitivity primitive fields. Built-in keyword blocking is a backup, **not** a privacy guarantee. Never record tokens, authorization headers, payment card data, PII, or sensitive API payloads.
- No network interception or GA4 delivery proof; use mitmproxy or backend evidence for HTTP and Firebase DebugView/BigQuery for remote delivery as appropriate.
- A passing analytics contract proves only app-side emission at the recording boundary. If no analytics events were captured at all, the result is INCONCLUSIVE because an uninstrumented analytics adapter is indistinguishable from a missing first event.
- Redact test accounts and restrict artifact retention. Test with staging, not live customer data.
- Recording is best-effort. Process termination can drop queued data; avoid using this as a definitive crash recorder.
- Capture start/stop is applied when the SDK checks `control.json` on record, not a guaranteed instant network-synchronized barrier. `capture stop` has a best-effort 0.5-second settling interval (`--settle`); it does not guarantee app writer queues are drained.
- XCTest/real iOS device and full Android Gradle build were not executed in this container; see [docs/verification.md](docs/verification.md).

## Tests

```bash
python3 -m unittest discover -s tests -v
cd ios && swift test
```

For the same analytics contract format as your existing `analytics-event-validator` skill, use `hlens analytics verify`. The older skill's validator directly rejects combined state + analytics NDJSON; the HarnessLens CLI automatically filters out state records before invoking equivalent validation rules.
