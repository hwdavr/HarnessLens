// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "HarnessLens",
    platforms: [.iOS(.v15)],
    products: [.library(name: "HarnessLens", targets: ["HarnessLens"])],
    targets: [
        .target(name: "HarnessLens"),
        .testTarget(name: "HarnessLensTests", dependencies: ["HarnessLens"])
    ]
)
