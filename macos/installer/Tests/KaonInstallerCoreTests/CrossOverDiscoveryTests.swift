import Foundation
@testable import KaonInstallerCore
import XCTest

final class CrossOverDiscoveryTests: XCTestCase {
    func testDiscoversStableAndPreviewButIgnoresOtherApps() throws {
        let temporaryDirectory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: temporaryDirectory) }

        let stable = temporaryDirectory.appendingPathComponent("CrossOver.app", isDirectory: true)
        let preview = temporaryDirectory.appendingPathComponent("CrossOver Preview.app", isDirectory: true)
        let other = temporaryDirectory.appendingPathComponent("Steam.app", isDirectory: true)
        for directory in [stable, preview, other] {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        }

        let discovery = CrossOverDiscovery(searchRoots: [temporaryDirectory])
        let installations = discovery.discover()

        XCTAssertEqual(installations.count, 2)
        XCTAssertEqual(installations.map(\.edition), [.stable, .preview])
        XCTAssertEqual(
            discovery.preferredInstallation(for: .preview, among: installations)?.applicationURL,
            preview
        )
    }

    func testDeduplicatesOverlappingSearchRoots() throws {
        let temporaryDirectory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: temporaryDirectory) }
        let stable = temporaryDirectory.appendingPathComponent("CrossOver.app", isDirectory: true)
        try FileManager.default.createDirectory(at: stable, withIntermediateDirectories: true)

        let discovery = CrossOverDiscovery(searchRoots: [temporaryDirectory, temporaryDirectory])

        XCTAssertEqual(discovery.discover().count, 1)
    }

    private func makeTemporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("CrossOverDiscoveryTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
}
