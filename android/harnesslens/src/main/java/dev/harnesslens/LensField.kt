package dev.harnesslens

/** Only marked primitive fields are exported from an annotated state object. */
@Target(AnnotationTarget.FIELD)
@Retention(AnnotationRetention.RUNTIME)
annotation class LensField(val alias: String = "")

object LensFields {
    /** Values default to an empty map unless fields are explicitly annotated. */
    fun extract(state: Any): Map<String, Any?> {
        val result = linkedMapOf<String, Any?>()
        for (field in state.javaClass.declaredFields) {
            val spec = field.getAnnotation(LensField::class.java) ?: continue
            if (result.size >= 32) break
            val name = spec.alias.ifEmpty { field.name }
            if (!allowedName(name)) continue
            try {
                field.isAccessible = true
                val value = field.get(state)
                if (isPrimitive(value)) result[name] = value
            } catch (_: ReflectiveOperationException) {
                // One unsupported private field must not crash the running app.
            } catch (_: SecurityException) { }
        }
        return result
    }

    internal fun allowedName(name: String): Boolean {
        if (name.isEmpty() || name.length > 80) return false
        val lower = name.lowercase()
        return listOf("token", "secret", "password", "authorization", "cookie", "email", "phone", "address", "nric", "cardnumber").none { lower.contains(it) }
    }

    internal fun isPrimitive(value: Any?): Boolean = when (value) {
        is String, is Boolean, is Int, is Long, is Float, is Double, is Short, is Byte, is Enum<*> -> true
        else -> false
    }
}
