// EXAMPLE — compile only in a dedicated test/debug configuration.
// import HarnessLens
//
// In your existing analytics abstraction:
// func track(_ name: String, parameters: [String: Any]) {
//     #if DEBUG
//     HarnessLens.shared.analytics(name, params: parameters)
//     #endif
//     firebaseTracker.track(name, parameters: parameters)
// }
//
// In your real SwiftUI Observable model after a business state transition:
// #if DEBUG
// HarnessLens.shared.state("ev.payment", fields: [
//     "status": paymentState.status.rawValue,
//     "retryCount": paymentState.retryCount
// ])
// #endif
//
// This observes the real model; it never constructs a second instance.
