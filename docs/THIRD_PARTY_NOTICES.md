# Third-party notices and release boundaries

This document describes the contents of Kaon's downloadable macOS installer
artifacts. It is not a relicensing of the repository and is not legal advice.

## Kaon

The original Kaon documentation and launcher work is distributed under the
Apache License 2.0. The release app includes a copy at
`Contents/Resources/Licenses/Apache-2.0.txt`. Modified upstream files retain
their existing attribution and should carry a notice that they were changed.

## Steam Metadata Editor AppInfo codec

Kaon's bundled Python setup and repair engine uses the AppInfo binary VDF codec
derived from Steam Metadata Editor by Tomas Ralph. That project is licensed
under the GNU General Public License, version 3 or (at the recipient's option)
any later version.

The Python engine and codec form the GPL component shipped in the app. The
installer includes the exact corresponding source at
`Contents/Resources/Source/steammetadataeditor/src/appinfo.py` and
`Contents/Resources/Source/macos/engine/`, with the license at
`Contents/Resources/Licenses/GPL-3.0-or-later.txt`. The native setup UI and
separable Apache-licensed utilities remain an aggregate alongside that
component.

Source for the complete release is available from the Git tag associated with
the release artifact.

## Proton and lsteamclient

The source repository inherited an experimental `lsteamclient/` research tree
from upstream Kaon. It contains a copied Proton source tree, numerous gitlinks,
fonts and binary archives, and Steamworks SDK material governed by additional
licenses and agreements.

No file from `lsteamclient/` is included in the macOS installer, ZIP, or DMG.
Kaon's supported setup does not build, download, or install Proton or
`lsteamclient`. Contributors working on that research tree must review each
component's license independently; the repository's top-level Apache license
does not override nested licenses.

## CrossOver and CrossOver Preview

CrossOver is a CodeWeavers product containing both open-source and proprietary
components. Kaon does not redistribute either CrossOver application, a license
file, a bottle, or a CrossOver binary. At runtime the user selects an existing,
licensed installation.

If the optional menu-bar suppression feature is enabled, Kaon derives a
bottle-local Wine Explorer modification from the user's own installation after
strict compatibility checks. No original or modified Explorer binary is stored
in this repository or shipped in a release. The option is documented as
hiding all Windows tray icons in the selected bottle, not only Steam's icon.

CrossOver is a trademark of CodeWeavers, Inc. Use of the name describes
compatibility and does not imply endorsement.

## Steam

Kaon does not redistribute the native Steam client, the Windows Steam client,
Steamworks SDK headers or libraries, games, application manifests, account
data, or any user's Steam cache. Users install Steam themselves and remain
responsible for its terms of use.

Steam is a trademark of Valve Corporation. Use of the name describes
compatibility and does not imply endorsement.

## Python

The self-contained setup helper includes a Python runtime and standard library.
Official builds currently use Python 3.13. Python is distributed under the
Python Software Foundation License and related historical licenses. The
app includes the license text supplied with the exact build interpreter at
`Contents/Resources/Licenses/Python.txt`.

The release does not require or modify a Python installation on the user's Mac.

## PyInstaller

Kaon uses PyInstaller 6.21.0 to freeze the setup engine and Python runtime into
one native helper. PyInstaller is distributed under GPL-2.0-or-later with a
bootloader exception that permits distribution of generated bundles under the
licenses applicable to their contents. Its license and exception are included
at `Contents/Resources/Licenses/PyInstaller.txt`.

## Other build dependencies

The release does not bundle Homebrew, Xcode, or the macOS SDK. The native UI is
compiled with Apple's Swift toolchain. Any future bundled runtime or binary
dependency must be added to this notice together with its license before it may
enter a release artifact.
