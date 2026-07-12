import KaonInstallerCore
import SwiftUI

private enum InstallerPage: String, CaseIterable, Identifiable {
    case setup
    case review
    case maintenance

    var id: String { rawValue }

    var title: String {
        switch self {
        case .setup: return "Setup"
        case .review: return "Review & Install"
        case .maintenance: return "Maintenance"
        }
    }

    var symbol: String {
        switch self {
        case .setup: return "slider.horizontal.3"
        case .review: return "checklist"
        case .maintenance: return "wrench.and.screwdriver"
        }
    }
}

private final class InstallerNavigationState: ObservableObject {
    @Published var selectedPage: InstallerPage? = .setup
}

struct InstallerRootView: View {
    @StateObject private var viewModel = InstallerViewModel()
    @StateObject private var navigationState = InstallerNavigationState()

    var body: some View {
        NavigationSplitView {
            List(InstallerPage.allCases, selection: $navigationState.selectedPage) { page in
                Label(page.title, systemImage: page.symbol)
                    .tag(page)
            }
            .navigationTitle("Kaon")
            .navigationSplitViewColumnWidth(min: 180, ideal: 210, max: 250)
            .safeAreaInset(edge: .bottom) {
                VStack(alignment: .leading, spacing: 6) {
                    Label("User-level install", systemImage: "person.crop.circle.badge.checkmark")
                        .font(.caption.weight(.medium))
                    Text("No administrator password required")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()
            }
        } detail: {
            Group {
                switch navigationState.selectedPage ?? .setup {
                case .setup:
                    SetupView(viewModel: viewModel) {
                        navigationState.selectedPage = .review
                    }
                case .review:
                    ReviewInstallView(viewModel: viewModel)
                case .maintenance:
                    MaintenanceView(viewModel: viewModel)
                }
            }
            .safeAreaInset(edge: .bottom) {
                if viewModel.operationState != .idle {
                    OperationPanel(viewModel: viewModel)
                        .padding(.horizontal, 24)
                        .padding(.bottom, 16)
                }
            }
        }
    }
}

private struct PageHeader: View {
    let title: String
    let subtitle: String
    let symbol: String

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            Image(systemName: symbol)
                .font(.system(size: 30, weight: .semibold))
                .foregroundStyle(.tint)
                .frame(width: 48, height: 48)
                .background(.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.title.bold())
                Text(subtitle)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct SetupView: View {
    @ObservedObject var viewModel: InstallerViewModel
    let continueAction: () -> Void

    private var editionBinding: Binding<CrossOverEdition> {
        Binding(
            get: { viewModel.configuration.crossOverEdition },
            set: { viewModel.selectEdition($0) }
        )
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                PageHeader(
                    title: "Configure Kaon",
                    subtitle: "Connect native Steam to games installed in a CrossOver Steam bottle.",
                    symbol: "gamecontroller.fill"
                )

                SettingsCard(title: "CrossOver", symbol: "shippingbox") {
                    VStack(alignment: .leading, spacing: 12) {
                        Picker("Edition", selection: editionBinding) {
                            ForEach(CrossOverEdition.allCases) { edition in
                                Text(edition.displayName).tag(edition)
                            }
                        }
                        .pickerStyle(.segmented)

                        HStack(spacing: 10) {
                            Image(systemName: viewModel.configuration.crossOverApplicationURL == nil
                                  ? "exclamationmark.triangle.fill"
                                  : "checkmark.circle.fill")
                                .foregroundStyle(viewModel.configuration.crossOverApplicationURL == nil ? .orange : .green)
                            VStack(alignment: .leading, spacing: 2) {
                                if let url = viewModel.configuration.crossOverApplicationURL {
                                    Text(url.lastPathComponent)
                                        .fontWeight(.medium)
                                    Text(url.path)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .textSelection(.enabled)
                                } else {
                                    Text("No matching application found")
                                        .fontWeight(.medium)
                                    Text("Install this edition or choose a custom CrossOver app.")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            if viewModel.configuration.crossOverEdition == .custom {
                                Button("Choose…") { viewModel.chooseCustomApplication() }
                            } else {
                                Button("Scan Again") { viewModel.refreshDiscovery() }
                            }
                        }
                        .padding(12)
                        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))

                        TextField("Bottle name", text: $viewModel.configuration.bottleName)
                            .textFieldStyle(.roundedBorder)
                            .onChange(of: viewModel.configuration.bottleName) { _ in
                                viewModel.refreshPrerequisites()
                            }
                        Text("Use the bottle that contains Windows Steam. The default bottle name is “Steam.”")
                            .font(.caption)
                            .foregroundStyle(.secondary)

                        if !viewModel.detectedSteamBottles.isEmpty {
                            Menu("Choose a detected Steam bottle") {
                                ForEach(viewModel.detectedSteamBottles, id: \.self) { bottle in
                                    Button(bottle) {
                                        viewModel.configuration.bottleName = bottle
                                        viewModel.refreshPrerequisites()
                                    }
                                }
                            }
                        }
                    }
                }

                SettingsCard(title: "Before you install", symbol: "checkmark.shield") {
                    VStack(alignment: .leading, spacing: 12) {
                        PrerequisiteRow(
                            ready: viewModel.selectedCrossOverApplicationIsValid,
                            readyText: "CrossOver is selected",
                            missingText: "Choose a valid installed CrossOver app"
                        )
                        Divider()
                        PrerequisiteRow(
                            ready: viewModel.nativeSteamReady,
                            readyText: "Native Steam has been opened and initialized",
                            missingText: "Open native Steam once and let the Library load"
                        )
                        Divider()
                        PrerequisiteRow(
                            ready: !viewModel.nativeSteamRunning,
                            readyText: "Native Steam is fully quit and safe to configure",
                            missingText: "Quit native Steam, then click Refresh Checks"
                        )
                        Divider()
                        PrerequisiteRow(
                            ready: viewModel.selectedBottleHasWindowsSteam,
                            readyText: "Windows Steam was found in this bottle",
                            missingText: "Install Windows Steam in this CrossOver bottle"
                        )
                        Divider()
                        PrerequisiteRow(
                            ready: viewModel.selectedBottleMatchesEdition,
                            readyText: "This bottle matches the selected CrossOver edition",
                            missingText: "This bottle requires CrossOver Preview—select Preview above"
                        )

                        Text("Quit native Steam completely before clicking Install. Kaon will stop safely and explain what to do if Steam is still open.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)

                        HStack {
                            Button("Open or Get Native Steam") { viewModel.openNativeSteam() }
                            Button("Open Selected CrossOver") { viewModel.openSelectedCrossOver() }
                                .disabled(!viewModel.selectedCrossOverApplicationIsValid)
                            Spacer()
                            Button("Refresh Checks") {
                                viewModel.refreshDiscovery()
                                viewModel.refreshPrerequisites()
                            }
                        }
                    }
                }

                SettingsCard(title: "Reliability", symbol: "arrow.triangle.2.circlepath") {
                    OptionRow(
                        title: "Automatically repair Kaon changes",
                        detail: "A lightweight user agent checks Steam’s metadata and restores Kaon launch options if a Steam update or edit removes them.",
                        isOn: $viewModel.configuration.automaticRepair
                    )
                    Divider()
                    OptionRow(
                        title: "Start Windows Steam at login",
                        detail: "Starts the selected bottle’s Steam immediately after setup and after future sign-ins. You can stop or start it from Maintenance. Native Steam remains the interface you normally use.",
                        isOn: $viewModel.configuration.startAtLogin
                    )
                }

                SettingsCard(title: "Optional interface hiding", symbol: "eye.slash") {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("These options only change the managed background session. They are off by default.")
                            .font(.caption)
                            .foregroundStyle(.secondary)

                        OptionRow(
                            title: "Hide CrossOver Steam from the Dock in background",
                            detail: "Suppresses the managed background session and removes an existing CrossOver Dock pin while enabled. If you open CrossOver yourself, it can appear in the Dock normally. Turning this off or uninstalling restores a pin Kaon removed.",
                            isOn: $viewModel.configuration.hideDockWhenBackgrounded
                        )
                        Divider()
                        OptionRow(
                            title: "Hide Windows tray icons from the macOS menu bar",
                            detail: "Applies a guarded, bottle-local Explorer override. This hides every Windows tray icon in the selected bottle, never modifies CrossOver.app, and safely falls back after an unsupported CrossOver update.",
                            isOn: $viewModel.configuration.hideWindowsTrayIcons
                        )

                        if viewModel.configuration.hideDockWhenBackgrounded
                            || viewModel.configuration.hideWindowsTrayIcons {
                            Label(
                                "Repair mode keeps these optional changes current and rolls back unsafe or unknown Explorer patches.",
                                systemImage: "shield.lefthalf.filled"
                            )
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .padding(10)
                            .background(.blue.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                        }
                    }
                }

                HStack {
                    if !viewModel.validationErrors(for: .install).isEmpty {
                        Text("You can review now; Install stays unavailable until the orange items are fixed.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Review Setup") { continueAction() }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                }
            }
            .padding(28)
            .frame(maxWidth: 760)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("Setup")
    }
}

private struct ReviewInstallView: View {
    @ObservedObject var viewModel: InstallerViewModel

    private var errors: [String] { viewModel.validationErrors(for: .install) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                PageHeader(
                    title: "Review & Install",
                    subtitle: "Kaon installs only in your user account. Maintenance can remove the active integration while preserving games and recovery files.",
                    symbol: "checklist"
                )

                SettingsCard(title: "Installation summary", symbol: "doc.text.magnifyingglass") {
                    SummaryRow(label: "CrossOver edition", value: viewModel.configuration.crossOverEdition.displayName)
                    Divider()
                    SummaryRow(
                        label: "Application",
                        value: viewModel.configuration.crossOverApplicationURL?.path ?? "Not selected"
                    )
                    Divider()
                    SummaryRow(label: "Steam bottle", value: viewModel.configuration.normalizedBottleName)
                    Divider()
                    SummaryRow(
                        label: "Automatic repair",
                        value: viewModel.configuration.automaticRepair ? "Enabled" : "Disabled"
                    )
                    Divider()
                    SummaryRow(
                        label: "Background Steam at login",
                        value: viewModel.configuration.startAtLogin ? "Enabled" : "Disabled"
                    )
                    Divider()
                    SummaryRow(
                        label: "Hide Dock in background",
                        value: viewModel.configuration.hideDockWhenBackgrounded ? "Enabled" : "Disabled"
                    )
                    Divider()
                    SummaryRow(
                        label: "Hide Windows tray icons",
                        value: viewModel.configuration.hideWindowsTrayIcons ? "Enabled for this bottle" : "Disabled"
                    )
                }

                if !errors.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Label("Fix these items before installing", systemImage: "exclamationmark.triangle.fill")
                            .fontWeight(.semibold)
                        ForEach(errors, id: \.self) { error in
                            Text("• \(error)")
                        }
                    }
                    .foregroundStyle(.orange)
                    .padding(14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("What Install does")
                        .font(.headline)
                    Text("It links the selected CrossOver Steam library to native Steam, installs Kaon’s launch helpers, updates Steam launch metadata safely, and enables only the automations selected above. Existing game files are reused, not copied.")
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("After setup, always choose “Shared CrossOver … Library” in Steam’s Install dialog. New games in that library are added to Kaon automatically.")
                        .font(.callout.weight(.semibold))
                        .fixedSize(horizontal: false, vertical: true)
                }

                if viewModel.installationCompleted {
                    SettingsCard(title: "You're ready to install a PC game", symbol: "play.circle.fill") {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("1. Open native Steam.")
                            Text("2. Click Install on a Windows game and choose “Shared CrossOver … Library.”")
                            if viewModel.configuration.automaticRepair {
                                Text("3. After a newly downloaded game finishes, choose Steam → Quit Steam. Wait up to one minute, then reopen Steam.")
                            } else {
                                Text("3. After a newly downloaded game finishes, quit Steam, open Maintenance, click Repair Kaon, then reopen Steam.")
                            }
                            Text("4. Click Play and choose “Play through … (Kaon).”")
                            Text(viewModel.configuration.automaticRepair
                                 ? "You only need the quit-and-reopen step after installing a new game or when Kaon says a repair is waiting."
                                 : "Because automatic repair is off, run Repair Kaon after every newly installed game.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            HStack {
                                Spacer()
                                Button("Open Native Steam") { viewModel.openNativeSteam() }
                                    .buttonStyle(.borderedProminent)
                            }
                        }
                    }
                }

                HStack {
                    Spacer()
                    Button {
                        viewModel.run(.install)
                    } label: {
                        if case .running(.install) = viewModel.operationState {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Label("Install Kaon", systemImage: "arrow.down.circle.fill")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(!errors.isEmpty || viewModel.isRunning)
                }
            }
            .padding(28)
            .frame(maxWidth: 760)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("Review & Install")
    }
}

private final class MaintenanceViewState: ObservableObject {
    @Published var confirmingUninstall = false
}

private struct MaintenanceView: View {
    @ObservedObject var viewModel: InstallerViewModel
    @StateObject private var viewState = MaintenanceViewState()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                PageHeader(
                    title: "Maintenance",
                    subtitle: "Check, repair, or remove the active user-level Kaon integration.",
                    symbol: "wrench.and.screwdriver.fill"
                )

                SettingsCard(title: "Status", symbol: "waveform.path.ecg") {
                    Text("Check the installed engine, Steam library link, launch metadata, background agent, and optional visibility changes.")
                        .foregroundStyle(.secondary)
                    HStack {
                        Spacer()
                        Button("Stop Configured Windows Steam") { viewModel.run(.stopSteam) }
                            .disabled(viewModel.isRunning)
                        Button("Start Configured Windows Steam") { viewModel.run(.startSteam) }
                            .disabled(viewModel.isRunning)
                        Button("Check Status") { viewModel.run(.status) }
                            .disabled(viewModel.isRunning)
                    }
                }

                SettingsCard(title: "Repair", symbol: "cross.case") {
                    Text("Reinstalls missing helpers and restores Kaon launch options using the choices on the Setup page. Steam is never edited while native Steam is actively writing its metadata.")
                        .foregroundStyle(.secondary)
                    HStack {
                        Spacer()
                        Button("Repair Kaon") { viewModel.run(.repair) }
                            .buttonStyle(.borderedProminent)
                            .disabled(!viewModel.validationErrors(for: .repair).isEmpty || viewModel.isRunning)
                    }
                }

                SettingsCard(title: "Uninstall", symbol: "trash") {
                    Text("Removes active LaunchAgents and Kaon launch entries, then restores its Steam, Dock, and bottle-local visibility changes. Games, shared-library entries, backups, and recovery support files are preserved.")
                        .foregroundStyle(.secondary)
                    HStack {
                        Spacer()
                        Button("Uninstall Kaon", role: .destructive) {
                            viewState.confirmingUninstall = true
                        }
                        .disabled(viewModel.isRunning)
                    }
                }
            }
            .padding(28)
            .frame(maxWidth: 760)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("Maintenance")
        .confirmationDialog(
            "Uninstall Kaon?",
            isPresented: $viewState.confirmingUninstall,
            titleVisibility: .visible
        ) {
            Button("Uninstall Kaon", role: .destructive) { viewModel.run(.uninstall) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This removes Kaon’s active automation and restores its bottle-local changes. Installed games, the shared library, recovery backups, and the CrossOver bottle stay in place.")
        }
    }
}

private struct SettingsCard<Content: View>: View {
    let title: String
    let symbol: String
    @ViewBuilder let content: Content

    init(title: String, symbol: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.symbol = symbol
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label(title, systemImage: symbol)
                .font(.headline)
            content
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(.quaternary, lineWidth: 1)
        )
    }
}

private struct OptionRow: View {
    let title: String
    let detail: String
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .fontWeight(.medium)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .toggleStyle(.switch)
    }
}

private struct PrerequisiteRow: View {
    let ready: Bool
    let readyText: String
    let missingText: String

    var body: some View {
        Label(ready ? readyText : missingText, systemImage: ready ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
            .foregroundStyle(ready ? .green : .orange)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct SummaryRow: View {
    let label: String
    let value: String

    var body: some View {
        LabeledContent(label) {
            Text(value)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
    }
}

private final class OperationPanelState: ObservableObject {
    @Published var showingDetails = false
}

private struct OperationPanel: View {
    @ObservedObject var viewModel: InstallerViewModel
    @StateObject private var viewState = OperationPanelState()

    private var presentation: (symbol: String, color: Color, title: String, detail: String) {
        switch viewModel.operationState {
        case .idle:
            return ("circle", .secondary, "", "")
        case let .running(action):
            return ("clock.arrow.circlepath", .accentColor, "\(action.displayName)…", "Please leave this window open.")
        case let .succeeded(_, message):
            return ("checkmark.circle.fill", .green, "Finished", message)
        case let .warning(_, message):
            return ("exclamationmark.triangle.fill", .orange, "Finished with a warning", message)
        case let .failed(_, message):
            return ("xmark.octagon.fill", .red, "Action needed", message)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                if viewModel.isRunning {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Image(systemName: presentation.symbol)
                        .foregroundStyle(presentation.color)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(presentation.title)
                        .fontWeight(.semibold)
                    Text(presentation.detail)
                        .font(.caption)
                        .lineLimit(2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if !viewModel.operationLog.isEmpty {
                    Button(viewState.showingDetails ? "Hide Details" : "Details") {
                        withAnimation { viewState.showingDetails.toggle() }
                    }
                    .buttonStyle(.plain)
                }
                if !viewModel.isRunning {
                    Button {
                        viewModel.clearOperation()
                    } label: {
                        Image(systemName: "xmark")
                    }
                    .buttonStyle(.plain)
                    .help("Dismiss")
                }
            }

            if viewState.showingDetails, !viewModel.operationLog.isEmpty {
                ScrollView([.horizontal, .vertical]) {
                    Text(viewModel.operationLog)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 150)
                .padding(10)
                .background(.black.opacity(0.06), in: RoundedRectangle(cornerRadius: 7))
            }
        }
        .padding(14)
        .background(.thickMaterial, in: RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(presentation.color.opacity(0.35), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.12), radius: 12, y: 4)
    }
}
