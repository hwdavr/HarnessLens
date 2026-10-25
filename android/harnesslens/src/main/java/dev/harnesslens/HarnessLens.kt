package dev.harnesslens

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Recorder for the real Android app. It does not instantiate or dispatch into ViewModels. */
object HarnessLens {
    @Volatile private var context: Context? = null
    private val writer = Executors.newSingleThreadExecutor { task ->
        Thread(task, "HarnessLensWriter").apply { isDaemon = true }
    }
    private var currentRunId: String? = null
    private var sequence = 0
    private val queueFailures = AtomicInteger(0)

    /** Call once in your debug/test Application.onCreate(). */
    fun install(context: Context) {
        this.context = context.applicationContext
    }

    /** Call at the existing analytics wrapper boundary. Real Firebase delivery is unchanged. */
    fun analytics(name: String, params: Map<String, Any?> = emptyMap()) {
        record("event", "name", name, "params", params)
    }

    /** Pass an explicit allowlist of primitive values from a live state holder. */
    fun state(scope: String, values: Map<String, Any?>) {
        record("state", "scope", scope, "values", values)
    }

    /** Optional @field:LensField-based export, avoiding a hand-written mapper. */
    fun state(scope: String, stateObject: Any) {
        state(scope, LensFields.extract(stateObject))
    }

    /** Opt-in one-liner to observe an existing StateFlow; cancel with its lifecycle scope. */
    fun <T : Any> watch(scope: String, source: StateFlow<T>, lifecycle: CoroutineScope): Job =
        lifecycle.launch { source.collect { state(scope, it) } }

    /** Best effort; invoke from a worker/test thread before collecting artifacts. */
    fun flush(timeoutMillis: Long = 5000): Boolean = try {
        writer.submit {}.get(timeoutMillis, TimeUnit.MILLISECONDS)
        true
    } catch (_: Exception) { false }

    private fun record(type: String, labelKey: String, label: String, valuesKey: String, values: Map<String, Any?>) {
        if (context == null || label.isEmpty() || label.length > 120) return
        val safeValues = sanitize(values)
        writer.execute { write(type, labelKey, label, valuesKey, safeValues) }
    }

    private fun write(type: String, labelKey: String, label: String, valuesKey: String, fields: Map<String, Any?>) {
        val app = context ?: return
        val root = File(app.filesDir, "harnesslens")
        val control = try { JSONObject(File(root, "control.json").readText()) } catch (_: Exception) { return }
        if (control.optInt("version") != 1 || !control.optBoolean("active")) return
        val runID = control.optString("run_id")
        if (!Regex("^[0-9a-f]{32}$").matches(runID)) return
        val runs = File(root, "runs")
        if (!runs.exists() && !runs.mkdirs()) return
        val target = File(runs, "$runID.ndjson")
        if (currentRunId != runID) {
            currentRunId = runID
            sequence = 0
            val ready = JSONObject()
                .put("type", "capture_ready")
                .put("version", 1)
                .put("run_id", runID)
                .put("timestamp_ms", System.currentTimeMillis())
            if (!append(target, ready)) return
        }
        sequence += 1
        val body = JSONObject()
            .put("type", type).put("run_id", runID).put("sequence", sequence)
            .put("timestamp_ms", System.currentTimeMillis())
            .put(labelKey, label).put(valuesKey, JSONObject(fields))
        if (!append(target, body)) queueFailures.incrementAndGet()
    }

    private fun append(file: File, value: JSONObject): Boolean = try {
        FileOutputStream(file, true).use { stream ->
            stream.write(value.toString().toByteArray(Charsets.UTF_8))
            stream.write('\n'.code)
        }
        true
    } catch (_: Exception) { false }

    private fun sanitize(source: Map<String, Any?>): Map<String, Any?> {
        val safe = linkedMapOf<String, Any?>()
        for ((key, value) in source.toSortedMap()) {
            if (safe.size >= 32) break
            if (!LensFields.allowedName(key)) continue
            val primitive = when (value) {
                is String -> value.take(240)
                is Boolean, is Int, is Long, is Short, is Byte -> value
                is Double -> value.takeIf { it.isFinite() }
                is Float -> value.takeIf { it.isFinite() }
                is Enum<*> -> value.name
                else -> null // reject nested maps, raw objects, arrays, identifiers by default
            }
            if (primitive != null) safe[key] = primitive
        }
        return safe
    }
}
