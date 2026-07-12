import Foundation

public struct SetupResult: Equatable, Sendable {
    public let action: SetupAction
    public let terminationStatus: Int32
    public let standardOutput: String
    public let standardError: String

    public init(
        action: SetupAction,
        terminationStatus: Int32,
        standardOutput: String,
        standardError: String
    ) {
        self.action = action
        self.terminationStatus = terminationStatus
        self.standardOutput = standardOutput
        self.standardError = standardError
    }

    public var succeeded: Bool { terminationStatus == 0 }

    public var combinedOutput: String {
        [standardOutput, standardError]
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: "\n")
    }
}

public enum SetupRunnerError: LocalizedError {
    case invalidConfiguration([String])
    case couldNotPrepareOutput
    case couldNotDecodeOutput

    public var errorDescription: String? {
        switch self {
        case let .invalidConfiguration(errors):
            return errors.joined(separator: "\n")
        case .couldNotPrepareOutput:
            return "The installer could not prepare its temporary output files."
        case .couldNotDecodeOutput:
            return "The setup engine returned output that could not be read."
        }
    }
}

public actor SetupRunner {
    private let locator: EntrypointLocator
    private let fileManager: FileManager

    public init(locator: EntrypointLocator = EntrypointLocator(), fileManager: FileManager = .default) {
        self.locator = locator
        self.fileManager = fileManager
    }

    public func run(action: SetupAction, configuration: InstallerConfiguration) async throws -> SetupResult {
        let validationErrors = configuration.validationErrors(for: action, fileManager: fileManager)
        guard validationErrors.isEmpty else {
            throw SetupRunnerError.invalidConfiguration(validationErrors)
        }

        let entrypointURL = try locator.locate()
        let temporaryDirectory = fileManager.temporaryDirectory
            .appendingPathComponent("KaonInstaller-\(UUID().uuidString)", isDirectory: true)
        try fileManager.createDirectory(at: temporaryDirectory, withIntermediateDirectories: true)
        defer { try? fileManager.removeItem(at: temporaryDirectory) }

        let standardOutputURL = temporaryDirectory.appendingPathComponent("stdout")
        let standardErrorURL = temporaryDirectory.appendingPathComponent("stderr")
        guard fileManager.createFile(atPath: standardOutputURL.path, contents: nil),
              fileManager.createFile(atPath: standardErrorURL.path, contents: nil) else {
            throw SetupRunnerError.couldNotPrepareOutput
        }

        let standardOutputHandle = try FileHandle(forWritingTo: standardOutputURL)
        let standardErrorHandle = try FileHandle(forWritingTo: standardErrorURL)
        defer {
            try? standardOutputHandle.close()
            try? standardErrorHandle.close()
        }

        let process = Process()
        let arguments = configuration.commandArguments(for: action)
        if fileManager.isExecutableFile(atPath: entrypointURL.path) {
            process.executableURL = entrypointURL
            process.arguments = arguments
        } else {
            process.executableURL = URL(fileURLWithPath: "/bin/zsh")
            process.arguments = [entrypointURL.path] + arguments
        }
        process.standardOutput = standardOutputHandle
        process.standardError = standardErrorHandle
        process.environment = ProcessInfo.processInfo.environment

        try process.run()
        let terminationStatus: Int32 = await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                process.waitUntilExit()
                continuation.resume(returning: process.terminationStatus)
            }
        }

        try standardOutputHandle.synchronize()
        try standardErrorHandle.synchronize()

        let standardOutputData = try Data(contentsOf: standardOutputURL)
        let standardErrorData = try Data(contentsOf: standardErrorURL)
        guard let standardOutput = String(data: standardOutputData, encoding: .utf8),
              let standardError = String(data: standardErrorData, encoding: .utf8) else {
            throw SetupRunnerError.couldNotDecodeOutput
        }

        return SetupResult(
            action: action,
            terminationStatus: terminationStatus,
            standardOutput: standardOutput,
            standardError: standardError
        )
    }
}
