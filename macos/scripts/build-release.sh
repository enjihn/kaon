#!/bin/bash

# Build Kaon's native macOS setup application and release containers.
#
# The release payload is assembled from an explicit allowlist. In particular,
# the inherited Proton/lsteamclient research tree and all locally installed
# Steam, CrossOver, bottle, and Windows binary content are never copied.

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"

OUTPUT_DIR="$REPO_ROOT/dist"
VERSION="${KAON_VERSION:-}"
BUNDLE_ID="${KAON_BUNDLE_ID:-io.github.enjihn.kaon.setup}"
SIGN_IDENTITY="${KAON_SIGN_IDENTITY:--}"
PYTHON="${KAON_PYTHON:-python3}"
readonly TARGET_ARCH="arm64"
SWIFT_BUILD="${KAON_SWIFT_BUILD:-}"
readonly PYINSTALLER_VERSION="6.21.0"

usage() {
    /bin/cat <<'EOF'
usage: macos/scripts/build-release.sh [options]

Options:
  --output DIR             Write artifacts to DIR (default: ./dist)
  --version VERSION        Release version (default: tag or commit identifier)
  --bundle-id IDENTIFIER   App bundle identifier
  --sign-identity NAME     Developer ID identity, or - for ad-hoc signing
  -h, --help               Show this help

Environment:
  KAON_PYTHON                Python used to run PyInstaller (default: python3)
  KAON_SWIFT_BUILD           Direct swift-build executable (advanced override)
  KAON_NOTARY_PROFILE        notarytool keychain profile
  KAON_NOTARY_APPLE_ID       Apple ID used by notarytool
  KAON_NOTARY_TEAM_ID        Apple Developer team ID
  KAON_NOTARY_PASSWORD       App-specific Apple ID password
  KAON_SKIP_NOTARIZATION=1   Do not notarize even when credentials are present
  KAON_KEEP_BUILD=1          Keep the temporary build directory
EOF
}

die() {
    echo "build-release: $*" >&2
    exit 1
}

while (( $# > 0 )); do
    case "$1" in
        --output)
            (( $# >= 2 )) || die "--output requires a directory"
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --version)
            (( $# >= 2 )) || die "--version requires a value"
            VERSION="$2"
            shift 2
            ;;
        --bundle-id)
            (( $# >= 2 )) || die "--bundle-id requires a value"
            BUNDLE_ID="$2"
            shift 2
            ;;
        --sign-identity)
            (( $# >= 2 )) || die "--sign-identity requires a value"
            SIGN_IDENTITY="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1"
            ;;
    esac
done

[[ "$(uname -s)" == "Darwin" ]] || die "release builds require macOS"
[[ "$BUNDLE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] \
    || die "invalid bundle identifier: $BUNDLE_ID"
[[ "$(uname -m)" == "$TARGET_ARCH" ]] \
    || die "Kaon releases are Apple Silicon only; build on an arm64 Mac (host is $(uname -m))"
command -v "$PYTHON" >/dev/null 2>&1 || die "Python was not found: $PYTHON"
PYTHON_ARCH="$("$PYTHON" -c 'import platform; print(platform.machine())')"
[[ "$PYTHON_ARCH" == "$TARGET_ARCH" ]] \
    || die "$PYTHON is running as $PYTHON_ARCH; expected $TARGET_ARCH"
INSTALLED_PYINSTALLER_VERSION="$("$PYTHON" -m PyInstaller --version 2>/dev/null || true)"
[[ "$INSTALLED_PYINSTALLER_VERSION" == "$PYINSTALLER_VERSION" ]] || die \
    "PyInstaller $PYINSTALLER_VERSION is required (install with: $PYTHON -m pip install pyinstaller==$PYINSTALLER_VERSION)"

if [[ -z "$VERSION" ]]; then
    VERSION="$(git -C "$REPO_ROOT" describe --tags --always 2>/dev/null || true)"
fi
[[ -n "$VERSION" ]] || VERSION="0.0.0"

readonly DISPLAY_VERSION="${VERSION#v}"
PLIST_VERSION="$(printf '%s' "$DISPLAY_VERSION" | sed -E 's/[^0-9.].*$//')"
if [[ ! "$PLIST_VERSION" =~ ^[0-9]+([.][0-9]+){0,2}$ ]]; then
    PLIST_VERSION="0.0.0"
fi
BUILD_NUMBER="$(git -C "$REPO_ROOT" rev-list --count HEAD 2>/dev/null || true)"
[[ "$BUILD_NUMBER" =~ ^[0-9]+$ ]] || BUILD_NUMBER="$(date -u '+%Y%m%d')"
ARTIFACT_VERSION="$(printf '%s' "$DISPLAY_VERSION" | tr -c 'A-Za-z0-9._-' '-')"
[[ -n "$ARTIFACT_VERSION" ]] || ARTIFACT_VERSION="0.0.0"

readonly PACKAGE_DIR="$REPO_ROOT/macos/installer"
readonly ENGINE_DIR="$REPO_ROOT/macos/engine"
readonly ENGINE_ENTRYPOINT="$ENGINE_DIR/kaon_setup.py"
readonly REPAIR_SOURCE="$ENGINE_DIR/kaon_repair.py"
readonly RUNTIME_RESOURCES="$REPO_ROOT/macos/resources"
readonly APPINFO_SOURCE="$REPO_ROOT/steammetadataeditor/src/appinfo.py"
readonly APACHE_LICENSE="$REPO_ROOT/LICENSE"
readonly GPL_LICENSE="$REPO_ROOT/steammetadataeditor/LICENSE"
readonly THIRD_PARTY_NOTICES="$REPO_ROOT/docs/THIRD_PARTY_NOTICES.md"

[[ -f "$PACKAGE_DIR/Package.swift" ]] || die "missing Swift package: $PACKAGE_DIR"
[[ -f "$ENGINE_ENTRYPOINT" ]] || die "missing setup engine: $ENGINE_ENTRYPOINT"
[[ -f "$REPAIR_SOURCE" ]] || die "missing repair engine: $REPAIR_SOURCE"
[[ -f "$RUNTIME_RESOURCES/launch_crossover.sh" ]] || die "missing CrossOver launcher template"
[[ -f "$RUNTIME_RESOURCES/launch_with_log.sh" ]] || die "missing logging launcher template"
[[ -f "$APPINFO_SOURCE" ]] || die "missing GPL AppInfo codec: $APPINFO_SOURCE"
[[ -f "$APACHE_LICENSE" ]] || die "missing Apache license"
[[ -f "$GPL_LICENSE" ]] || die "missing GPL license"
[[ -f "$THIRD_PARTY_NOTICES" ]] || die "missing third-party notices"

PYTHON_LICENSE="$("$PYTHON" - <<'PY' || true
from pathlib import Path
import sys
import sysconfig

candidates = (
    Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
    Path(sys.base_prefix) / "LICENSE.txt",
    Path(sys.base_prefix) / "LICENSE",
    Path(sys.prefix) / "LICENSE.txt",
    Path(sys.prefix) / "LICENSE",
)
for candidate in candidates:
    if candidate.is_file():
        print(candidate.resolve())
        break
PY
)"
[[ -f "$PYTHON_LICENSE" ]] || die "could not locate the bundled Python runtime license"

PYINSTALLER_LICENSE="$("$PYTHON" - <<'PY' || true
from importlib import metadata
from pathlib import Path

for item in metadata.files("pyinstaller") or ():
    if Path(str(item)).name == "COPYING.txt":
        candidate = Path(item.locate())
        if candidate.is_file():
            print(candidate.resolve())
            break
PY
)"
[[ -f "$PYINSTALLER_LICENSE" ]] || die "could not locate PyInstaller's COPYING.txt"

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd -P)"

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/kaon-release.XXXXXX")"
cleanup() {
    if [[ "${KAON_KEEP_BUILD:-0}" == "1" ]]; then
        echo "Kept build directory: $BUILD_ROOT"
    else
        rm -rf "$BUILD_ROOT"
    fi
}
trap cleanup EXIT

readonly APP_NAME="Kaon Setup.app"
readonly APP_BUNDLE="$BUILD_ROOT/$APP_NAME"
readonly CONTENTS="$APP_BUNDLE/Contents"
readonly MACOS_DIR="$CONTENTS/MacOS"
readonly RESOURCES_DIR="$CONTENTS/Resources"
readonly ENTRYPOINT_DIR="$RESOURCES_DIR/Kaon/bin"
readonly SOURCE_DIR="$RESOURCES_DIR/Source"
readonly SOURCE_ENGINE_DIR="$SOURCE_DIR/macos/engine"
readonly SOURCE_RESOURCES_DIR="$SOURCE_DIR/macos/resources"
readonly SOURCE_EDITOR_DIR="$SOURCE_DIR/steammetadataeditor/src"

mkdir -p \
    "$MACOS_DIR" \
    "$ENTRYPOINT_DIR" \
    "$SOURCE_ENGINE_DIR" \
    "$SOURCE_RESOURCES_DIR" \
    "$SOURCE_EDITOR_DIR" \
    "$RESOURCES_DIR/Licenses"

FROZEN_DIST="$BUILD_ROOT/pyinstaller-dist"
PYINSTALLER_ARGS=(
    --noconfirm
    --clean
    --onefile
    --name kaon-setup
    --target-architecture "$TARGET_ARCH"
    --distpath "$FROZEN_DIST"
    --workpath "$BUILD_ROOT/pyinstaller-work"
    --specpath "$BUILD_ROOT/pyinstaller-spec"
    --paths "$ENGINE_DIR"
    --hidden-import kaon_repair
    --add-data "$REPAIR_SOURCE:engine"
    --add-data "$APPINFO_SOURCE:steammetadataeditor/src"
    --add-data "$RUNTIME_RESOURCES/launch_crossover.sh:resources"
    --add-data "$RUNTIME_RESOURCES/launch_with_log.sh:resources"
)
if [[ "$SIGN_IDENTITY" != "-" && -n "$SIGN_IDENTITY" ]]; then
    PYINSTALLER_ARGS+=(--codesign-identity "$SIGN_IDENTITY")
fi

echo "Freezing kaon-setup for $TARGET_ARCH with PyInstaller $PYINSTALLER_VERSION"
"$PYTHON" -m PyInstaller "${PYINSTALLER_ARGS[@]}" "$ENGINE_ENTRYPOINT"
[[ -x "$FROZEN_DIST/kaon-setup" ]] || die "PyInstaller did not create kaon-setup"
install -m 0755 "$FROZEN_DIST/kaon-setup" "$ENTRYPOINT_DIR/kaon-setup"
/usr/bin/lipo -verify_arch "$TARGET_ARCH" "$ENTRYPOINT_DIR/kaon-setup"
[[ "$(/usr/bin/lipo -archs "$ENTRYPOINT_DIR/kaon-setup")" == "$TARGET_ARCH" ]] \
    || die "frozen kaon-setup is not a thin $TARGET_ARCH executable"
FROZEN_ARCHIVE_LIST="$BUILD_ROOT/pyinstaller-archive.txt"
"$PYTHON" -m PyInstaller.utils.cliutils.archive_viewer \
    -r "$ENTRYPOINT_DIR/kaon-setup" > "$FROZEN_ARCHIVE_LIST"
for required_resource in \
    engine/kaon_repair.py \
    steammetadataeditor/src/appinfo.py \
    resources/launch_crossover.sh \
    resources/launch_with_log.sh; do
    /usr/bin/grep -Fq "$required_resource" "$FROZEN_ARCHIVE_LIST" \
        || die "frozen kaon-setup is missing $required_resource"
done
/usr/bin/grep -Eq "'kaon_repair'[[:space:]]*$" "$FROZEN_ARCHIVE_LIST" \
    || die "frozen kaon-setup is missing the kaon_repair module"

scratch="$BUILD_ROOT/swift-$TARGET_ARCH"
echo "Building KaonInstaller for $TARGET_ARCH"
if [[ -n "$SWIFT_BUILD" ]]; then
    "$SWIFT_BUILD" \
        --package-path "$PACKAGE_DIR" \
        --scratch-path "$scratch" \
        --configuration release \
        --arch "$TARGET_ARCH"
    bin_path="$("$SWIFT_BUILD" \
        --package-path "$PACKAGE_DIR" \
        --scratch-path "$scratch" \
        --configuration release \
        --arch "$TARGET_ARCH" \
        --show-bin-path)"
else
    swift build \
        --package-path "$PACKAGE_DIR" \
        --scratch-path "$scratch" \
        --configuration release \
        --arch "$TARGET_ARCH"
    bin_path="$(swift build \
        --package-path "$PACKAGE_DIR" \
        --scratch-path "$scratch" \
        --configuration release \
        --arch "$TARGET_ARCH" \
        --show-bin-path)"
fi
binary="$bin_path/KaonInstaller"
[[ -x "$binary" ]] || die "Swift product was not created: $binary"
install -m 0755 "$binary" "$MACOS_DIR/KaonInstaller"
/usr/bin/lipo -verify_arch "$TARGET_ARCH" "$MACOS_DIR/KaonInstaller"
[[ "$(/usr/bin/lipo -archs "$MACOS_DIR/KaonInstaller")" == "$TARGET_ARCH" ]] \
    || die "KaonInstaller is not a thin $TARGET_ARCH executable"

# The frozen helper contains the complete runtime. Keep the exact GPL source
# beside it so every distributed binary carries its corresponding source.
install -m 0644 "$ENGINE_ENTRYPOINT" "$SOURCE_ENGINE_DIR/kaon_setup.py"
install -m 0644 "$REPAIR_SOURCE" "$SOURCE_ENGINE_DIR/kaon_repair.py"
install -m 0644 "$RUNTIME_RESOURCES/launch_crossover.sh" "$SOURCE_RESOURCES_DIR/launch_crossover.sh"
install -m 0644 "$RUNTIME_RESOURCES/launch_with_log.sh" "$SOURCE_RESOURCES_DIR/launch_with_log.sh"
install -m 0644 "$APPINFO_SOURCE" "$SOURCE_EDITOR_DIR/appinfo.py"
install -m 0644 "$GPL_LICENSE" "$RESOURCES_DIR/Licenses/GPL-3.0-or-later.txt"
install -m 0644 "$APACHE_LICENSE" "$RESOURCES_DIR/Licenses/Apache-2.0.txt"
install -m 0644 "$PYTHON_LICENSE" "$RESOURCES_DIR/Licenses/Python.txt"
install -m 0644 "$PYINSTALLER_LICENSE" "$RESOURCES_DIR/Licenses/PyInstaller.txt"
install -m 0644 "$THIRD_PARTY_NOTICES" "$RESOURCES_DIR/THIRD_PARTY_NOTICES.md"

INFO_PLIST="$CONTENTS/Info.plist"
/usr/bin/plutil -create xml1 "$INFO_PLIST"
/usr/bin/plutil -insert CFBundleDevelopmentRegion -string en "$INFO_PLIST"
/usr/bin/plutil -insert CFBundleDisplayName -string "Kaon Setup" "$INFO_PLIST"
/usr/bin/plutil -insert CFBundleExecutable -string KaonInstaller "$INFO_PLIST"
/usr/bin/plutil -insert CFBundleIdentifier -string "$BUNDLE_ID" "$INFO_PLIST"
/usr/bin/plutil -insert CFBundleInfoDictionaryVersion -string 6.0 "$INFO_PLIST"
/usr/bin/plutil -insert CFBundleName -string "Kaon Setup" "$INFO_PLIST"
/usr/bin/plutil -insert CFBundlePackageType -string APPL "$INFO_PLIST"
/usr/bin/plutil -insert CFBundleShortVersionString -string "$PLIST_VERSION" "$INFO_PLIST"
/usr/bin/plutil -insert CFBundleVersion -string "$BUILD_NUMBER" "$INFO_PLIST"
/usr/bin/plutil -insert LSApplicationCategoryType -string public.app-category.utilities "$INFO_PLIST"
/usr/bin/plutil -insert LSMinimumSystemVersion -string 13.0 "$INFO_PLIST"
/usr/bin/plutil -insert NSHighResolutionCapable -bool true "$INFO_PLIST"
/usr/bin/plutil -insert NSHumanReadableCopyright -string "Copyright 2026 Kaon contributors" "$INFO_PLIST"
/usr/bin/plutil -lint "$CONTENTS/Info.plist"

assert_release_policy() {
    local unsafe_paths unsafe_type candidate description

    unsafe_paths="$(find "$APP_BUNDLE" \
        \( -type f -o -type d \) \
        \( -iname 'lsteamclient' \
           -o -iname 'Steam.app' \
           -o -iname 'CrossOver.app' \
           -o -iname 'CrossOver Preview.app' \
           -o -iname '*.exe' \
           -o -iname '*.dll' \
           -o -iname '*.so' \
           -o -iname '*.dylib' \
           -o -iname '*.a' \
           -o -iname '*.framework' \) \
        -print)"
    [[ -z "$unsafe_paths" ]] \
        || die "forbidden release content detected:"$'\n'"$unsafe_paths"

    unsafe_paths="$(find "$APP_BUNDLE" -type l -print)"
    [[ -z "$unsafe_paths" ]] \
        || die "release payload contains symbolic links:"$'\n'"$unsafe_paths"

    unsafe_type=""
    while IFS= read -r candidate; do
        case "$candidate" in
            "$MACOS_DIR/KaonInstaller"|"$ENTRYPOINT_DIR/kaon-setup") continue ;;
        esac
        description="$(/usr/bin/file -b "$candidate")"
        case "$description" in
            *PE32*|*ELF*|*Mach-O*)
                unsafe_type="${unsafe_type}${candidate}: ${description}"$'\n'
                ;;
        esac
    done < <(find "$APP_BUNDLE" -type f -print)
    [[ -z "$unsafe_type" ]] \
        || die "foreign executable content detected:"$'\n'"$unsafe_type"
}

assert_release_policy

if [[ "$SIGN_IDENTITY" == "-" || -z "$SIGN_IDENTITY" ]]; then
    echo "Applying ad-hoc signature"
    /usr/bin/codesign \
        --force \
        --identifier "$BUNDLE_ID.helper" \
        --sign - \
        "$ENTRYPOINT_DIR/kaon-setup"
    /usr/bin/codesign --force --sign - "$APP_BUNDLE"
    SIGN_IDENTITY="-"
else
    echo "Signing with Developer ID identity: $SIGN_IDENTITY"
    /usr/bin/codesign \
        --force \
        --identifier "$BUNDLE_ID.helper" \
        --options runtime \
        --timestamp \
        --sign "$SIGN_IDENTITY" \
        "$ENTRYPOINT_DIR/kaon-setup"
    /usr/bin/codesign \
        --force \
        --options runtime \
        --timestamp \
        --sign "$SIGN_IDENTITY" \
        "$APP_BUNDLE"
fi
/usr/bin/codesign --verify --strict --verbose=2 "$ENTRYPOINT_DIR/kaon-setup"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

NOTARY_MODE=""
if [[ "${KAON_SKIP_NOTARIZATION:-0}" != "1" ]]; then
    if [[ -n "${KAON_NOTARY_PROFILE:-}" ]]; then
        NOTARY_MODE="profile"
    else
        notary_value_count=0
        if [[ -n "${KAON_NOTARY_APPLE_ID:-}" ]]; then
            (( notary_value_count += 1 ))
        fi
        if [[ -n "${KAON_NOTARY_TEAM_ID:-}" ]]; then
            (( notary_value_count += 1 ))
        fi
        if [[ -n "${KAON_NOTARY_PASSWORD:-}" ]]; then
            (( notary_value_count += 1 ))
        fi
        if (( notary_value_count == 3 )); then
            NOTARY_MODE="apple-id"
        elif (( notary_value_count != 0 )); then
            die "notarization credentials are incomplete"
        fi
    fi
fi

notary_submit() {
    local artifact="$1"
    case "$NOTARY_MODE" in
        profile)
            xcrun notarytool submit \
                "$artifact" \
                --keychain-profile "$KAON_NOTARY_PROFILE" \
                --wait
            ;;
        apple-id)
            xcrun notarytool submit \
                "$artifact" \
                --apple-id "$KAON_NOTARY_APPLE_ID" \
                --team-id "$KAON_NOTARY_TEAM_ID" \
                --password "$KAON_NOTARY_PASSWORD" \
                --wait
            ;;
        *)
            die "internal error: notarization was requested without credentials"
            ;;
    esac
}

if [[ -n "$NOTARY_MODE" ]]; then
    [[ "$SIGN_IDENTITY" != "-" ]] \
        || die "notarization requires a Developer ID signature"
    temporary_zip="$BUILD_ROOT/Kaon-Setup-notarization.zip"
    /usr/bin/ditto -c -k --keepParent "$APP_BUNDLE" "$temporary_zip"
    notary_submit "$temporary_zip"
    xcrun stapler staple "$APP_BUNDLE"
    xcrun stapler validate "$APP_BUNDLE"
fi

readonly ZIP_NAME="Kaon-Setup-$ARTIFACT_VERSION.zip"
readonly DMG_NAME="Kaon-Setup-$ARTIFACT_VERSION.dmg"
readonly CHECKSUM_NAME="SHA256SUMS"
readonly SIGNING_STATUS_NAME="SIGNING-STATUS.txt"
readonly ZIP_PATH="$OUTPUT_DIR/$ZIP_NAME"
readonly DMG_PATH="$OUTPUT_DIR/$DMG_NAME"
readonly CHECKSUM_PATH="$OUTPUT_DIR/$CHECKSUM_NAME"
readonly SIGNING_STATUS_PATH="$OUTPUT_DIR/$SIGNING_STATUS_NAME"
rm -f "$ZIP_PATH" "$DMG_PATH" "$CHECKSUM_PATH" "$SIGNING_STATUS_PATH"

/usr/bin/ditto -c -k --keepParent "$APP_BUNDLE" "$ZIP_PATH"

DMG_STAGE="$BUILD_ROOT/dmg"
mkdir -p "$DMG_STAGE"
/usr/bin/ditto "$APP_BUNDLE" "$DMG_STAGE/$APP_NAME"
/bin/ln -s /Applications "$DMG_STAGE/Applications"
/usr/bin/hdiutil create \
    -quiet \
    -ov \
    -format UDZO \
    -volname "Kaon Setup" \
    -srcfolder "$DMG_STAGE" \
    "$DMG_PATH"

if [[ "$SIGN_IDENTITY" != "-" ]]; then
    /usr/bin/codesign \
        --force \
        --timestamp \
        --sign "$SIGN_IDENTITY" \
        "$DMG_PATH"
    /usr/bin/codesign --verify --strict --verbose=2 "$DMG_PATH"
fi

if [[ -n "$NOTARY_MODE" ]]; then
    notary_submit "$DMG_PATH"
    xcrun stapler staple "$DMG_PATH"
    xcrun stapler validate "$DMG_PATH"
    /usr/sbin/spctl --assess --type execute --verbose=2 "$APP_BUNDLE"
    /usr/sbin/spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG_PATH"
fi

(
    cd "$OUTPUT_DIR"
    /usr/bin/shasum -a 256 "$ZIP_NAME" "$DMG_NAME" > "$CHECKSUM_NAME"
)

echo "Created release artifacts:"
echo "  $ZIP_PATH"
echo "  $DMG_PATH"
echo "  $CHECKSUM_PATH"
if [[ "$SIGN_IDENTITY" == "-" ]]; then
    SIGNING_SUMMARY="ad-hoc signed; not notarized (Gatekeeper confirmation required)"
elif [[ -n "$NOTARY_MODE" ]]; then
    SIGNING_SUMMARY="Developer ID signed and Apple-notarized; tickets stapled"
else
    SIGNING_SUMMARY="Developer ID signed; not notarized"
fi
printf 'Apple Silicon (arm64): %s\n' "$SIGNING_SUMMARY" > "$SIGNING_STATUS_PATH"
echo "Signature: $SIGNING_SUMMARY"
echo "  $SIGNING_STATUS_PATH"
