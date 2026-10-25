// EXAMPLE ONLY — put initialization in your debug/test app variant, not production.
// android/app/src/debug/java/com/example/DebugApplication.kt
// override fun onCreate() { super.onCreate(); HarnessLens.install(this) }

// Existing model; optional annotation approach:
// data class PaymentState(
//     @field:LensField val status: String,
//     @field:LensField val retryCount: Int,
//     val accessToken: String // Not annotated: NEVER exported
// )
// In the live ViewModel, after uiState is initialized:
// init { HarnessLens.watch("ev.payment", uiState, viewModelScope) }

// Alternative, NO annotations, just an explicit allowlist when the state changes:
// HarnessLens.state("ev.payment", mapOf("status" to uiState.value.status, "retryCount" to uiState.value.retryCount))

// At the existing AnalyticsTracker boundary in a debug-only decorator:
// override fun track(name: String, params: Map<String, Any>) {
//     HarnessLens.analytics(name, params)
//     delegate.track(name, params) // Keep real SDK delivery unchanged
// }
