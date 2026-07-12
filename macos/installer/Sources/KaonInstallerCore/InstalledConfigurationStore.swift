import Foundation

public struct InstalledConfigurationStore: Sendable {
    public let configurationURL: URL

    public init(
        configurationURL: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Kaon", isDirectory: true)
            .appendingPathComponent("config.json", isDirectory: false)
    ) {
        self.configurationURL = configurationURL
    }

    public func load() -> InstallerConfiguration? {
        guard let values = try? configurationURL.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey]),
              values.isRegularFile == true,
              values.isSymbolicLink != true,
              let data = try? Data(contentsOf: configurationURL),
              let stored = try? JSONDecoder().decode(StoredConfiguration.self, from: data),
              stored.schemaVersion == 1,
              let applicationPath = stored.crossOverApplicationPath,
              !applicationPath.isEmpty else {
            return nil
        }

        let edition = CrossOverEdition(rawValue: stored.crossOverEdition ?? "custom") ?? .custom
        return InstallerConfiguration(
            crossOverEdition: edition,
            crossOverApplicationURL: URL(fileURLWithPath: applicationPath),
            bottleName: stored.bottleName ?? "Steam",
            automaticRepair: stored.automaticRepair ?? true,
            startAtLogin: stored.startAtLogin ?? true,
            hideDockWhenBackgrounded: stored.hideDock ?? false,
            hideWindowsTrayIcons: stored.hideTray ?? false
        )
    }
}

private struct StoredConfiguration: Decodable {
    let schemaVersion: Int
    let crossOverEdition: String?
    let crossOverApplicationPath: String?
    let bottleName: String?
    let automaticRepair: Bool?
    let startAtLogin: Bool?
    let hideDock: Bool?
    let hideTray: Bool?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case crossOverEdition = "crossover_edition"
        case crossOverApplicationPath = "crossover_app"
        case bottleName = "bottle"
        case automaticRepair = "auto_repair"
        case startAtLogin = "start_at_login"
        case hideDock = "hide_dock"
        case hideTray = "hide_tray"
    }
}
