import Foundation
@testable import KaonInstallerCore
import XCTest

final class InstallerConfigurationTests: XCTestCase {
    func testCommandArgumentsMatchSetupContract() {
        let appURL = URL(fileURLWithPath: "/Applications/CrossOver Preview.app")
        let configuration = InstallerConfiguration(
            crossOverEdition: .preview,
            crossOverApplicationURL: appURL,
            bottleName: "  Steam Preview  ",
            automaticRepair: true,
            startAtLogin: false,
            hideDockWhenBackgrounded: true,
            hideWindowsTrayIcons: false
        )

        XCTAssertEqual(
            configuration.commandArguments(for: .repair),
            [
                "repair",
                "--crossover-edition", "preview",
                "--crossover-app", "/Applications/CrossOver Preview.app",
                "--bottle", "Steam Preview",
                "--auto-repair",
                "--no-start-at-login",
                "--hide-dock",
                "--no-hide-tray",
                "--yes",
                "--json"
            ]
        )
    }

    func testInstallValidationRequiresApplication() {
        let configuration = InstallerConfiguration(crossOverApplicationURL: nil)

        XCTAssertTrue(configuration.validationErrors(for: .install).contains {
            $0.contains("Select an installed CrossOver")
        })
        XCTAssertFalse(configuration.validationErrors(for: .status).contains {
            $0.contains("Select an installed CrossOver")
        })
        XCTAssertEqual(
            configuration.commandArguments(for: .startSteam),
            ["start-steam", "--yes", "--json"]
        )
    }

    func testDisplayNamesMatchSelectedCrossOverApplication() {
        let preview = InstallerConfiguration(crossOverEdition: .preview)
        XCTAssertEqual(preview.sharedLibraryDisplayName, "Shared CrossOver Preview Library")
        XCTAssertEqual(preview.kaonLaunchOptionDisplayName, "Play through CrossOver Preview (Kaon)")

        let custom = InstallerConfiguration(
            crossOverEdition: .custom,
            crossOverApplicationURL: URL(fileURLWithPath: "/Applications/CrossOver Nightly.app")
        )
        XCTAssertEqual(custom.sharedLibraryDisplayName, "Shared CrossOver Nightly Library")
        XCTAssertEqual(custom.kaonLaunchOptionDisplayName, "Play through CrossOver Nightly (Kaon)")
    }

    func testBottleNamesRejectTraversalAndLineBreaks() {
        for invalidName in ["../Steam", "Steam\nOther"] {
            let configuration = InstallerConfiguration(bottleName: invalidName)
            XCTAssertTrue(
                configuration.validationErrors(for: .install).contains {
                    $0.contains("slashes or line breaks")
                },
                "Expected bottle name to be rejected: \(String(reflecting: invalidName))"
            )
        }
    }

    func testBottleNamesRejectExactPathComponents() {
        for invalidName in [".", "..", "  ..  "] {
            let configuration = InstallerConfiguration(bottleName: invalidName)
            XCTAssertTrue(
                configuration.validationErrors(for: .install).contains {
                    $0.contains("cannot be “.” or “..”")
                },
                "Expected bottle name to be rejected: \(String(reflecting: invalidName))"
            )
        }

        let ordinaryDots = InstallerConfiguration(bottleName: "Steam...Preview")
        XCTAssertFalse(
            ordinaryDots.validationErrors(for: .install).contains {
                $0.contains("cannot be “.” or “..”")
            }
        )
    }
}
