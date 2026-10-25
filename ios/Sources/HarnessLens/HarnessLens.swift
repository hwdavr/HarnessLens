import Foundation

/// Read-only diagnostic recorder for authorized local/simulator builds.
/// It is inert unless a test runner creates Library/Caches/harnesslens/control.json.
public final class HarnessLens {
    public static let shared = HarnessLens()
    private let queue = DispatchQueue(label: "dev.harnesslens.recorder", qos: .utility)
    private var directory: URL
    private var currentRunId: String?
    private var sequence: Int = 0

    private init() {
        let cache = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first!
        directory = cache.appendingPathComponent("harnesslens", isDirectory: true)
    }

    /// Normally unnecessary: override for isolated library tests.
    public func configure(directory: URL) {
        queue.sync {
            self.directory = directory
            self.currentRunId = nil
            self.sequence = 0
        }
    }

    /// Record at the shared AnalyticsTracker boundary, before forwarding to Firebase.
    public func analytics(_ name: String, params: [String: Any] = [:]) {
        let fields = Self.sanitize(params)
        queue.async { self.persist(type: "event", labelKey: "name", label: name, fieldsKey: "params", fields: fields) }
    }

    /// Export selected primitive fields from a *live* UI state holder. Read only.
    public func state(_ scope: String, fields: [String: Any]) {
        let values = Self.sanitize(fields)
        queue.async { self.persist(type: "state", labelKey: "scope", label: scope, fieldsKey: "values", fields: values) }
    }

    /// Call before extracting evidence if your UI test can invoke an app test hook.
    public func flush() {
        queue.sync {}
    }

    private func persist(type: String, labelKey: String, label: String, fieldsKey: String, fields: [String: Any]) {
        guard label.count <= 120, !label.isEmpty,
              let controlData = try? Data(contentsOf: directory.appendingPathComponent("control.json")),
              let doc = try? JSONSerialization.jsonObject(with: controlData) as? [String: Any],
              (doc["version"] as? Int) == 1,
              (doc["active"] as? Bool) == true,
              let runID = doc["run_id"] as? String,
              runID.range(of: "^[0-9a-f]{32}$", options: .regularExpression) != nil else { return }
        let runs = directory.appendingPathComponent("runs", isDirectory: true)
        do { try FileManager.default.createDirectory(at: runs, withIntermediateDirectories: true) }
        catch { return }
        let file = runs.appendingPathComponent("\(runID).ndjson")
        if currentRunId != runID {
            currentRunId = runID
            sequence = 0
            // A reused run ID is not accepted as fresh evidence by the CLI; it creates a new run ID by default.
            guard append(["type": "capture_ready", "version": 1, "run_id": runID,
                          "timestamp_ms": Self.nowMs()], to: file) else { return }
        }
        sequence += 1
        let record: [String: Any] = [
            "type": type, "run_id": runID, "sequence": sequence,
            "timestamp_ms": Self.nowMs(), labelKey: label, fieldsKey: fields
        ]
        _ = append(record, to: file)
    }

    @discardableResult
    private func append(_ record: [String: Any], to file: URL) -> Bool {
        guard JSONSerialization.isValidJSONObject(record),
              let encoded = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]),
              FileManager.default.fileExists(atPath: file.path) || FileManager.default.createFile(atPath: file.path, contents: nil),
              let handle = try? FileHandle(forWritingTo: file) else { return false }
        defer { try? handle.close() }
        do {
            try handle.seekToEnd()
            try handle.write(contentsOf: encoded + Data([0x0a]))
            return true
        } catch { return false }
    }

    private static func nowMs() -> Int64 { Int64(Date().timeIntervalSince1970 * 1000) }

    /// Allowlist primitive values only. No arrays, dictionaries, blobs or object descriptions.
    private static func sanitize(_ fields: [String: Any]) -> [String: Any] {
        var result: [String: Any] = [:]
        for (key, value) in fields.sorted(by: { $0.key < $1.key }).prefix(32) {
            guard key.count <= 80, !key.isEmpty, !isSensitive(key) else { continue }
            switch value {
            case let v as String: result[key] = String(v.prefix(240))
            case let v as Bool: result[key] = v
            case let v as Int: result[key] = v
            case let v as Int64: result[key] = v
            case let v as Double where v.isFinite: result[key] = v
            case let v as Float where v.isFinite: result[key] = v
            default: break
            }
        }
        return result
    }

    private static func isSensitive(_ key: String) -> Bool {
        let lowered = key.lowercased()
        return ["token", "secret", "password", "authorization", "cookie", "email", "phone", "address", "nric", "cardnumber"].contains { lowered.contains($0) }
    }
}
