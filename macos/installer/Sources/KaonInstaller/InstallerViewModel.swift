import AppKit
import Foundation
import KaonInstallerCore
import UniformTypeIdentifiers

@MainActor
final class InstallerViewModel: ObservableObject {
    enum OperationState: Equatable {
        case idle
        case running(SetupAction)
        case succeeded(SetupAction, String)
        case warning(SetupAction, String)
        case failed(SetupAction?, String)

        var isRunning: Bool {
            if case .running = self { return true }
            return false
        }
    }

    @Published var configuration: InstallerConfiguration
    @Published private(set) var discoveredInstallations: [CrossOverInstallation]
    @Published private(set) var detectedSteamBottles: [String] = []
    @Published private(set) var nativeSteamReady = false
    @Published private(set) var nativeSteamRunning = false
    @Published private(set) var selectedBottleHasWindowsSteam = false
    @Published private(set) var selectedBottleMatchesEdition = true
    @Published private(set) var operationState: OperationState = .idle
    @Published private(set) var operationLog = ""
    @Published private(set) var installationCompleted = false

    private let discovery: CrossOverDiscovery
    private let runner: SetupRunner

    init(
        discovery: CrossOverDiscovery = CrossOverDiscovery(),
        installedConfigurationStore: InstalledConfigurationStore = InstalledConfigurationStore(),
        runner: SetupRunner = SetupRunner()
    ) {
        self.discovery = discovery
        self.runner = runner

        let installations = discovery.discover()
        discoveredInstallations = installations
        let steamBottles = Self.discoverSteamBottles()
        detectedSteamBottles = steamBottles

        if let installedConfiguration = installedConfigurationStore.load() {
            configuration = installedConfiguration
            refreshPrerequisites()
            return
        }

        let defaultBottle = steamBottles.contains("Steam")
            ? "Steam"
            : (steamBottles.first ?? "Steam")
        let defaultEdition: CrossOverEdition
        if Self.bottleRequiresPreview(defaultBottle)
            && installations.contains(where: { $0.edition == .preview }) {
            defaultEdition = .preview
        } else if installations.contains(where: { $0.edition == .stable }) {
            defaultEdition = .stable
        } else if installations.contains(where: { $0.edition == .preview }) {
            defaultEdition = .preview
        } else {
            defaultEdition = .stable
        }

        configuration = InstallerConfiguration(
            crossOverEdition: defaultEdition,
            crossOverApplicationURL: discovery.preferredInstallation(
                for: defaultEdition,
                among: installations
            )?.applicationURL,
            bottleName: defaultBottle
        )
        refreshPrerequisites()
    }

    var isRunning: Bool { operationState.isRunning }

    var selectedCrossOverApplicationIsValid: Bool {
        guard let applicationURL = configuration.crossOverApplicationURL else { return false }
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(
            atPath: applicationURL.path,
            isDirectory: &isDirectory
        ) && isDirectory.boolValue && applicationURL.pathExtension.lowercased() == "app"
    }

    func refreshDiscovery() {
        discoveredInstallations = discovery.discover()
        detectedSteamBottles = Self.discoverSteamBottles()
        if configuration.crossOverEdition != .custom {
            configuration.crossOverApplicationURL = discovery.preferredInstallation(
                for: configuration.crossOverEdition,
                among: discoveredInstallations
            )?.applicationURL
        }
        refreshPrerequisites()
    }

    func selectEdition(_ edition: CrossOverEdition) {
        configuration.crossOverEdition = edition
        guard edition != .custom else {
            configuration.crossOverApplicationURL = nil
            refreshPrerequisites()
            return
        }
        configuration.crossOverApplicationURL = discovery.preferredInstallation(
            for: edition,
            among: discoveredInstallations
        )?.applicationURL
        refreshPrerequisites()
    }

    func chooseCustomApplication() {
        let panel = NSOpenPanel()
        panel.title = "Choose CrossOver"
        panel.message = "Select the CrossOver application that owns your Steam bottle."
        panel.prompt = "Choose"
        panel.directoryURL = URL(fileURLWithPath: "/Applications", isDirectory: true)
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.treatsFilePackagesAsDirectories = false
        panel.allowedContentTypes = [.applicationBundle]

        guard panel.runModal() == .OK, let selectedURL = panel.url else { return }
        configuration.crossOverEdition = .custom
        configuration.crossOverApplicationURL = selectedURL
        refreshPrerequisites()
    }

    func validationErrors(for action: SetupAction) -> [String] {
        var errors = configuration.validationErrors(for: action)
        if action == .install || action == .repair {
            if !nativeSteamReady {
                errors.append("Open native Steam once, let your Library load, then quit Steam before continuing.")
            }
            if nativeSteamRunning {
                errors.append("Native Steam is still open. In Steam, choose Steam → Quit Steam, then click Refresh Checks.")
            }
            if !selectedBottleHasWindowsSteam {
                errors.append("Windows Steam was not found in the selected bottle. Open CrossOver, install Steam there, then click Refresh Checks.")
            }
            if !selectedBottleMatchesEdition {
                errors.append("This bottle was created for CrossOver Preview. Select CrossOver Preview above, then refresh the checks.")
            }
        }
        return errors
    }

    func refreshPrerequisites() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        nativeSteamReady = FileManager.default.fileExists(
            atPath: home.appendingPathComponent(
                "Library/Application Support/Steam/appcache/appinfo.vdf"
            ).path
        )
        nativeSteamRunning = Self.processIsRunning(named: "steam_osx")
        let bottle = configuration.normalizedBottleName
        selectedBottleHasWindowsSteam = !bottle.isEmpty && FileManager.default.fileExists(
            atPath: home.appendingPathComponent(
                "Library/Application Support/CrossOver/Bottles/\(bottle)/drive_c/Program Files (x86)/Steam/steam.exe"
            ).path
        )
        selectedBottleMatchesEdition = configuration.crossOverEdition != .stable
            || !Self.bottleRequiresPreview(bottle)
    }

    func openSelectedCrossOver() {
        guard let applicationURL = configuration.crossOverApplicationURL else { return }
        NSWorkspace.shared.open(applicationURL)
    }

    func openNativeSteam() {
        let candidates = [
            URL(fileURLWithPath: "/Applications/Steam.app"),
            FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications/Steam.app")
        ]
        if let application = candidates.first(where: { FileManager.default.fileExists(atPath: $0.path) }) {
            NSWorkspace.shared.open(application)
            return
        }
        guard let url = URL(string: "https://store.steampowered.com/about/") else { return }
        NSWorkspace.shared.open(url)
    }

    func run(_ action: SetupAction) {
        guard !isRunning else { return }

        let errors = validationErrors(for: action)
        guard errors.isEmpty else {
            operationState = .failed(action, errors.joined(separator: "\n"))
            operationLog = errors.joined(separator: "\n")
            return
        }

        operationState = .running(action)
        operationLog = "Running \(action.displayName.lowercased())…"
        let configuration = configuration

        Task {
            do {
                let result = try await runner.run(action: action, configuration: configuration)
                let output = Self.prettyPrintedOutput(result.combinedOutput)
                operationLog = output.isEmpty ? "The setup engine finished without additional output." : output

                if result.succeeded {
                    if action == .install || action == .repair { installationCompleted = true }
                    if action == .uninstall { installationCompleted = false }
                    if let warning = Self.warningMessage(from: result.standardOutput) {
                        operationState = .warning(action, warning)
                    } else {
                        operationState = .succeeded(action, Self.successMessage(for: action))
                    }
                } else {
                    let message = Self.failureMessage(from: result.combinedOutput)
                        ?? (output.isEmpty
                            ? "The setup engine exited with status \(result.terminationStatus)."
                            : output)
                    operationState = .failed(action, message)
                }
            } catch {
                let message = error.localizedDescription
                operationLog = message
                operationState = .failed(action, message)
            }
        }
    }

    func clearOperation() {
        guard !isRunning else { return }
        operationState = .idle
        operationLog = ""
    }

    private static func successMessage(for action: SetupAction) -> String {
        switch action {
        case .install:
            return "Kaon is installed. Reopen native Steam and choose the “Shared CrossOver … Library” whenever you install a Windows game."
        case .repair:
            return "Kaon’s files and Steam launch metadata were repaired."
        case .status:
            return "Kaon’s status check completed."
        case .uninstall:
            return "Kaon’s active integration was removed. Games, shared-library entries, and recovery files were preserved."
        case .startSteam:
            return "Windows Steam was started."
        case .stopSteam:
            return "Windows Steam was stopped."
        }
    }

    private static func prettyPrintedOutput(_ output: String) -> String {
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              let data = trimmed.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data),
              JSONSerialization.isValidJSONObject(object),
              let formattedData = try? JSONSerialization.data(
                withJSONObject: object,
                options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
              ),
              let formatted = String(data: formattedData, encoding: .utf8) else {
            return trimmed
        }
        return formatted
    }

    private static func warningMessage(from output: String) -> String? {
        guard let data = output.data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: data) else {
            return nil
        }
        var messages: [String] = []

        func inspect(_ value: Any) {
            if let dictionary = value as? [String: Any] {
                if dictionary["degraded"] as? Bool == true {
                    if let message = dictionary["message"] as? String {
                        messages.append(message)
                    } else if let error = dictionary["error"] as? String {
                        messages.append(error)
                    } else {
                        messages.append("A selected feature is installed but currently degraded.")
                    }
                }
                if let warnings = dictionary["warnings"] as? [String] {
                    messages.append(contentsOf: warnings)
                }
                dictionary.values.forEach(inspect)
            } else if let array = value as? [Any] {
                array.forEach(inspect)
            }
        }

        inspect(root)
        let unique = messages.reduce(into: [String]()) { result, message in
            if !result.contains(message) { result.append(message) }
        }
        return unique.isEmpty ? nil : unique.joined(separator: "\n")
    }

    private static func failureMessage(from output: String) -> String? {
        guard let data = output.data(using: .utf8),
              let decoded = try? JSONSerialization.jsonObject(with: data),
              let object = decoded as? [String: Any],
              let error = object["error"] as? String,
              !error.isEmpty else {
            return nil
        }
        return error
    }

    private static func discoverSteamBottles(fileManager: FileManager = .default) -> [String] {
        let root = fileManager.homeDirectoryForCurrentUser.appendingPathComponent(
            "Library/Application Support/CrossOver/Bottles",
            isDirectory: true
        )
        guard let bottles = try? fileManager.contentsOfDirectory(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }
        return bottles.compactMap { bottle in
            let steam = bottle.appendingPathComponent(
                "drive_c/Program Files (x86)/Steam/steam.exe"
            )
            return fileManager.fileExists(atPath: steam.path) ? bottle.lastPathComponent : nil
        }.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }

    private static func bottleRequiresPreview(
        _ bottle: String,
        fileManager: FileManager = .default
    ) -> Bool {
        guard !bottle.isEmpty else { return false }
        let configuration = fileManager.homeDirectoryForCurrentUser.appendingPathComponent(
            "Library/Application Support/CrossOver/Bottles/\(bottle)/cxbottle.conf"
        )
        guard let text = try? String(contentsOf: configuration, encoding: .utf8) else {
            return false
        }
        return text.range(
            of: #"(?m)^\s*"Preview"\s*=\s*"1"\s*$"#,
            options: .regularExpression
        ) != nil
    }

    private static func processIsRunning(named name: String) -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        process.arguments = ["-x", name]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus == 0
        } catch {
            return false
        }
    }
}
