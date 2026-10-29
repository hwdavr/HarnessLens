# WireMock CLI examples

HarnessLens controls an existing WireMock 3 server through its Admin API; it does not add a Python dependency. Connect to an existing server, or start a local server in a persistent terminal with a trusted standalone JAR:

```bash
hlens mock --session qa-run-01 --exclusive start
hlens mock --session qa-run-01 --exclusive apply examples/wiremock/rest-query.json
# Drive the application with the UI test tool.
hlens mock --session qa-run-01 requests
hlens mock --session qa-run-01 verify --name accounts-empty --min-hits 1
hlens mock --session qa-run-01 reset
```

For a local listener, set `HLS_WIREMOCK_JAR` once in your shell profile to the trusted standalone JAR path, for example `export HLS_WIREMOCK_JAR="/path/to/wiremock-standalone-3.13.2.jar"`. Then `hlens mock --session qa-run-01 --exclusive start` starts that JAR without an explicit `--jar` argument. You can still pass `start --jar /path/to/wiremock-standalone.jar` to override the environment setting. With the variable unset and no `--jar`, `start` health-checks an existing server. The CLI stays attached and stops only the server process it launched when interrupted. Global mock options come before the command.

Examples use synthetic endpoints; adapt them to the selected app's API. Fixture files contain native WireMock mapping JSON. Apply a changed fixture while the app points at the server to switch the response without rebuilding the app. `reset` removes only mappings and journal entries carrying this session's metadata. Keep the Admin API private: its in-memory journal retains request bodies and headers.

Use `--scope-header X-QA-Session` instead of `--exclusive` only when the app can send that header with the exact session ID. The adapter then scopes every mapping and removes this header before upstream passthrough. Otherwise use a dedicated WireMock instance.

For real backend fallback:

```bash
hlens mock --session qa-run-01 --exclusive apply \
  examples/wiremock/passthrough.json --upstream https://staging.example.test
```

The catch-all uses lowest precedence; explicit fixtures override it. Passthrough sends real app requests to the supplied backend. Always choose an authorized test service and account.
