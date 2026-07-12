import Foundation
@testable import KaonInstallerCore
import XCTest

final class InstalledConfigurationStoreTests: XCTestCase {
    func testLoadsInstalledConfiguration() throws {
        let temporaryDirectory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: temporaryDirectory) }
        let configurationURL = temporaryDirectory.appendingPathComponent("config.json")
        let json = """
        {
          "schema_version": 1,
          "crossover_edition": "preview",
          "crossover_app": "/Applications/CrossOver Preview.app",
          "bottle": "Steam Preview",
          "auto_repair": true,
          "start_at_login": true,
          "hide_dock": true,
          "hide_tray": false
        }
        """
        try Data(json.utf8).write(to: configurationURL)

        let configuration = InstalledConfigurationStore(configurationURL: configurationURL).load()

        XCTAssertEqual(configuration?.crossOverEdition, .preview)
        XCTAssertEqual(configuration?.crossOverApplicationURL?.path, "/Applications/CrossOver Preview.app")
        XCTAssertEqual(configuration?.bottleName, "Steam Preview")
        XCTAssertEqual(configuration?.automaticRepair, true)
        XCTAssertEqual(configuration?.startAtLogin, true)
        XCTAssertEqual(configuration?.hideDockWhenBackgrounded, true)
        XCTAssertEqual(configuration?.hideWindowsTrayIcons, false)
    }

    func testRejectsUnsupportedOrSymbolicLinkConfiguration() throws {
        let temporaryDirectory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: temporaryDirectory) }
        let targetURL = temporaryDirectory.appendingPathComponent("target.json")
        let linkURL = temporaryDirectory.appendingPathComponent("config.json")
        try Data("{\"schema_version\":2,\"crossover_app\":\"/Applications/CrossOver.app\"}".utf8)
            .write(to: targetURL)

        XCTAssertNil(InstalledConfigurationStore(configurationURL: targetURL).load())
        try FileManager.default.createSymbolicLink(at: linkURL, withDestinationURL: targetURL)
        XCTAssertNil(InstalledConfigurationStore(configurationURL: linkURL).load())
    }

    private func makeTemporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("InstalledConfigurationStoreTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
}
