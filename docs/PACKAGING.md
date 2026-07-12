# macOS packaging and releases

Kaon's downloadable macOS product is a native `Kaon Setup.app`. The release
workflow publishes a ZIP and compressed DMG for each supported architecture:
Apple Silicon (`arm64`) and Intel (`x86_64`). Artifact names include the
architecture so users can choose the correct download. The app targets macOS
13 or later.

The app contains a frozen `kaon-setup` helper with its own Python runtime.
People installing Kaon do not need Python, Homebrew, Xcode, or command-line
tools. Python and PyInstaller are build-time requirements only.

The setup app configures software that the user already owns and installed. It
does not contain CrossOver, CrossOver Preview, Steam, a CrossOver bottle, a
Windows game, or any derived Windows executable.

## Build locally

Building requires macOS, Xcode command-line tools with Swift 5.9 or newer,
native Python 3.13, PyInstaller 6.21.0, and the system `codesign`, `hdiutil`,
and `ditto` tools. CrossOver and Steam are not needed to compile or package the
installer. Create an isolated packaging environment and build natively:

```sh
python3.13 -m venv .venv-package
.venv-package/bin/python -m pip install 'pyinstaller==6.21.0'
KAON_PYTHON=.venv-package/bin/python \
KAON_ARCH="$(uname -m)" \
macos/scripts/build-release.sh --version 0.1.0 --output dist
```

Run that command on Apple Silicon to produce `*-arm64.zip` and `*-arm64.dmg`,
or on an Intel Mac to produce the corresponding `*-x86_64` artifacts. The
script deliberately rejects cross-architecture builds. GitHub Actions uses one
native runner for each architecture and publishes both variants.

Without a signing identity the script applies an ad-hoc signature. This is
useful for development and produces fully inspectable ZIP and DMG artifacts,
but it does not establish a trusted developer identity. Gatekeeper may require
the downloader to approve an ad-hoc build manually.

The script accepts these command-line options:

```text
--output DIR
--version VERSION
--bundle-id IDENTIFIER
--sign-identity NAME
```

`KAON_ARCH` defaults to the host architecture. `KAON_PYTHON` selects the Python
interpreter that runs the pinned PyInstaller version.

## Deliberately small release payload

`build-release.sh` constructs the app from an allowlist:

- the `KaonInstaller` Swift executable;
- a one-file `kaon-setup` executable frozen from `macos/engine/kaon_setup.py`;
- the repair module, launcher templates, and AppInfo codec embedded into that
  frozen executable;
- exact corresponding source under `Contents/Resources/Source`;
- the GPL, Apache, Python, and PyInstaller licenses and third-party notices.

The source-tree `macos/bin/kaon-setup` wrapper is a developer convenience. It
is not placed in a downloadable release and is not the installer runtime.

The build does not archive the repository. It rejects symbolic links, foreign
PE/ELF/Mach-O executables other than the two explicit native executables,
standalone static or dynamic libraries, Windows executables, and paths named
for `lsteamclient`, `Steam.app`, or either CrossOver application. This keeps
the large inherited Proton research subtree and proprietary user installations
out of every artifact.

Do not weaken this allowlist to make packaging more convenient. New runtime
resources should be reviewed and added intentionally.

## Developer ID signing and notarization

Pass a Developer ID Application identity either with `--sign-identity` or
`KAON_SIGN_IDENTITY`. PyInstaller uses that identity for the binaries embedded
in the frozen runtime. The build then signs the frozen helper itself before it
signs the outer app, enables the hardened runtime, and requests secure
timestamps. Developer ID builds also sign the DMG before notarization.

Notarization is optional and activates only when one of these complete
credential sets is present:

1. `KAON_NOTARY_PROFILE`, naming an existing `notarytool` keychain profile; or
2. all of `KAON_NOTARY_APPLE_ID`, `KAON_NOTARY_TEAM_ID`, and
   `KAON_NOTARY_PASSWORD`.

Partially configured credentials fail the build. `KAON_SKIP_NOTARIZATION=1`
can explicitly suppress notarization for a local Developer ID diagnostic
build. When enabled, the app is submitted first and stapled before the final
ZIP is created; the DMG is then submitted and stapled separately.

Useful verification commands are:

```sh
codesign --verify --deep --strict --verbose=2 "Kaon Setup.app"
spctl --assess --type execute --verbose=2 "Kaon Setup.app"
xcrun stapler validate "Kaon Setup.app"
xcrun stapler validate Kaon-Setup-0.1.0-arm64.dmg
shasum -a 256 -c SHA256SUMS-arm64
```

## GitHub Actions secrets

The release workflow always works without secrets and produces an ad-hoc
development release. To produce a trusted public release, configure:

- `MACOS_CERTIFICATE_P12`: base64-encoded Developer ID Application `.p12`;
- `MACOS_CERTIFICATE_PASSWORD`: password protecting that `.p12`;
- `MACOS_SIGNING_IDENTITY`: exact identity name (optional when the imported
  certificate contains one unambiguous Developer ID Application identity);
- `APPLE_ID`;
- `APPLE_TEAM_ID`;
- `APPLE_APP_PASSWORD`.

The certificate is imported into an ephemeral keychain, used only for the
release job, and deleted in an `always()` cleanup step. Pull requests never
receive signing or notarization credentials.

## CI and release flow

`.github/workflows/ci.yml` tests on both GitHub-hosted ARM64 and Intel macOS
runners. It validates scripts and plists, runs the Python and Swift test suites,
builds both native ad-hoc releases, audits each ZIP, verifies checksums, and
executes the frozen helper in an environment that does not expose Python.

`.github/workflows/release.yml` runs on `v*` tags or manually. Its native runner
matrix freezes, signs, optionally notarizes, and uploads both architectures.
Tag builds publish both ZIPs, both DMGs, and one consolidated `SHA256SUMS` file
to a GitHub release. A manual run creates architecture-labeled workflow
artifacts without publishing a GitHub release, which is useful for
release-candidate inspection.

Before tagging a public version, complete a clean-machine smoke test on both an
Apple Silicon Mac and an Intel Mac using current regular CrossOver and, where
available, CrossOver Preview. Exercise install, repeated repair, background
Steam startup, both optional hiding controls, CrossOver update fallback, and
uninstall/restore.
