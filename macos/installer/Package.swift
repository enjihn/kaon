// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "KaonInstaller",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "KaonInstaller", targets: ["KaonInstaller"])
    ],
    targets: [
        .target(
            name: "KaonInstallerCore"
        ),
        .executableTarget(
            name: "KaonInstaller",
            dependencies: ["KaonInstallerCore"]
        ),
        .testTarget(
            name: "KaonInstallerCoreTests",
            dependencies: ["KaonInstallerCore"]
        )
    ]
)
