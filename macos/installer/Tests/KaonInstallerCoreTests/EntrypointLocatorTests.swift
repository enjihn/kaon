import Foundation
@testable import KaonInstallerCore
import XCTest

final class EntrypointLocatorTests: XCTestCase {
    func testEnvironmentOverrideHasPriority() throws {
        let temporaryDirectory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: temporaryDirectory) }
        let overrideURL = temporaryDirectory.appendingPathComponent("custom-setup")
        XCTAssertTrue(FileManager.default.createFile(atPath: overrideURL.path, contents: Data()))
        let locator = EntrypointLocator(
            environment: ["KAON_SETUP_ENTRYPOINT": overrideURL.path],
            currentDirectoryURL: temporaryDirectory,
            homeDirectoryURL: temporaryDirectory,
            bundleResourceURL: nil,
            sourceFileURL: nil
        )

        let locatedURL = try locator.locate()
        XCTAssertEqual(locatedURL, overrideURL)
    }

    func testFindsRepositoryEntrypointFromNestedDirectory() throws {
        let temporaryDirectory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: temporaryDirectory) }
        let entrypointURL = temporaryDirectory.appendingPathComponent("macos/bin/kaon-setup")
        try FileManager.default.createDirectory(
            at: entrypointURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        XCTAssertTrue(FileManager.default.createFile(atPath: entrypointURL.path, contents: Data()))
        let nested = temporaryDirectory.appendingPathComponent("macos/installer/Sources", isDirectory: true)
        try FileManager.default.createDirectory(at: nested, withIntermediateDirectories: true)
        let locator = EntrypointLocator(
            environment: [:],
            currentDirectoryURL: nested,
            homeDirectoryURL: temporaryDirectory.appendingPathComponent("empty-home"),
            bundleResourceURL: nil,
            sourceFileURL: nil
        )

        let locatedURL = try locator.locate()
        XCTAssertEqual(locatedURL, entrypointURL)
    }

    private func makeTemporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("EntrypointLocatorTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
}
