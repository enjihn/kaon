import SwiftUI

@main
struct KaonInstallerApp: App {
    var body: some Scene {
        WindowGroup {
            InstallerRootView()
                .frame(minWidth: 780, minHeight: 640)
        }
        .defaultSize(width: 880, height: 720)
        .windowResizability(.contentMinSize)
    }
}
