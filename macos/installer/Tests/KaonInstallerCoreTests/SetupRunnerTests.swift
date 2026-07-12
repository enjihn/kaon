import Darwin
import Foundation
@testable import KaonInstallerCore
import XCTest

final class SetupRunnerTests: XCTestCase {
    func testRunnerInvokesEntrypointWithoutShellInterpolation() async throws {
        let temporaryDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("SetupRunnerTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: temporaryDirectory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: temporaryDirectory) }

        let entrypointURL = temporaryDirectory.appendingPathComponent("kaon-setup")
        let script = """
        #!/bin/zsh
        printf '{"action":"%s","edition":"%s","bottle":"%s"}\\n' "$1" "$3" "$7"
        """
        try Data(script.utf8).write(to: entrypointURL)
        XCTAssertEqual(chmod(entrypointURL.path, 0o755), 0)

        let crossOverURL = temporaryDirectory.appendingPathComponent("CrossOver Preview.app", isDirectory: true)
        try FileManager.default.createDirectory(at: crossOverURL, withIntermediateDirectories: true)
        let locator = EntrypointLocator(
            environment: ["KAON_SETUP_ENTRYPOINT": entrypointURL.path],
            currentDirectoryURL: temporaryDirectory,
            homeDirectoryURL: temporaryDirectory,
            bundleResourceURL: nil,
            sourceFileURL: nil
        )
        let runner = SetupRunner(locator: locator)
        let configuration = InstallerConfiguration(
            crossOverEdition: .preview,
            crossOverApplicationURL: crossOverURL,
            bottleName: "Bottle; touch should-not-exist"
        )

        let result = try await runner.run(action: .install, configuration: configuration)

        XCTAssertTrue(result.succeeded)
        XCTAssertTrue(result.standardOutput.contains("\"action\":\"install\""))
        XCTAssertTrue(result.standardOutput.contains("\"edition\":\"preview\""))
        XCTAssertTrue(result.standardOutput.contains("Bottle; touch should-not-exist"))
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: temporaryDirectory.appendingPathComponent("should-not-exist").path
        ))
    }
}
