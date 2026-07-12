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
    @Published private(set) var operationState: OperationState = .idle
    @Published private(set) var operationLog = ""

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

        if let installedConfiguration = installedConfigurationStore.load() {
            configuration = installedConfiguration
            return
        }

        let defaultEdition: CrossOverEdition
        if installations.contains(where: { $0.edition == .stable }) {
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
            )?.applicationURL
        )
    }

    var isRunning: Bool { operationState.isRunning }

    func refreshDiscovery() {
        discoveredInstallations = discovery.discover()
        if configuration.crossOverEdition != .custom {
            configuration.crossOverApplicationURL = discovery.preferredInstallation(
                for: configuration.crossOverEdition,
                among: discoveredInstallations
            )?.applicationURL
        }
    }

    func selectEdition(_ edition: CrossOverEdition) {
        configuration.crossOverEdition = edition
        guard edition != .custom else {
            configuration.crossOverApplicationURL = nil
            return
        }
        configuration.crossOverApplicationURL = discovery.preferredInstallation(
            for: edition,
            among: discoveredInstallations
        )?.applicationURL
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
    }

    func validationErrors(for action: SetupAction) -> [String] {
        configuration.validationErrors(for: action)
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
                    if let warning = Self.warningMessage(from: result.standardOutput) {
                        operationState = .warning(action, warning)
                    } else {
                        operationState = .succeeded(action, Self.successMessage(for: action))
                    }
                } else {
                    let message = output.isEmpty
                        ? "The setup engine exited with status \(result.terminationStatus)."
                        : output
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
            return "Kaon is installed and the selected automation is active."
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
}
