import Foundation

public struct CrossOverInstallation: Equatable, Identifiable, Sendable {
    public let edition: CrossOverEdition
    public let applicationURL: URL

    public init(edition: CrossOverEdition, applicationURL: URL) {
        self.edition = edition
        self.applicationURL = applicationURL
    }

    public var id: String { applicationURL.standardizedFileURL.path }

    public var displayName: String {
        applicationURL.deletingPathExtension().lastPathComponent
    }
}

public struct CrossOverDiscovery: @unchecked Sendable {
    public let searchRoots: [URL]
    private let fileManager: FileManager

    public init(
        searchRoots: [URL] = CrossOverDiscovery.defaultSearchRoots(),
        fileManager: FileManager = .default
    ) {
        self.searchRoots = searchRoots
        self.fileManager = fileManager
    }

    public func discover() -> [CrossOverInstallation] {
        var installationsByPath: [String: CrossOverInstallation] = [:]

        for root in searchRoots {
            guard let contents = try? fileManager.contentsOfDirectory(
                at: root,
                includingPropertiesForKeys: [.isDirectoryKey],
                options: [.skipsHiddenFiles]
            ) else {
                continue
            }

            for candidate in contents where Self.looksLikeCrossOver(candidate) {
                var isDirectory: ObjCBool = false
                guard fileManager.fileExists(atPath: candidate.path, isDirectory: &isDirectory),
                      isDirectory.boolValue else {
                    continue
                }

                let edition: CrossOverEdition = candidate.lastPathComponent
                    .localizedCaseInsensitiveContains("preview") ? .preview : .stable
                let installation = CrossOverInstallation(
                    edition: edition,
                    applicationURL: candidate.standardizedFileURL
                )
                installationsByPath[installation.id] = installation
            }
        }

        return installationsByPath.values.sorted { lhs, rhs in
            if lhs.edition != rhs.edition {
                return lhs.edition == .stable
            }
            return lhs.applicationURL.path.localizedStandardCompare(rhs.applicationURL.path) == .orderedAscending
        }
    }

    public func preferredInstallation(
        for edition: CrossOverEdition,
        among installations: [CrossOverInstallation]? = nil
    ) -> CrossOverInstallation? {
        guard edition != .custom else { return nil }
        return (installations ?? discover()).first { $0.edition == edition }
    }

    public static func defaultSearchRoots(fileManager: FileManager = .default) -> [URL] {
        var roots = [URL(fileURLWithPath: "/Applications", isDirectory: true)]
        if let userApplications = fileManager.urls(for: .applicationDirectory, in: .userDomainMask).first {
            roots.append(userApplications)
        }
        return roots
    }

    private static func looksLikeCrossOver(_ url: URL) -> Bool {
        url.pathExtension.lowercased() == "app"
            && url.deletingPathExtension().lastPathComponent.localizedCaseInsensitiveContains("crossover")
    }
}
