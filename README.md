# Kaon Setup — automated Steam + CrossOver integration

[![macOS CI](https://github.com/enjihn/kaon/actions/workflows/ci.yml/badge.svg)](https://github.com/enjihn/kaon/actions/workflows/ci.yml)

Kaon Setup lets the regular Steam app for Mac install, update, and launch
compatible Windows Steam games through an existing CrossOver installation.
This fork turns the original proof of concept into a guided macOS app with
automatic repair, background startup, guarded rollback after failed changes,
and uninstall support.

Kaon does not include CrossOver, Steam, or any games, and it does not make every
Windows game compatible with CrossOver. It connects software that is already
installed on your Mac.

> **Fork notice**
>
> This is an independent fork of [natbro/kaon](https://github.com/natbro/kaon).
> Nathaniel Brothman created the original research, configuration, launch
> scripts, and documentation that proved this approach could work. The native
> installer and automation in this fork are maintained separately and are not
> endorsed or supported by the upstream author.

**Jump to:** [Quick start](#quick-start) ·
[Install and play a game](#install-and-play-a-game) ·
[Options](#installer-choices) · [Maintenance](#maintenance-and-uninstall) ·
[Troubleshooting](#troubleshooting) · [Limitations](#limitations-and-safety)

## Quick start

### Three names used in this guide

| Name | Meaning |
| --- | --- |
| **Mac Steam** | The normal Steam app for macOS. This is the interface you use to browse, install, update, and start games. |
| **Windows Steam** | A separate Windows copy of Steam installed inside CrossOver. It normally stays in the background while you play. |
| **Bottle** | CrossOver's container for Windows apps. Windows Steam and the games launched by Kaon use the same bottle. |

### Requirements

You need:

- macOS 13 or newer on an Apple Silicon Mac (M1 or newer);
- Mac Steam, launched at least once so its Library has finished loading;
- a genuine CodeWeavers CrossOver or CrossOver Preview installation; and
- Windows Steam installed in that CrossOver bottle's default location, normally
  in a bottle named `Steam`.

Use the same Steam account in both clients to avoid ownership and Steamworks
mismatches. Kaon currently supports bottles in CrossOver's standard per-user
`Bottles` folder; relocated or symbolic-link bottle folders and secondary
Windows Steam libraries are not supported.

> **Important Steam tradeoff**
>
> Kaon makes the **whole Mac Steam client behave as a Windows client**. Games
> offering both Mac and Windows versions will use the Windows version, while
> Mac-only games may stop receiving updates until the override is removed. If
> you actively use native Mac games, read [Important Steam
> tradeoff](#important-steam-tradeoff) before installing.

Game compatibility remains CrossOver-dependent. Anti-cheat, DRM, launchers,
graphics requirements, or Windows drivers can prevent a game from working even
when Kaon itself is configured correctly.

### Prepare both Steam clients

If the requirements above are unfamiliar, use this order:

1. Install [Steam for Mac](https://store.steampowered.com/about/), sign in, wait
   until the Library appears, then choose **Steam → Quit Steam**.
2. Install [CrossOver](https://www.codeweavers.com/crossover) or CrossOver
   Preview and open it. Kaon does not require Preview.
3. In CrossOver, choose **Install a Windows Application**, select **Steam**, and
   install it in a bottle named `Steam`. Keep Steam's default installation
   folder, let it update, and sign in with the same account used in Mac Steam.
4. For the simplest first setup, quit both Steam clients and any running games.

Kaon Setup finds ordinary `Steam` bottles and shows a checklist. If something
is missing, **Install Kaon** remains unavailable and the app offers **Open
Selected CrossOver**, **Open or Get Mac Steam**, and **Refresh Checks** buttons.

## Install Kaon Setup

1. Open [Releases](https://github.com/enjihn/kaon/releases) and expand the
   newest release's **Assets** section. If there is no notarized DMG, a public
   installer has not been published yet; source-code ZIPs are not installers.
2. Download `Kaon-Setup-<version>.dmg`. Public release notes must say the build
   is **Developer ID signed and Apple-notarized**. Do not use an Actions
   artifact as a public installer.
3. Open the DMG and drag **Kaon Setup.app** to **Applications**. You may instead
   use the `Applications` folder inside your home folder. Kaon's configuration
   is user-level and never uses `sudo` or a privileged helper, although Finder
   may request normal macOS authentication when copying any app into the system
   Applications folder.
4. Eject the installer disk, then open **Kaon Setup** from Applications.
5. Choose the CrossOver edition that owns the Steam bottle, then select that
   bottle. It is normally named `Steam`.
6. For the easiest setup, leave **Automatic repair** and **Start Windows Steam
   at login** enabled. Leave both interface-hiding options off until a game has
   launched successfully.
7. Click **Review Setup**, review the summary, and click **Install Kaon**.
8. Reopen Mac Steam.

Kaon Setup is volunteer-maintained beta software. Compatibility and support
timelines are not guaranteed. Detailed reports and contributions are welcome
in [GitHub Issues](https://github.com/enjihn/kaon/issues).

## Install and play a game

1. In Mac Steam, click **Install** on a compatible Windows game.
2. In Steam's install dialog, select the shared CrossOver library. Its name is
   normally **Shared CrossOver Library** or **Shared CrossOver Preview
   Library**. A custom selection uses the selected app's name. Do not choose
   Mac Steam's default library.
3. When the download finishes, click **Play** and look for **Play through
   CrossOver (Kaon)** or **Play through CrossOver Preview (Kaon)**. A custom
   selection uses the selected app's name instead.
4. If the Kaon option appears, select it and play. No restart or repair is
   needed.
5. If the option is missing, quit Mac Steam:
   - With **Automatic repair** enabled, wait about a minute and reopen Steam.
     If the option is still missing, quit Steam and run **Repair Kaon** once.
   - With **Automatic repair** disabled, open **Kaon Setup → Maintenance**,
     click **Repair Kaon**, and then reopen Steam.
6. Windows Steam must be running when a game needs Steam ownership or
   Steamworks services. The default startup option normally keeps it available
   in the background. Otherwise, use **Maintenance → Start Configured Windows
   Steam** before playing.

Kaon discovers eligible games automatically and adds their launch options the
next time it can safely repair Steam's metadata. It skips entries it cannot
modify safely instead of guessing. You never need to edit a supported game's
metadata by hand.

Most of the time, everyday use is simply: open Mac Steam, install games into
the shared CrossOver library, and choose the Kaon launch option. You do **not**
need to quit Steam after every download when that option is already present.

## What Kaon changes

Kaon automates the original integration and keeps ownership records and
backups for its changes:

1. It adds `@sSteamCmdForcePlatformType windows` to Mac Steam's
   `steam_dev.cfg`, which exposes Windows Install and Play controls.
2. It registers the Windows Steam folder inside the selected bottle as a Mac
   Steam library. Both Steam clients then refer to the same game files.
3. It installs small launch scripts into that shared library.
4. It adds one clearly labeled Kaon launch option to each eligible installed
   game with usable Windows launch metadata. Installed games are discovered
   from the shared library's `appmanifest_*.acf` files.
5. If enabled, a per-user agent repairs Kaon-owned configuration after Mac
   Steam closes.
6. If enabled, another per-user agent requests a quiet Windows Steam start
   after setup, after Repair, and at future macOS logins.

The games are not duplicated. Windows Steam still needs to run during play for
ownership checks, Steamworks, friends, achievements, and games that require the
Steam client. Steam or CrossOver may still show an updater, login prompt,
permission request, or error window when attention is required.

### Important Steam tradeoff

The Windows platform override applies to the **whole Mac Steam client**. Games
with both Mac and Windows depots will be treated as Windows games while it is
enabled. Mac-only games may remain installed but stop receiving updates. Kaon
is simplest when Mac Steam is used primarily as the interface for the shared
CrossOver library.

The upstream research also found that the presence of `steam_dev.cfg` can make
Mac Steam skip its initial bootstrap updater. Most client updates still arrive
normally. If Steam appears stuck on an old client build, use the advanced
procedure under [Mac Steam's bootstrap updater is
stuck](#mac-steams-bootstrap-updater-is-stuck); do not remove the file casually
while Steam is open.

## Installer choices

| Choice | What it does |
| --- | --- |
| **CrossOver** | Uses a normal, signed `CrossOver.app` installation. |
| **CrossOver Preview** | Uses `CrossOver Preview.app`; the exact selected path is saved so stable and Preview can coexist. |
| **Custom** | Selects a genuine, signed CodeWeavers CrossOver app that was renamed, relocated, or installed under an unusual name. It does not support unrelated Wine wrappers. |
| **Automatic repair** | Checks Kaon's platform override, shared-library link, launchers, and managed game options after Mac Steam closes. Enabled by default. |
| **Start Windows Steam at login** | Requests a quiet start after Install, after Repair, and at future logins. It does not continuously relaunch Steam after you deliberately quit it. |
| **Remove CrossOver's pinned Dock tile** | When Kaon starts Windows Steam, it launches directly in the background. This option also removes the selected CrossOver app's persistent Dock tile, if present, and records it for restoration. |
| **Hide Windows icons from the Mac menu bar** | Experimental. Hides every Windows tray icon in the selected bottle, not only Steam. Off by default. |

Recommended for most people: choose the CrossOver edition that owns the Steam
bottle, leave **Automatic repair** and **Start Windows Steam at login** on, and
leave both hiding options off until a game launches successfully.

The managed startup requests Windows Steam's quiet mode and does not use a
foreground CrossOver wrapper. The Dock choice only handles an existing pinned
CrossOver tile; the menu-bar choice handles Windows tray icons. macOS, Steam,
or CrossOver may still show a window or transient icon when attention is
required.

To change an option after installation, update it on Kaon Setup's **Setup**
page, quit Mac Steam, and click **Maintenance → Repair Kaon**. Disabling a Kaon
background item in System Settings may only stop it temporarily; Install or
Repair enables it again when its saved Kaon option is still on.

macOS may show a **Background Items Added** notification after setup. This is
expected for the repair, startup, or tray-protection agents you selected.

## Optional Dock tile and menu-bar hiding

The two hiding options are independent and are off by default.

When Kaon starts Windows Steam, its background launcher starts Wine directly
instead of opening CrossOver's foreground app wrapper. The Dock option also
removes an existing pinned tile for the exact selected CrossOver app. Kaon
records that tile and restores it when you turn the option off and run Repair,
or when you uninstall. Opening CrossOver yourself can still show it normally in
the Dock.

CrossOver's Explorer process bridges Windows tray icons into the Mac menu bar.
The experimental menu-bar option:

- reads Explorer from the user's own licensed CrossOver installation;
- accepts only a small allowlist of recognized Explorer instruction patterns;
- derives and marks a validated bottle-local copy;
- installs it only inside the selected bottle; and
- records hashes so Kaon can repair or restore only its own changes.

Kaon never modifies or redistributes `CrossOver.app`. If an updated Explorer
is not recognized, Kaon leaves the vendor file untouched and reports degraded
tray hiding instead of guessing. The menu-bar icon may return until that build
is supported.

## Maintenance and uninstall

Open **Kaon Setup.app → Maintenance** to:

- report the saved CrossOver app and bottle plus the platform override,
  shared-library links, launchers, background agents, and visibility state;
- repair the managed configuration using the choices on the Setup page;
- start or stop the configured Windows Steam; or
- uninstall the active Kaon integration.

**Check Status** and the Windows Steam start/stop buttons can be used while Mac
Steam is open. Install, Repair, and Uninstall require Mac Steam to be fully
quit. A launch-option health check is available only while Mac Steam is closed.
Kaon defers instead of rewriting Steam's binary metadata while Steam or Steam
Metadata Editor is using it.

### What Uninstall Kaon removes

Uninstall removes Kaon's agents and exact owned launch entries, removes the
Windows-platform override when Kaon added it, restores a bottle-local Explorer
changed by Kaon, and restores a Dock tile that Kaon removed. Preexisting user
configuration is left in place.

It deliberately preserves downloaded games, the CrossOver bottle, the shared
Steam library registration, backups, logs, and recovery support files. This
prevents an uninstall operation from erasing a game library or the information
needed to recover from an earlier change.

**Uninstall Kaon** removes the active integration, not **Kaon Setup.app**
itself. After a successful uninstall, move the app to Trash if you no longer
want it. Leaving the preserved support files in place is safest; remove them
manually only if you deliberately want to discard all Kaon recovery data.

## Automatic repair and data safety

Steam periodically replaces `appcache/appinfo.vdf`, which can remove local
launch options. Kaon's repair engine is conservative:

- it uses a per-user lock and defers while Mac Steam or Steam Metadata Editor
  is open;
- it confirms the source file is stable before working;
- it stages changes beside the original, reparses the result, and verifies app
  counts and every non-Kaon section;
- it backs up by content hash and installs with an atomic rename; and
- it owns only the exact launch entries recorded in Kaon's state. A user-edited
  or diverged entry is reported and left untouched during uninstall.

The agent combines file watching with a roughly 60-second retry interval. That
interval is not a guaranteed deadline: macOS scheduling, Steam still exiting,
or another metadata tool can defer a repair. It makes no changes when the
watched signatures have not changed and never modifies metadata while Mac
Steam is active.

## Troubleshooting

### “Steam must be running”

The game is talking to Windows Steam, not only Mac Steam. In Kaon Setup, open
**Maintenance** and choose **Start Configured Windows Steam**, or enable
startup at login. You can still open Windows Steam normally through CrossOver
when you want its full interface.

### A game says “OS Error 0” or its Kaon option disappeared

Quit Mac Steam, open **Maintenance**, and choose **Repair Kaon**. Automatic
repair normally handles this after Steam closes. If manual Repair is deferred,
the displayed error explains what still needs to close.

### A newly installed game has no Kaon launch option

Confirm that you selected **Shared CrossOver Library** or **Shared CrossOver
Preview Library** in Steam's install dialog. Games placed in Mac Steam's
default library are intentionally not managed because Windows Steam cannot see
those files.

If the game is already in the shared library, quit Mac Steam and run **Repair
Kaon**. Kaon discovers it from its `appmanifest_*.acf` file. **Details** shows
how many games were managed or skipped. A skipped game may lack a usable
Windows launch menu or may not yet be present in Steam's metadata cache.

### The shared CrossOver library is missing

Do not create a replacement library or reinstall any games. Quit Mac Steam,
open **Kaon Setup → Maintenance**, click **Repair Kaon**, and reopen Steam. If
the library is still absent, use **Check Status** and include the result when
reporting the problem.

### Kaon Setup says installation or repair failed

Quit Mac Steam, return to Maintenance, and try again or copy the displayed
error. Install and Repair stage and validate their changes before replacing
live files. If another process changes a file during rollback, Kaon leaves the
newer content untouched rather than overwriting it.

If the error names a recovery snapshot, keep that folder. It means rollback
could not be completed safely; include the complete error when reporting the
problem.

### CrossOver Preview was selected but stable CrossOver starts

Open Setup and confirm the displayed application path. Stable and Preview can
share a bundle identifier, so Kaon saves and launches the exact `.app` path
instead of selecting only by bundle identifier.

### The menu-bar icon returned after a CrossOver update

In Maintenance, stop the configured Windows Steam and close every other app in
that bottle. Quit Mac Steam, run **Repair Kaon**, and then start Windows Steam
again. If the updated Explorer pattern is unknown, Kaon leaves the vendor copy
unmodified and reports degraded tray hiding. Do not copy an Explorer binary
from a different CrossOver version.

### Mac Steam's bootstrap updater is stuck

This is an advanced recovery procedure. Starting Mac Steam without Kaon's
Windows override can make Steam reconsider installed depots, so do not use it
for routine updates.

1. On Kaon Setup's **Setup** page, write down whether **Automatic repair** and
   **Start Windows Steam at login** are currently on or off.
2. Quit both Steam clients, every game, and Kaon Setup.
3. In Terminal, paste this entire block. It stops at the first error, refuses to
   overwrite an earlier hold file, disables both agents, stops the configured
   Windows Steam, and temporarily moves the override:

   ```zsh
   /bin/zsh <<'KAON_BOOTSTRAP'
   set -eu
   runtime="$HOME/Library/Application Support/Kaon/bin/kaon-setup"
   override="$HOME/Library/Application Support/Steam/Steam.AppBundle/Steam/Contents/MacOS/steam_dev.cfg"
   hold="$HOME/Library/Application Support/Kaon/steam_dev.cfg.bootstrap-hold"

   if [[ -e "$hold" || -L "$hold" ]]; then
     print -u2 -- "Stop: an earlier bootstrap hold file already exists: $hold"
     exit 1
   fi
   "$runtime" repair --no-auto-repair --no-start-at-login --yes
   "$runtime" stop-steam
   mv "$override" "$hold"
   KAON_BOOTSTRAP
   ```

4. Open Mac Steam, let its client bootstrap finish, and quit it again **without
   installing, updating, or launching a game**.
5. Paste this complete block to restore the override. It refuses to overwrite a
   file if Steam recreated one unexpectedly:

   ```zsh
   /bin/zsh <<'KAON_RESTORE'
   set -eu
   override="$HOME/Library/Application Support/Steam/Steam.AppBundle/Steam/Contents/MacOS/steam_dev.cfg"
   hold="$HOME/Library/Application Support/Kaon/steam_dev.cfg.bootstrap-hold"

   if [[ -e "$override" || -L "$override" ]]; then
     print -u2 -- "Stop: Steam recreated the override path; nothing was overwritten: $override"
     exit 1
   fi
   mv "$hold" "$override"
   KAON_RESTORE
   ```

6. Reopen Kaon Setup, restore the two choices you recorded in step 1, keep Mac
   Steam closed, and click **Maintenance → Repair Kaon** before reopening Mac
   Steam.

Stop and request help if any command reports an error. Do not continue with a
missing override file.

### Logs, status, and issue reports

Start with **Kaon Setup → Maintenance → Check Status**. Use the operation's
**Details** button for its complete output. To inspect logs in Finder, choose
**Go → Go to Folder** and paste:

```text
~/Library/Logs/Kaon
```

Advanced users can run:

```zsh
~/Library/Application\ Support/Kaon/bin/kaon-setup status --json
open ~/Library/Logs/Kaon
```

Kaon does not intentionally print credentials or the complete user
environment. However, Windows Steam, Wine, CrossOver, or a game can write
arbitrary diagnostic output, and status includes local paths, the account's
short username, the bottle name, and selected options. Review and redact status
and logs before posting them publicly. Never upload an entire Steam or
CrossOver bottle.

## Main files Kaon manages

Kaon's configuration is user-level and does not use `sudo`:

```text
~/Library/Application Support/Kaon/
  bin/kaon-setup                 installed maintenance engine
  lib/                           installed launch and repair resources
  config.json                    selected CrossOver app, bottle, and options
  state/                         ownership and rollback records
  backups/                       content-addressed safety copies
  *.lock                         internal operation coordination

~/Library/LaunchAgents/
  io.github.enjihn.kaon.autoheal.plist              # Automatic repair
  io.github.enjihn.kaon.crossover-steam.plist       # Start at login
  io.github.enjihn.kaon.crossover-tray-guard.plist  # Menu-bar hiding

~/Library/Logs/Kaon/             diagnostic output
```

Only the agents for enabled options are installed. Game launchers live at
`<CrossOver Steam>/steamapps/common/Kaon/`. Kaon also records or updates the
Mac Steam configuration files described under [What Kaon
changes](#what-kaon-changes). **Kaon Setup.app** remains wherever you copied it.

## Command line for developers and advanced users

The native app calls the same auditable engine exposed by a source checkout.
Run these commands from the repository root; they are not paths installed for
ordinary app users:

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

Running from source requires Python 3.10 or newer; CI tests Python 3.13. The
downloadable app contains its own runtime and does not require Homebrew,
Python, Xcode, or command-line tools.

## Limitations and safety

- This integration relies on undocumented Steam metadata behavior. A Steam
  update can require a Kaon update.
- Passing CI confirms the installer builds and its safety tests pass; it does
  not prove that a particular game, Steam build, or CrossOver release works.
  Treat versions not identified as tested in release notes as unverified.
- CrossOver and CrossOver Preview compatibility can change. Experimental
  menu-bar hiding supports only recognized Explorer patterns.
- Mac Steam's Windows override is global. Keep backups of important native Mac
  installs and do not remove the override casually while Steam is open.
- Some anti-cheat, kernel-driver, launcher, DRM, graphics, or architecture
  requirements are incompatible with Wine or CrossOver regardless of Kaon.
- Background-item notifications and controls belong to macOS and cannot be
  suppressed by Kaon.
- Manually triggered development artifacts may be ad-hoc signed and trigger
  Gatekeeper warnings. Tagged public releases are blocked unless Developer ID
  signed and Apple-notarized.

## Building and contributing

The repository keeps the installer separate from the inherited research tree:

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
for file in macos/bin/kaon-setup macos/resources/*.sh; do
  zsh -n "$file"
done
bash -n macos/scripts/build-release.sh
```

Build a release with `macos/scripts/build-release.sh`. See
[docs/PACKAGING.md](docs/PACKAGING.md) for signing, notarization, artifact
policy, and release secrets.

## Licensing and redistribution

The original Kaon work, the native Swift installer, and the repository's
separable utilities use the top-level [Apache License 2.0](LICENSE). The bundled
Python setup and repair engine integrates the AppInfo codec inherited from
Steam Metadata Editor; that combined component is distributed under
GPLv3-or-later. See [third-party notices](docs/THIRD_PARTY_NOTICES.md) and
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
