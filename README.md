# Kaon for macOS — automated Steam + CrossOver setup

[![macOS CI](https://github.com/enjihn/kaon/actions/workflows/ci.yml/badge.svg)](https://github.com/enjihn/kaon/actions/workflows/ci.yml)

Kaon lets the native macOS Steam client install, update, and launch Windows
Steam games through an existing CrossOver installation. This fork turns the
original proof of concept into a guided macOS app with repair, background
startup, rollback, and uninstall support.

> **Fork notice**
>
> This is an independent fork of [natbro/kaon](https://github.com/natbro/kaon).
> Nathaniel Brothman created the original research, configuration, launch
> scripts, and documentation that proved this approach could work. The native
> installer and automation in this fork are maintained separately and are not
> endorsed or supported by the upstream author.

## Quick start

You need:

- macOS 13 or newer on an Apple Silicon Mac (M1 or newer);
- the native [Steam for macOS](https://store.steampowered.com/about/) client,
  launched at least once;
- CrossOver or CrossOver Preview already installed; and
- Windows Steam installed inside a CrossOver bottle, normally named `Steam`.

### Before you run Kaon

If those prerequisites are unfamiliar, use this exact order:

1. Install [native Steam for Mac](https://store.steampowered.com/about/), sign
   in, wait until the Library appears, then choose **Steam → Quit Steam**.
2. Install [CrossOver](https://www.codeweavers.com/crossover) or CrossOver
   Preview and open it.
3. In CrossOver, choose **Install a Windows Application**, select **Steam**,
   and install it in a bottle named `Steam`. Let Windows Steam update and sign
   in once.
4. Quit native Steam before running Kaon Setup. Windows Steam may remain open,
   although the optional tray-hiding feature waits until that bottle is idle.

If you do not already have access to CrossOver Preview, use ordinary CrossOver;
Kaon does not require Preview.

Kaon Setup automatically finds ordinary `Steam` bottles and shows a checklist.
If a requirement is missing, its Install button stays disabled and the app
offers **Open Selected CrossOver**, **Open or Get Native Steam**, and
**Refresh Checks** buttons.

Then:

1. Open [Releases](https://github.com/enjihn/kaon/releases), expand **Assets**,
   and download the signed `Kaon-Setup-<version>.dmg`. Do not use a source-code
   ZIP or an Actions artifact. Stop if the release notes do not say the build
   is **Developer ID signed and Apple-notarized**.
2. Open the DMG, drag **Kaon Setup.app** to **Applications**, then open it.
   Public tagged releases are Developer-ID signed and Apple-notarized; the
   release notes state the verified status.
3. Choose **CrossOver**, **CrossOver Preview**, or a custom CrossOver app.
4. Select the bottle containing Windows Steam.
5. Review the optional background and interface-hiding choices, then click
   **Install Kaon**.
6. Reopen native Steam. Windows games can now be installed from its normal
   Library interface. **In Steam's install dialog, choose the library labeled
   `Shared CrossOver … Library`**, not the native default library.
7. After a newly downloaded game finishes, choose **Steam → Quit Steam**. If
   Automatic repair is on, wait up to one minute and reopen Steam. If it is
   off, open **Kaon Setup → Maintenance**, click **Repair Kaon**, then reopen
   Steam.
8. Click Play and choose **Play through … (Kaon)**. Every future Windows game
   installed into that shared library is discovered by Automatic repair or the
   manual Repair button; you do not need to edit each game by hand.

Kaon is currently beta software. CI and manually triggered development
artifacts may be ad-hoc signed and require Control-click → **Open**, but the
release workflow refuses to publish a tagged public release unless the Apple
Silicon build is Developer-ID signed and Apple-notarized.

## What Kaon changes

Kaon performs the same core integration as upstream, but automatically and
with verified backups:

1. It adds `@sSteamCmdForcePlatformType windows` to native Steam's
   `steam_dev.cfg`. Native Steam then shows Windows Install and Play controls.
2. It registers the Windows Steam folder inside the selected CrossOver bottle
   as a shared native Steam library. Windows games must be installed into this
   labeled shared library so both Steam clients see the same files.
3. It installs small launch scripts into that shared library.
4. It adds one clearly labeled Kaon launch option to every installed game in
   the shared library.
5. If enabled, a per-user background agent safely restores those managed
   changes after Steam updates its local metadata.
6. If enabled, another per-user agent starts Windows Steam silently at login.

The native and Windows Steam clients refer to the same game files; Kaon does
not duplicate the games. Windows Steam still needs to run for ownership checks,
Steamworks, friends, achievements, and games that require the Steam client.

### Important Steam tradeoff

The platform override applies to the **whole native Steam client**. Games with
both Mac and Windows depots will be treated as Windows games while it is
enabled. Kaon is best used when native Steam is serving primarily as the UI for
the CrossOver Windows library. Read [Limitations and safety](#limitations-and-safety)
before using a mixed Mac/Windows game library.

The upstream research also found that the presence of `steam_dev.cfg` can make
native Steam skip its initial bootstrap updater. Most client updates still
arrive normally, but if Steam appears stuck on an old client build, use the
careful maintenance procedure below rather than deleting the file while Steam
is open.

### Temporarily allow a native Steam bootstrap update

This is an advanced recovery procedure because starting without the Windows
override can make native Steam reconsider installed depots.

1. Quit both Steam clients and every game.
2. Disable Kaon's repair agent with
   `~/Library/Application\ Support/Kaon/bin/kaon-setup repair --no-auto-repair --yes`.
3. Move—not delete—`steam_dev.cfg` out of
   `~/Library/Application Support/Steam/Steam.AppBundle/Steam/Contents/MacOS/`.
4. Start native Steam, allow its client bootstrap to finish, then quit Steam
   again before installing, updating, or launching a game.
5. Put `steam_dev.cfg` back and run
   `~/Library/Application\ Support/Kaon/bin/kaon-setup repair --auto-repair --yes`
   before reopening Steam.

Kaon's backups remain available, but this procedure cannot prevent Steam from
changing depots while it is temporarily in macOS mode. Avoid it unless the
client updater is genuinely stuck.

## Installer choices

| Choice | What it does |
| --- | --- |
| **CrossOver** | Uses a normal `CrossOver.app` installation. |
| **CrossOver Preview** | Uses `CrossOver Preview.app`; the exact selected path is saved, so stable and Preview can coexist. |
| **Custom** | Uses a renamed or nonstandard CrossOver app you select. |
| **Automatic repair** | Watches Steam metadata at low frequency and restores only Kaon-owned entries after native Steam is closed. Enabled by default. |
| **Start Windows Steam at login** | Launches the selected bottle's Steam directly and silently immediately after setup and once after future logins. It does not continuously relaunch Steam if you deliberately quit it. |
| **Hide CrossOver from the background Dock** | Uses the direct background launcher and optionally removes the selected CrossOver app's pinned Dock tile. A tile removed by Kaon is recorded and can be restored. CrossOver appears normally when you open it yourself. |
| **Hide Windows tray icons** | Experimental. Hides every Windows tray icon in the selected bottle, not only Steam. Off by default. |

Recommended for most people: choose the CrossOver edition that contains the
Steam bottle, leave **Automatic repair** and **Start Windows Steam at login**
on, and leave both hiding options off until a game launches successfully.
Startup keeps the Windows Steam window closed; Dock and menu-bar visibility are
controlled separately by the two hiding options.

macOS may show a **Background Items Added** notification after setup. That is
expected for the repair and startup agents selected above. Kaon's Uninstall
removes those agents; disabling them in System Settings disables the matching
feature.

## Optional Dock and menu-bar hiding

The two hiding options are independent.

Background Dock hiding launches Wine directly instead of using CrossOver's
generated foreground app wrapper. If the selected CrossOver edition is pinned,
Kaon can remove that exact path from the Dock without changing other tiles. It
never changes CrossOver's `Info.plist`, and manually opening CrossOver still
shows it normally.

Windows tray hiding is necessarily more advanced. CrossOver's Explorer process
bridges Windows tray icons into the macOS menu bar. Kaon:

- reads Explorer from the user's own licensed CrossOver installation;
- validates one known stable or Preview instruction pattern;
- derives a six-byte bottle-local patch;
- installs it only into the selected bottle; and
- records hashes so it can repair or roll back only its own changes.

Kaon never modifies or redistributes `CrossOver.app`. An unknown CrossOver
update fails safely: games remain usable, the menu-bar icon may return, and
Kaon reports that tray hiding is degraded instead of guessing at a patch.

## Maintenance

Open **Kaon Setup.app → Maintenance** at any time to:

- check the selected app, bottle, Steam library link, launch options, and
  background agents;
- repair the complete managed configuration;
- start or stop the selected bottle's Windows Steam; or
- uninstall the active Kaon integration.

Native Steam must be completely quit before install, repair, or uninstall.
Kaon intentionally defers rather than rewriting Steam's binary metadata while
Steam or Steam Metadata Editor is using it.

Uninstall removes Kaon's agents and exact owned launch entries, restores a
Kaon-patched bottle Explorer, and restores a Dock tile that Kaon removed. It
also removes Kaon's Windows-platform override. It preserves downloaded games,
the shared-library registration and data, content-addressed backups, and
recovery support by default so uninstall cannot erase a game library.

## Command line

The native app calls the same auditable setup engine exposed by the source
tree:

```zsh
# Preview the detected installation and current health
macos/bin/kaon-setup status --json

# Install with CrossOver Preview and the default Steam bottle
macos/bin/kaon-setup install \
  --crossover-edition preview \
  --bottle Steam \
  --auto-repair \
  --start-at-login

# Opt into both interface-hiding features
macos/bin/kaon-setup repair --hide-dock --hide-tray --yes

# Repair, start/stop the Windows client, or uninstall
macos/bin/kaon-setup repair --yes
macos/bin/kaon-setup start-steam
macos/bin/kaon-setup stop-steam
macos/bin/kaon-setup uninstall --yes
```

Running from source requires Python 3. The downloadable app contains its own
setup runtime and does not require Homebrew, Python, Xcode, or administrator
access.

## Files installed in your account

Kaon is a user-level installation and does not use `sudo`.

```text
~/Library/Application Support/Kaon/
  bin/kaon-setup                 installed maintenance engine
  config.json                    selected CrossOver app, bottle, and options
  state/                         ownership and rollback records
  backups/                       content-addressed safety copies

~/Library/LaunchAgents/
  io.github.enjihn.kaon.autoheal.plist
  io.github.enjihn.kaon.crossover-steam.plist
  io.github.enjihn.kaon.crossover-tray-guard.plist   # only when enabled

~/Library/Logs/Kaon/             bounded or low-volume diagnostic logs
```

The game launchers live at
`<CrossOver Steam>/steamapps/common/Kaon/`. Logs never dump the user's full
environment or authentication data.

## Automatic repair and data safety

Steam periodically replaces `appcache/appinfo.vdf`, which can remove local
launch options. Kaon's repair engine is conservative:

- it uses a per-user lock and waits for native Steam and the metadata editor to
  close;
- it confirms the source file is stable before working;
- it stages changes beside the original, reparses the result, and verifies app
  counts and every non-Kaon section;
- it backs up by content hash and installs with an atomic rename; and
- it owns only the exact launch entries recorded in Kaon's state. A user-edited
  or diverged entry is reported and left untouched during uninstall.

The agent combines file watching with a 60-second fallback. It does no work
when signatures have not changed and never modifies metadata while Steam is
active.

## Troubleshooting

### “Steam must be running”

The game is talking to Windows Steam, not only native Steam. In Kaon Setup,
open **Maintenance** and choose **Start Windows Steam**, or enable startup at
login. You can still launch Windows Steam normally through CrossOver when you
want its full UI.

### A game says “OS Error 0” or its Kaon option disappeared

Quit native Steam, open **Maintenance**, and choose **Repair Kaon**. Automatic
repair normally handles this after Steam closes; the status result will explain
if it was deferred or encountered an unknown metadata version.

### A newly installed game has no Kaon launch option

Confirm that you selected `Shared CrossOver … Library` in Steam's install
dialog. Games placed in native Steam's default library are intentionally not
managed because Windows Steam cannot see those files. If the game is already
in the shared library, quit native Steam and run Repair; Kaon discovers it from
its `appmanifest_*.acf` file automatically.

### The Shared CrossOver library is missing

Do not create a replacement library or reinstall any games. Quit native Steam,
open **Kaon Setup → Maintenance**, click **Repair Kaon**, and reopen Steam. If
the library is still absent, use **Check Status** and include its result when
reporting the problem.

### Kaon Setup says installation failed

Install and Repair are transactional. Kaon restores only unchanged bytes it
just wrote and never deletes the CrossOver bottle or games. Quit Steam, reopen
Maintenance, and retry or copy the displayed error. If the message names a
recovery snapshot, keep that folder: it means another process changed a file
during rollback, so Kaon deliberately left the newer file untouched.

### CrossOver Preview was selected but stable CrossOver starts

Open Setup and confirm the displayed application path. Stable and Preview can
share a bundle identifier, so Kaon saves and launches the exact `.app` path
rather than selecting by bundle identifier.

### The menu-bar icon returned after a CrossOver update

Run Repair. If the updated Explorer pattern is unknown, Kaon intentionally
leaves the vendor copy unmodified and reports degraded tray hiding. Do not copy
an Explorer binary from a different CrossOver version.

### Logs and status

```zsh
~/Library/Application\ Support/Kaon/bin/kaon-setup status --json
open ~/Library/Logs/Kaon
```

When reporting an issue, include the JSON status and relevant Kaon log lines,
but do not upload your whole Steam or CrossOver bottle.

## Limitations and safety

- This remains an integration around undocumented Steam metadata behavior; a
  Steam update can require a Kaon update.
- CrossOver and CrossOver Preview compatibility must be tested per release.
  Tray hiding supports only explicitly recognized Explorer patterns.
- Native Steam's Windows override is global. Keep backups of important native
  Mac installs and do not casually remove the override while Steam is running.
- Some anti-cheat, kernel-driver, launcher, DRM, graphics, or architecture
  requirements are incompatible with Wine/CrossOver regardless of Kaon.
- Background-item notifications and controls are owned by macOS and cannot be
  suppressed by Kaon.
- Manually triggered development artifacts are ad-hoc signed and may trigger
  Gatekeeper friction. Tagged public releases are blocked unless notarized.

## Building and contributing

The repository intentionally keeps the installer separate from the inherited
research tree:

```text
macos/
  installer/       SwiftUI native setup and maintenance app
  engine/          setup orchestration and safe appinfo reconciliation
  bin/             source-tree command-line entrypoint
  resources/       installed game launchers
  scripts/         release builder and policy audit
  tests/           Python safety and format tests
.github/workflows/ macOS CI and release packaging
docs/              packaging and third-party licensing details
```

Run the local checks with:

```zsh
python3 -m unittest discover -s macos/tests -p 'test_*.py' -v
swift test --package-path macos/installer
zsh -n macos/bin/kaon-setup macos/resources/*.sh
```

Build a release with `macos/scripts/build-release.sh`. See
[docs/PACKAGING.md](docs/PACKAGING.md) for signing, notarization,
artifact policy, and release secrets.

## Licensing and redistribution

The original Kaon work and this repository's general setup code retain the
top-level [Apache License 2.0](LICENSE). The AppInfo codec inherited from
Steam Metadata Editor, and Kaon's tightly coupled repair module, are distributed
under GPLv3-or-later; see
[third-party notices](docs/THIRD_PARTY_NOTICES.md) and
[`steammetadataeditor/LICENSE`](steammetadataeditor/LICENSE).

Release artifacts are assembled from an explicit allowlist. They do **not**
contain CrossOver, Steam, a bottle, Windows executables, or the inherited
`lsteamclient`/Steamworks research tree. Users must obtain CrossOver and Steam
from their respective vendors. The optional Explorer change is derived locally
from the user's installation and is never shipped.

## Credits

- [natbro/kaon](https://github.com/natbro/kaon) — original concept, research,
  launcher integration, and detailed Steam/CrossOver documentation.
- [tralph3/Steam-Metadata-Editor](https://github.com/tralph3/Steam-Metadata-Editor)
  — AppInfo parsing and writing foundation used under GPLv3.
- CodeWeavers, Valve, Wine, and the many translation-layer contributors whose
  work makes Windows gaming on macOS possible.

For the upstream project's original manual guide and research discussion, see
the [upstream README](https://github.com/natbro/kaon/blob/main/README.md).
