# Verification status and known limitations

This package was constructed in an environment with Python 3.13, Swift 6.2 (Linux), and Kotlin JVM compiler, but without Android SDK/Gradle/ADB or an iOS Simulator.

- Python unit + CLI integration tests: run locally (see output in task conversation).
- Swift Package: `cd ios && swift test`; compiles and exercises local capture + sensitive-key redaction on Linux. Apple Simulator integration not verified here.
- Kotlin annotation/field-selection: compiled with standalone `kotlinc` and exercised on the JVM. Android library itself requires an Android SDK build; not yet tested on a device.
- Device control/extraction: Python transport has mocked `adb` and iOS sandbox tests. **Not** verified with live `run-as`, Simulator, real app, or agent-device.
- Existing analytics validator rules: reused and tested on mixed state/analytics captures.

## Pilot checklist

1. Integrate Android (or Swift) recorder into a **nonproduction** app build.
2. Build and install, check app startup and state logging while `control.json` is absent (should be inert).
3. Start capture with `hlens`, launch app, drive a one-screen agent-device journey.
4. Check `capture status`, run_id isolation, file extraction and state values.
5. Verify a PASS contract, a deliberately failing contract, and a missing recorder INCONCLUSIVE result.
6. Check `capture stop`, privacy/sanitization, app restart, and simultaneous repeated runs on different devices.
7. Test that your normal production release has no recorder, file bridge or test-only transport.
