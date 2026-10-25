import XCTest
@testable import HarnessLens

final class HarnessLensTests: XCTestCase {
    func testNoControlMeansNoCapture() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        HarnessLens.shared.configure(directory: root)
        HarnessLens.shared.state("payment", fields: ["status": "pending"])
        HarnessLens.shared.flush()
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("runs").path))
    }

    func testCaptureAndSensitiveFieldFilter() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let runID = String(repeating: "a", count: 32)
        let control: [String: Any] = ["version": 1, "active": true, "run_id": runID]
        let controlData = try JSONSerialization.data(withJSONObject: control)
        try controlData.write(to: root.appendingPathComponent("control.json"))
        HarnessLens.shared.configure(directory: root)
        HarnessLens.shared.analytics("payment_completed", params: ["method": "linkpoints", "access_token": "do-not-capture"])
        HarnessLens.shared.state("ev.payment", fields: ["status": "confirmed", "retryCount": 2])
        HarnessLens.shared.flush()
        let text = try String(contentsOf: root.appendingPathComponent("runs/\(runID).ndjson"), encoding: .utf8)
        let lines = text.split(separator: "\n")
        XCTAssertEqual(lines.count, 3)
        XCTAssertTrue(text.contains("capture_ready"))
        XCTAssertTrue(text.contains("confirmed"))
        XCTAssertFalse(text.contains("do-not-capture"))
    }
}
