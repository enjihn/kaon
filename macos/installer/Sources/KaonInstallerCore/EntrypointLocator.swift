import Foundation

public enum EntrypointLocationError: LocalizedError, Equatable {
    case notFound([String])

    public var errorDescription: String? {
        switch self {
        case let .notFound(paths):
            let searched = paths.map { "  \u{2022} \($0)" }.joined(separator: "\n")
            return "Kaon’s setup engine was not found. Reinstall the Kaon Installer or set KAON_SETUP_ENTRYPOINT.\n\(searched)"
        }
    }
}

public struct EntrypointLocator: @unchecked Sendable {
    public var environment: [String: String]
    public var currentDirectoryURL: URL
    public var homeDirectoryURL: URL
    public var bundleResourceURL: URL?
    public var sourceFileURL: URL?
    private let fileManager: FileManager

    public init(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        currentDirectoryURL: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true),
        homeDirectoryURL: URL = FileManager.default.homeDirectoryForCurrentUser,
        bundleResourceURL: URL? = Bundle.main.resourceURL,
        sourceFileURL: URL? = URL(fileURLWithPath: #filePath),
        fileManager: FileManager = .default
    ) {
        self.environment = environment
        self.currentDirectoryURL = currentDirectoryURL
        self.homeDirectoryURL = homeDirectoryURL
        self.bundleResourceURL = bundleResourceURL
        self.sourceFileURL = sourceFileURL
        self.fileManager = fileManager
    }

    public func locate() throws -> URL {
        let candidates = candidateURLs()
        if let match = candidates.first(where: { fileManager.fileExists(atPath: $0.path) }) {
            return match.standardizedFileURL
        }
        throw EntrypointLocationError.notFound(candidates.map(\.path))
    }

    public func candidateURLs() -> [URL] {
        var candidates: [URL] = []

        if let override = environment["KAON_SETUP_ENTRYPOINT"], !override.isEmpty {
            candidates.append(URL(fileURLWithPath: override))
        }

        if let bundleResourceURL {
            candidates.append(bundleResourceURL.appendingPathComponent("kaon-setup", isDirectory: false))
            candidates.append(bundleResourceURL.appendingPathComponent("bin/kaon-setup", isDirectory: false))
            candidates.append(bundleResourceURL.appendingPathComponent("Kaon/bin/kaon-setup", isDirectory: false))
        }

        candidates += repositoryCandidates(startingAt: currentDirectoryURL)

        if let sourceFileURL {
            candidates += repositoryCandidates(startingAt: sourceFileURL.deletingLastPathComponent())
        }

        candidates.append(
            homeDirectoryURL
                .appendingPathComponent("Library/Application Support/Kaon", isDirectory: true)
                .appendingPathComponent("bin/kaon-setup", isDirectory: false)
        )

        var seen = Set<String>()
        return candidates.filter { seen.insert($0.standardizedFileURL.path).inserted }
    }

    private func repositoryCandidates(startingAt startURL: URL) -> [URL] {
        var directory = startURL.standardizedFileURL
        var candidates: [URL] = []

        for _ in 0..<9 {
            candidates.append(directory.appendingPathComponent("macos/bin/kaon-setup", isDirectory: false))
            let parent = directory.deletingLastPathComponent()
            if parent.path == directory.path { break }
            directory = parent
        }

        return candidates
    }
}
