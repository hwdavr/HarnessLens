# Android SDK (debug / internal QA builds only)

Add the module to your Android Gradle workspace as `:harnesslens` and add `debugImplementation(project(":harnesslens"))`. Place `HarnessLens.install(this)` and direct recorder calls in `src/debug` or an equivalent internal-only build variant.

**Optional `@field:LensField` annotations on production-source data classes:** those annotations require a compile-time dependency visible to your app's main sources. If you want *zero HarnessLens references* in release artifacts, prefer the explicit state mapping from the debug source set, or move annotated model wrappers into the QA variant. Do not add the full recorder as a production dependency merely to compile annotations.

`HarnessLens.watch` collects `StateFlow` and can miss rapid conflated intermediate transitions. It captures *sampled state snapshots* for agent debugging, not a lossless reducer event journal. Exposed values are primitives only, max 32 keys, 240 characters per string. Recording is inactive without an `hlens capture start` control file.

No KSP, reflection of arbitrary unmarked fields, direct ViewModel instantiation, or action dispatcher is needed.
