import Foundation

public enum CrossOverEdition: String, CaseIterable, Codable, Identifiable, Sendable {
    case stable
    case preview
    case custom

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .stable:
            return "CrossOver"
        case .preview:
            return "CrossOver Preview"
        case .custom:
            return "Custom"
        }
    }
}

public enum SetupAction: String, CaseIterable, Codable, Sendable {
    case install
    case repair
    case status
    case uninstall
    case startSteam = "start-steam"
    case stopSteam = "stop-steam"

    public var displayName: String {
        switch self {
        case .install:
            return "Install"
        case .repair:
            return "Repair"
        case .status:
            return "Check Status"
        case .uninstall:
            return "Uninstall"
        case .startSteam:
            return "Start Windows Steam"
        case .stopSteam:
            return "Stop Windows Steam"
        }
    }
}

public struct InstallerConfiguration: Equatable, Codable, Sendable {
    public var crossOverEdition: CrossOverEdition
    public var crossOverApplicationURL: URL?
    public var bottleName: String
    public var automaticRepair: Bool
    public var startAtLogin: Bool
    public var hideDockWhenBackgrounded: Bool
    public var hideWindowsTrayIcons: Bool

    public init(
        crossOverEdition: CrossOverEdition = .stable,
        crossOverApplicationURL: URL? = nil,
        bottleName: String = "Steam",
        automaticRepair: Bool = true,
        startAtLogin: Bool = true,
        hideDockWhenBackgrounded: Bool = false,
        hideWindowsTrayIcons: Bool = false
    ) {
        self.crossOverEdition = crossOverEdition
        self.crossOverApplicationURL = crossOverApplicationURL
        self.bottleName = bottleName
        self.automaticRepair = automaticRepair
        self.startAtLogin = startAtLogin
        self.hideDockWhenBackgrounded = hideDockWhenBackgrounded
        self.hideWindowsTrayIcons = hideWindowsTrayIcons
    }

    public var normalizedBottleName: String {
        bottleName.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public func validationErrors(for action: SetupAction, fileManager: FileManager = .default) -> [String] {
        guard action == .install || action == .repair else { return [] }
        var errors: [String] = []
        let bottle = normalizedBottleName

        if bottle.isEmpty {
            errors.append("Enter the name of the CrossOver bottle that contains Steam.")
        } else if bottle.count > 128 {
            errors.append("The bottle name must be 128 characters or fewer.")
        } else if bottle.contains("/") || bottle.contains("\n") || bottle.contains("\r") {
            errors.append("The bottle name cannot contain slashes or line breaks.")
        }

        if action.requiresCrossOverApplication {
            guard let applicationURL = crossOverApplicationURL else {
                errors.append("Select an installed CrossOver application.")
                return errors
            }

            var isDirectory: ObjCBool = false
            if !fileManager.fileExists(atPath: applicationURL.path, isDirectory: &isDirectory)
                || !isDirectory.boolValue
                || applicationURL.pathExtension.lowercased() != "app" {
                errors.append("The selected CrossOver application could not be found.")
            }
        }

        return errors
    }

    public func commandArguments(for action: SetupAction) -> [String] {
        guard action == .install || action == .repair else {
            return [action.rawValue, "--yes", "--json"]
        }
        var arguments = [
            action.rawValue,
            "--crossover-edition", crossOverEdition.rawValue
        ]

        if let crossOverApplicationURL {
            arguments += ["--crossover-app", crossOverApplicationURL.path]
        }

        arguments += [
            "--bottle", normalizedBottleName,
            automaticRepair ? "--auto-repair" : "--no-auto-repair",
            startAtLogin ? "--start-at-login" : "--no-start-at-login",
            hideDockWhenBackgrounded ? "--hide-dock" : "--no-hide-dock",
            hideWindowsTrayIcons ? "--hide-tray" : "--no-hide-tray",
            "--yes",
            "--json"
        ]

        return arguments
    }
}

public extension SetupAction {
    var requiresCrossOverApplication: Bool {
        switch self {
        case .install, .repair:
            return true
        case .status, .uninstall, .startSteam, .stopSteam:
            return false
        }
    }
}
