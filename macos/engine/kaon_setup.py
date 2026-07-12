# Kaon - macOS Steam / CrossOver integration
# Copyright (C) 2026 Kaon contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Install, repair, inspect, and remove Kaon's macOS integration.

The release build freezes this module into a self-contained executable.  The
source-tree ``macos/bin/kaon-setup`` wrapper is intentionally only a developer
convenience and may use the caller's Python 3 installation.
"""

from __future__ import annotations

import argparse
import base64
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence, TypeVar
from urllib.parse import unquote, urlparse


SCHEMA_VERSION = 1
SUPPORT_ROOT = Path.home() / "Library/Application Support/Kaon"
CONFIG_PATH = SUPPORT_ROOT / "config.json"
STATE_PATH = SUPPORT_ROOT / "state/install-state.json"
BACKUP_ROOT = SUPPORT_ROOT / "backups"
LOG_ROOT = Path.home() / "Library/Logs/Kaon"
LAUNCH_AGENT_ROOT = Path.home() / "Library/LaunchAgents"
STEAM_ROOT = Path.home() / "Library/Application Support/Steam"
BOTTLES_ROOT = Path.home() / "Library/Application Support/CrossOver/Bottles"
LABEL_PREFIX = "io.github.enjihn.kaon"
LABELS = {
    "autoheal": f"{LABEL_PREFIX}.autoheal",
    "steam": f"{LABEL_PREFIX}.crossover-steam",
    "tray": f"{LABEL_PREFIX}.crossover-tray-guard",
}
LEGACY_LABELS = (
    "com.natbro.kaon.autoheal",
    "com.natbro.kaon.crossover-steam",
    "com.natbro.kaon.crossover-tray-guard",
)
PLATFORM_LINE = "@sSteamCmdForcePlatformType windows"
STEAM_EXE_WINDOWS = "C:/Program Files (x86)/Steam/steam.exe"
MANAGED_DESCRIPTION_TEMPLATE = "Play through {name} (Kaon)"
T = TypeVar("T")

# Active transaction snapshots receive exact post-write fingerprints from the
# atomic helpers below. Rollback uses those fingerprints as a compare-and-swap
# guard: if Steam, the user, or another process changes a file after Kaon does,
# Kaon leaves the newer bytes alone and preserves a recovery snapshot.
_ACTIVE_MUTATION_SNAPSHOTS: list[Any] = []


class SetupError(RuntimeError):
    """Expected, user-facing setup failure."""

    def __init__(self, message: str, exit_code: int = 70):
        super().__init__(message)
        self.exit_code = exit_code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_fingerprint(path: Path) -> tuple[Any, ...]:
    """Return an exact, comparison-friendly identity for a managed path."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return ("missing",)
    if stat.S_ISLNK(metadata.st_mode):
        return ("symlink", os.readlink(path))
    if stat.S_ISREG(metadata.st_mode):
        return ("file", sha256_file(path), stat.S_IMODE(metadata.st_mode))
    return ("other", stat.S_IFMT(metadata.st_mode))


def _record_active_path_mutation(
    path: Path, fingerprint: tuple[Any, ...] | None = None
) -> None:
    for snapshot in tuple(_ACTIVE_MUTATION_SNAPSHOTS):
        snapshot.record_path_mutation(path, fingerprint)


def _record_active_dock_mutation(data: Mapping[str, Any]) -> None:
    for snapshot in tuple(_ACTIVE_MUTATION_SNAPSHOTS):
        snapshot.record_dock_mutation(data)


def _prepare_active_dock_mutation(before: Mapping[str, Any]) -> None:
    fresh = dock_export()
    if fresh != before:
        raise SetupError("The Dock changed while Kaon was preparing an update; try again.", 75)
    for snapshot in tuple(_ACTIVE_MUTATION_SNAPSHOTS):
        snapshot.assert_dock_preimage(before)


def run(
    arguments: Sequence[str | os.PathLike[str]],
    *,
    check: bool = False,
    capture: bool = True,
    input_data: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [os.fspath(item) for item in arguments],
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        input=input_data,
    )


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.kaon-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
        _record_active_path_mutation(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, data: Mapping[str, Any], mode: int = 0o600) -> None:
    encoded = json.dumps(data, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    atomic_write(path, encoded, mode)


def atomic_symlink(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.kaon-{os.getpid()}.tmp")
    try:
        staged.unlink(missing_ok=True)
        staged.symlink_to(target)
        os.replace(staged, path)
        fsync_directory(path.parent)
        _record_active_path_mutation(path)
    finally:
        staged.unlink(missing_ok=True)


def atomic_copy(source: Path, destination: Path, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.kaon-", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        temporary.chmod(mode)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        fsync_directory(destination.parent)
        _record_active_path_mutation(destination)
    finally:
        temporary.unlink(missing_ok=True)


def tracked_unlink(path: Path, *, missing_ok: bool = True) -> bool:
    """Unlink a managed path and make the deletion visible to rollback."""

    existed = path.is_symlink() or path.exists()
    path.unlink(missing_ok=missing_ok)
    if existed:
        fsync_directory(path.parent)
        _record_active_path_mutation(path, ("missing",))
    return existed


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    if path.is_symlink() or not path.is_file():
        raise SetupError(f"Refusing unsafe state path: {path}", 65)
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise SetupError(f"Could not read {path}: {error}", 65) from error


def load_config(required: bool = True) -> dict[str, Any]:
    config = load_json(CONFIG_PATH, None)
    if config is None:
        if required:
            raise SetupError("Kaon is not configured yet. Run `kaon-setup install`.", 78)
        return {}
    if not isinstance(config, dict) or config.get("schema_version") != SCHEMA_VERSION:
        raise SetupError(f"Unsupported Kaon configuration: {CONFIG_PATH}", 78)
    return config


def load_state() -> dict[str, Any]:
    state = load_json(STATE_PATH, None)
    if state is None:
        return {"schema_version": SCHEMA_VERSION, "files": {}, "library_entries": {}}
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        raise SetupError(f"Unsupported Kaon installation state: {STATE_PATH}", 78)
    state.setdefault("files", {})
    state.setdefault("library_entries", {})
    return state


def save_state(state: Mapping[str, Any]) -> None:
    atomic_json(STATE_PATH, state)


def ensure_private_directories() -> None:
    for directory in (SUPPORT_ROOT, SUPPORT_ROOT / "bin", SUPPORT_ROOT / "lib", SUPPORT_ROOT / "state", BACKUP_ROOT, LOG_ROOT):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    LAUNCH_AGENT_ROOT.mkdir(parents=True, exist_ok=True)


def acquire_setup_lock():
    ensure_private_directories()
    stream = (SUPPORT_ROOT / "setup.lock").open("a+")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        stream.close()
        raise SetupError("Another Kaon setup operation is active.", 75) from error
    return stream


def acquire_steam_start_lock():
    """Serialize startup attempts from setup, launchd, and the UI."""

    ensure_private_directories()
    stream = (SUPPORT_ROOT / "steam-start.lock").open("a+")
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    return stream


def resource_path(relative: str) -> Path:
    candidates: list[Path] = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.extend((Path(frozen_root) / relative, Path(frozen_root) / "kaon" / relative))
    source_file = Path(__file__).resolve()
    candidates.extend(
        (
            source_file.parents[1] / relative,
            source_file.parents[2] / relative,
            SUPPORT_ROOT / "lib" / Path(relative).name,
        )
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SetupError(f"Kaon installation resource is missing: {relative}", 66)


def native_steam_running() -> bool:
    return run(("/usr/bin/pgrep", "-x", "steam_osx")).returncode == 0


def require_native_steam_closed() -> None:
    if native_steam_running():
        raise SetupError(
            "Quit native macOS Steam completely before installing, repairing, or uninstalling Kaon.",
            75,
        )


def native_steam_mutation(action: Callable[[], T]) -> T:
    """Run one bounded native-Steam mutation with checks on both sides."""

    require_native_steam_closed()
    result = action()
    require_native_steam_closed()
    return result


def steam_paths(config: Mapping[str, Any]) -> dict[str, Path]:
    bottle_root = BOTTLES_ROOT / str(config["bottle"])
    shared_root = bottle_root / "drive_c/Program Files (x86)/Steam"
    return {
        "bottle_root": bottle_root,
        "shared_root": shared_root,
        "shared_steamapps": shared_root / "steamapps",
        "windows_steam": shared_root / "steam.exe",
        "appinfo": STEAM_ROOT / "appcache/appinfo.vdf",
        "steam_dev": STEAM_ROOT / "Steam.AppBundle/Steam/Contents/MacOS/steam_dev.cfg",
    }


def validate_bottle_name(name: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise SetupError("Bottle names may not be empty or contain path separators.", 64)
    return name


def crossover_candidates(edition: str) -> list[Path]:
    user_apps = Path.home() / "Applications"
    system_apps = Path("/Applications")
    if edition == "stable":
        names = ("CrossOver.app",)
    elif edition == "preview":
        names = ("CrossOver Preview.app", "CrossOver-Preview.app")
    else:
        return []
    candidates = [root / name for root in (user_apps, system_apps) for name in names]
    if edition == "preview":
        for root in (user_apps, system_apps):
            if root.is_dir():
                candidates.extend(sorted(root.glob("CrossOver*Preview*.app")))
    deduplicated: list[Path] = []
    for item in candidates:
        if item not in deduplicated:
            deduplicated.append(item)
    return deduplicated


def validate_crossover_app(path: Path) -> Path:
    path = path.expanduser()
    wine = path / "Contents/SharedSupport/CrossOver/bin/wine"
    info = path / "Contents/Info.plist"
    if not path.is_dir() or not info.is_file() or not os.access(wine, os.X_OK):
        raise SetupError(f"This is not a usable CrossOver application: {path}", 66)
    try:
        with info.open("rb") as stream:
            metadata = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as error:
        raise SetupError(f"Could not inspect CrossOver at {path}: {error}", 66) from error
    bundle_id = str(metadata.get("CFBundleIdentifier", ""))
    if "codeweavers" not in bundle_id.lower():
        raise SetupError(f"Unexpected CrossOver bundle identifier {bundle_id!r}: {path}", 66)
    signature = run(("/usr/bin/codesign", "--verify", "--deep", "--strict", path))
    if signature.returncode != 0:
        details = signature.stderr.decode(errors="replace").strip()
        raise SetupError(f"CrossOver's code signature did not validate: {path}: {details}", 66)
    return path.resolve()


def choose_crossover(edition: str | None, explicit: str | None) -> tuple[str, Path]:
    if explicit:
        selected_edition = edition or "custom"
        return selected_edition, validate_crossover_app(Path(explicit))
    editions = [edition] if edition in ("stable", "preview") else ["stable", "preview"]
    for candidate_edition in editions:
        for candidate in crossover_candidates(candidate_edition):
            try:
                return candidate_edition, validate_crossover_app(candidate)
            except SetupError:
                continue
    requested = edition or "CrossOver or CrossOver Preview"
    raise SetupError(f"Could not find {requested}. Install it first or pass --crossover-app PATH.", 66)


def cross_over_display_name(config: Mapping[str, Any]) -> str:
    edition = str(config.get("crossover_edition", "custom"))
    return {"stable": "CrossOver", "preview": "CrossOver Preview"}.get(edition, Path(str(config["crossover_app"])).stem)


def preflight(config: Mapping[str, Any], require_appinfo: bool = True) -> dict[str, Path]:
    validate_crossover_app(Path(str(config["crossover_app"])))
    validate_bottle_name(str(config["bottle"]))
    paths = steam_paths(config)
    if not paths["bottle_root"].is_dir() or paths["bottle_root"].is_symlink():
        raise SetupError(f"CrossOver bottle not found: {paths['bottle_root']}", 66)
    bottle_configuration = paths["bottle_root"] / "cxbottle.conf"
    if config.get("crossover_edition") == "stable" and bottle_configuration.is_file():
        bottle_text = bottle_configuration.read_text(encoding="utf-8", errors="replace")
        if re.search(r'^\s*"Preview"\s*=\s*"1"\s*$', bottle_text, re.MULTILINE):
            raise SetupError(
                f"Bottle {config['bottle']!r} is marked for CrossOver Preview. Select CrossOver Preview or choose a non-Preview bottle.",
                66,
            )
    if not paths["windows_steam"].is_file():
        raise SetupError(
            f"Windows Steam is not installed in bottle {config['bottle']!r}. Install it through the selected CrossOver app, then retry.",
            66,
        )
    if not STEAM_ROOT.is_dir():
        raise SetupError("Native macOS Steam has not been run for this user yet.", 66)
    if require_appinfo and not paths["appinfo"].is_file():
        raise SetupError("Native Steam's appinfo cache is missing. Launch Steam once, quit it, and retry.", 66)
    return paths


def record_original(path: Path, state: MutableMapping[str, Any]) -> None:
    files = state.setdefault("files", {})
    key = os.fspath(path)
    if key in files:
        return
    record: dict[str, Any] = {"existed": path.exists() or path.is_symlink()}
    if path.is_symlink():
        record.update({"kind": "symlink", "target": os.readlink(path)})
    elif path.is_file():
        digest = sha256_file(path)
        backup = BACKUP_ROOT / "originals" / f"{digest}-{path.name}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(path, backup)
        record.update(
            {
                "kind": "file",
                "sha256": digest,
                "backup": os.fspath(backup),
                "mode": stat.S_IMODE(path.stat().st_mode),
            }
        )
    else:
        record["kind"] = "missing"
    files[key] = record


class VDFParser:
    """Small KeyValues parser for Steam's text library configuration."""

    def __init__(self, text: str):
        self.text = text
        self.offset = 0

    def tokens(self) -> Iterable[str]:
        while self.offset < len(self.text):
            character = self.text[self.offset]
            if character.isspace():
                self.offset += 1
                continue
            if self.text.startswith("//", self.offset):
                newline = self.text.find("\n", self.offset)
                self.offset = len(self.text) if newline < 0 else newline + 1
                continue
            if character in "{}":
                self.offset += 1
                yield character
                continue
            if character == '"':
                self.offset += 1
                result: list[str] = []
                while self.offset < len(self.text):
                    character = self.text[self.offset]
                    self.offset += 1
                    if character == '"':
                        break
                    if character == "\\" and self.offset < len(self.text):
                        following = self.text[self.offset]
                        if following in ('"', "\\"):
                            result.append(following)
                            self.offset += 1
                            continue
                    result.append(character)
                else:
                    raise SetupError("Unterminated quoted string in libraryfolders.vdf", 65)
                yield "".join(result)
                continue
            start = self.offset
            while self.offset < len(self.text) and not self.text[self.offset].isspace() and self.text[self.offset] not in "{}":
                self.offset += 1
            yield self.text[start:self.offset]


def parse_vdf(data: bytes) -> OrderedDict[str, Any]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SetupError(f"libraryfolders.vdf is not UTF-8: {error}", 65) from error
    tokens = iter(VDFParser(text).tokens())

    def parse_object(stop_at_brace: bool) -> OrderedDict[str, Any]:
        result: OrderedDict[str, Any] = OrderedDict()
        while True:
            try:
                key = next(tokens)
            except StopIteration:
                if stop_at_brace:
                    raise SetupError("Missing closing brace in libraryfolders.vdf", 65)
                return result
            if key == "}":
                if stop_at_brace:
                    return result
                raise SetupError("Unexpected closing brace in libraryfolders.vdf", 65)
            if key == "{":
                raise SetupError("Unexpected opening brace in libraryfolders.vdf", 65)
            try:
                value = next(tokens)
            except StopIteration as error:
                raise SetupError(f"Missing value for VDF key {key!r}", 65) from error
            result[key] = parse_object(True) if value == "{" else value

    return parse_object(False)


def quote_vdf(value: Any) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def encode_vdf(data: Mapping[str, Any], depth: int = 0) -> bytes:
    lines: list[str] = []
    indentation = "\t" * depth
    for key, value in data.items():
        if isinstance(value, Mapping):
            lines.extend((f"{indentation}{quote_vdf(key)}", f"{indentation}{{"))
            lines.append(encode_vdf(value, depth + 1).decode("utf-8").rstrip("\n"))
            lines.append(f"{indentation}}}")
        else:
            lines.append(f"{indentation}{quote_vdf(key)}\t\t{quote_vdf(value)}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def canonical_path(value: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.expanduser(os.fspath(value))))


def ensure_library_entry(path: Path, shared_root: Path, state: MutableMapping[str, Any], label: str) -> bool:
    windows_library_file = shared_root / "steamapps/libraryfolders.vdf"
    if not windows_library_file.is_file() or windows_library_file.is_symlink():
        raise SetupError(f"Windows Steam library metadata is missing or unsafe: {windows_library_file}", 66)
    windows_data = parse_vdf(windows_library_file.read_bytes())
    windows_libraries = windows_data.get("libraryfolders")
    if not isinstance(windows_libraries, Mapping):
        raise SetupError(f"Unexpected Windows Steam library metadata: {windows_library_file}", 65)
    windows_entry = windows_libraries.get("0")
    if not isinstance(windows_entry, Mapping) or not windows_entry.get("contentid"):
        raise SetupError(f"Windows Steam's default library has no content identity: {windows_library_file}", 65)
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise SetupError(f"Refusing unsafe Steam library configuration: {path}", 65)
        data = parse_vdf(path.read_bytes())
    else:
        data = OrderedDict(
            {
                "libraryfolders": OrderedDict(
                    {
                        "0": OrderedDict(
                            {
                                "path": os.fspath(STEAM_ROOT),
                                "label": "",
                                "contentid": str(secrets.randbits(63)),
                                "totalsize": "0",
                                "update_clean_bytes_tally": "0",
                                "time_last_update_verified": "0",
                                "apps": OrderedDict(),
                            }
                        )
                    }
                )
            }
        )
    libraries = data.get("libraryfolders")
    if not isinstance(libraries, MutableMapping):
        raise SetupError(f"Unexpected libraryfolders.vdf structure: {path}", 65)
    expected = canonical_path(shared_root)
    matching_key: str | None = None
    for key, entry in libraries.items():
        if isinstance(entry, Mapping) and canonical_path(str(entry.get("path", ""))) == expected:
            matching_key = str(key)
            break
    changed = False
    added = False
    if matching_key is None:
        numeric_keys = [int(key) for key in libraries if str(key).isdigit()]
        matching_key = str(max(numeric_keys, default=-1) + 1)
        libraries[matching_key] = deepcopy(windows_entry)
        libraries[matching_key]["path"] = os.fspath(shared_root)
        libraries[matching_key]["label"] = label
        changed = True
        added = True
    entry = libraries[matching_key]
    if isinstance(entry, MutableMapping) and not str(entry.get("label", "")):
        entry["label"] = label
        changed = True
    if isinstance(entry, MutableMapping):
        # Both clients must agree that this is the same physical library. Keep
        # native Steam's host-volume totalsize, but synchronize the identity
        # and game/tally fields owned by Windows Steam.
        for key in ("contentid", "update_clean_bytes_tally", "time_last_update_verified", "apps"):
            if key in windows_entry and entry.get(key) != windows_entry[key]:
                entry[key] = deepcopy(windows_entry[key])
                changed = True
    state.setdefault("library_entries", {})[os.fspath(path)] = {
        "key": matching_key,
        "path": os.fspath(shared_root),
        "added_by_kaon": added or bool(state.get("library_entries", {}).get(os.fspath(path), {}).get("added_by_kaon")),
    }
    if changed:
        record_original(path, state)
        atomic_write(path, encode_vdf(data), 0o600)
    return changed


def ensure_platform_override(path: Path, state: MutableMapping[str, Any]) -> bool:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = original.splitlines()
    if any(line.strip() == PLATFORM_LINE for line in lines):
        state.setdefault("steam_dev", {}).setdefault("line_added_by_kaon", False)
        return False
    record_original(path, state)
    lines.append(PLATFORM_LINE)
    updated = "\n".join(lines) + "\n"
    atomic_write(path, updated.encode("utf-8"), 0o600)
    state.setdefault("steam_dev", {})["line_added_by_kaon"] = True
    return True


def install_file(source: Path, destination: Path, state: MutableMapping[str, Any], mode: int = 0o755) -> bool:
    desired = source.read_bytes()
    if destination.is_file() and not destination.is_symlink() and destination.read_bytes() == desired:
        state.setdefault("installed_hashes", {})[os.fspath(destination)] = sha256_bytes(desired)
        return False
    record_original(destination, state)
    atomic_write(destination, desired, mode)
    state.setdefault("installed_hashes", {})[os.fspath(destination)] = sha256_bytes(desired)
    return True


def restore_installed_file(path: Path, installed_hash: str, state: Mapping[str, Any]) -> str:
    """Restore a pre-install file, but only while Kaon's bytes are intact."""

    if path.is_symlink() or not path.is_file() or sha256_file(path) != installed_hash:
        return "diverged"
    original = state.get("files", {}).get(os.fspath(path), {})
    if not isinstance(original, Mapping):
        return "unknown"
    kind = original.get("kind")
    if kind == "missing" or not original.get("existed", True):
        tracked_unlink(path, missing_ok=False)
        return "removed"
    if kind == "symlink":
        atomic_symlink(path, str(original["target"]))
        return "restored"
    if kind == "file":
        backup = Path(str(original.get("backup", "")))
        if not backup.is_file() or sha256_file(backup) != original.get("sha256"):
            return "backup-missing"
        atomic_copy(backup, path, int(original.get("mode", 0o600)))
        return "restored"
    return "unknown"


def appinfo_module_path() -> Path:
    installed = SUPPORT_ROOT / "lib/appinfo.py"
    if installed.is_file():
        return installed
    return resource_path("steammetadataeditor/src/appinfo.py")


def install_runtime(state: MutableMapping[str, Any]) -> Path:
    ensure_private_directories()
    runtime = SUPPORT_ROOT / "bin/kaon-setup"
    library = SUPPORT_ROOT / "lib"
    appinfo_source = resource_path("steammetadataeditor/src/appinfo.py")
    install_file(appinfo_source, library / "appinfo.py", state, 0o600)
    for name in ("launch_crossover.sh", "launch_with_log.sh"):
        install_file(
            resource_path(f"resources/{name}"),
            library / name,
            state,
            0o755,
        )
    if getattr(sys, "frozen", False):
        install_file(Path(sys.executable), runtime, state, 0o755)
    else:
        install_file(Path(__file__).resolve(), library / "kaon_setup.py", state, 0o600)
        repair_source = Path(__file__).resolve().with_name("kaon_repair.py")
        if not repair_source.is_file():
            repair_source = resource_path("engine/kaon_repair.py")
        install_file(repair_source, library / "kaon_repair.py", state, 0o600)
        interpreter = Path(sys.executable).resolve()
        wrapper = (
            "#!/bin/zsh\nset -eu\n"
            f"exec {shell_quote(os.fspath(interpreter))} {shell_quote(os.fspath(library / 'kaon_setup.py'))} \"$@\"\n"
        ).encode("utf-8")
        if not runtime.is_file() or runtime.read_bytes() != wrapper:
            record_original(runtime, state)
            atomic_write(runtime, wrapper, 0o755)
        state.setdefault("installed_hashes", {})[os.fspath(runtime)] = sha256_bytes(wrapper)
    return runtime


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def install_launchers(paths: Mapping[str, Path], state: MutableMapping[str, Any]) -> list[str]:
    destination = paths["shared_steamapps"] / "common/Kaon"
    destination.mkdir(parents=True, exist_ok=True)
    changed: list[str] = []
    for name in ("launch_crossover.sh", "launch_with_log.sh"):
        source = resource_path(f"resources/{name}")
        target = destination / name
        if install_file(source, target, state, 0o755):
            changed.append(os.fspath(target))
    return changed


def repair_launch_entries(config: Mapping[str, Any], paths: Mapping[str, Path], check: bool = False) -> Any:
    library = SUPPORT_ROOT / "lib"
    for module_directory in (Path(__file__).resolve().parent, library):
        if os.fspath(module_directory) not in sys.path:
            sys.path.insert(0, os.fspath(module_directory))
    try:
        import kaon_repair  # type: ignore
    except ImportError as error:
        raise SetupError(f"Kaon's launch repair module is unavailable: {error}", 69) from error
    description = MANAGED_DESCRIPTION_TEMPLATE.format(name=cross_over_display_name(config))
    try:
        result = kaon_repair.repair(
            paths["appinfo"],
            paths["shared_steamapps"],
            appinfo_module_path(),
            BACKUP_ROOT / "autoheal",
            str(config["bottle"]),
            description,
            check=check,
        )
        if not check:
            if result.appinfo_postimage is not None:
                digest, mode = result.appinfo_postimage
                _record_active_path_mutation(
                    paths["appinfo"], ("file", digest, int(mode))
                )
            if result.state_postimage is not None:
                digest, mode = result.state_postimage
                _record_active_path_mutation(
                    SUPPORT_ROOT / "state/managed-apps.json",
                    ("file", digest, int(mode)),
                )
        return result
    except kaon_repair.KaonRepairError as error:
        raise SetupError(str(error), getattr(error, "exit_code", 70)) from error


def remove_launch_entries(paths: Mapping[str, Path], check: bool = False) -> Any:
    library = SUPPORT_ROOT / "lib"
    for module_directory in (Path(__file__).resolve().parent, library):
        if os.fspath(module_directory) not in sys.path:
            sys.path.insert(0, os.fspath(module_directory))
    import kaon_repair  # type: ignore
    try:
        return kaon_repair.remove(
            paths["appinfo"],
            appinfo_module_path(),
            BACKUP_ROOT / "autoheal",
            check=check,
        )
    except kaon_repair.KaonRepairError as error:
        raise SetupError(str(error), getattr(error, "exit_code", 70)) from error


def agent_plist(label: str, arguments: Sequence[str], log_stem: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": list(arguments),
        "LimitLoadToSessionType": "Aqua",
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "ThrottleInterval": 30,
        "Umask": 63,
        "StandardOutPath": os.fspath(LOG_ROOT / f"{log_stem}.out.log"),
        "StandardErrorPath": os.fspath(LOG_ROOT / f"{log_stem}.err.log"),
    }
    base.update(extra)
    return base


def service_loaded(label: str) -> bool:
    domain = f"gui/{os.getuid()}"
    return run(("/bin/launchctl", "print", f"{domain}/{label}")).returncode == 0


def bootout_agent(label: str, path: Path) -> None:
    domain = f"gui/{os.getuid()}"
    if service_loaded(label):
        run(("/bin/launchctl", "bootout", domain, path))


def install_agent(label: str, plist: Mapping[str, Any], enabled: bool, state: MutableMapping[str, Any]) -> None:
    path = LAUNCH_AGENT_ROOT / f"{label}.plist"
    bootout_agent(label, path)
    if not enabled:
        tracked_unlink(path)
        return
    record_original(path, state)
    atomic_write(path, plistlib.dumps(dict(plist), fmt=plistlib.FMT_XML, sort_keys=False), 0o600)
    lint = run(("/usr/bin/plutil", "-lint", path))
    if lint.returncode != 0:
        raise SetupError(f"Generated invalid launch agent {path}: {lint.stderr.decode(errors='replace')}", 65)
    domain = f"gui/{os.getuid()}"
    run(("/bin/launchctl", "enable", f"{domain}/{label}"))
    result = run(("/bin/launchctl", "bootstrap", domain, path))
    if result.returncode != 0:
        raise SetupError(f"Could not load {label}: {result.stderr.decode(errors='replace').strip()}", 70)


def configure_agents(config: Mapping[str, Any], paths: Mapping[str, Path], runtime: Path, state: MutableMapping[str, Any]) -> None:
    autoheal = agent_plist(
        LABELS["autoheal"],
        (os.fspath(runtime), "guard", "--yes"),
        "autoheal",
        WatchPaths=[os.fspath(paths["appinfo"]), os.fspath(paths["shared_steamapps"])],
        StartInterval=60,
    )
    background = agent_plist(
        LABELS["steam"],
        (os.fspath(runtime), "ensure-steam", "--yes"),
        "crossover-steam",
        AbandonProcessGroup=True,
        ThrottleInterval=60,
    )
    tray = agent_plist(
        LABELS["tray"],
        (os.fspath(runtime), "tray-guard", "--yes"),
        "crossover-tray-guard",
        WatchPaths=[os.fspath(item) for item in tray_targets(paths)],
        StartInterval=300,
    )
    install_agent(LABELS["autoheal"], autoheal, bool(config["auto_repair"]), state)
    install_agent(LABELS["tray"], tray, bool(config["hide_tray"]), state)
    install_agent(LABELS["steam"], background, bool(config["start_at_login"]), state)


def disable_legacy_agents(state: MutableMapping[str, Any]) -> None:
    for label in LEGACY_LABELS:
        path = LAUNCH_AGENT_ROOT / f"{label}.plist"
        if not path.exists():
            continue
        record_original(path, state)
        bootout_agent(label, path)
        migrated = BACKUP_ROOT / "legacy-launchagents" / path.name
        migrated.parent.mkdir(parents=True, exist_ok=True)
        if not migrated.exists():
            shutil.copy2(path, migrated)
        tracked_unlink(path, missing_ok=False)


KNOWN_TRAY_PREFIXES = (
    bytes.fromhex("c7442428060300004889742420"),
    bytes.fromhex("c7442428060300004c89642420"),
)
TRAY_SUFFIX = bytes.fromhex("83f8ff89c30f85")
TRAY_REPLACEMENT = bytes.fromhex("b80100000090")
ORIGINAL_MARKER = b"Wine builtin DLL"
PATCHED_MARKER = b"Kaon builtin DLL"
MARKER_OFFSET = 0x40


def vendor_explorer(config: Mapping[str, Any]) -> Path:
    wine_root = Path(str(config["crossover_app"])) / "Contents/SharedSupport/CrossOver/lib/wine"
    preferred = wine_root / "x86_64-windows/explorer.exe"
    if preferred.is_file() and not preferred.is_symlink():
        return preferred
    candidates = [item for item in wine_root.glob("*-windows/explorer.exe") if item.is_file() and not item.is_symlink()]
    if len(candidates) != 1:
        raise SetupError(f"Could not identify CrossOver's vendor Explorer in {wine_root}", 66)
    return candidates[0]


def tray_targets(paths: Mapping[str, Path]) -> tuple[Path, Path]:
    windows = paths["bottle_root"] / "drive_c/windows"
    return windows / "explorer.exe", windows / "system32/explorer.exe"


def derive_tray_patch(vendor: bytes) -> bytes:
    if not vendor.startswith(b"MZ") or vendor[MARKER_OFFSET:MARKER_OFFSET + len(ORIGINAL_MARKER)] != ORIGINAL_MARKER:
        raise SetupError("This CrossOver Explorer build has an unknown format; tray hiding was left disabled.", 65)
    matches: list[tuple[int, bytes]] = []
    for prefix in KNOWN_TRAY_PREFIXES:
        pattern = re.compile(re.escape(prefix) + b"\\xff\\x15...." + re.escape(TRAY_SUFFIX), re.DOTALL)
        matches.extend((match.start(), prefix) for match in pattern.finditer(vendor))
    if len(matches) != 1:
        raise SetupError(
            f"This CrossOver Explorer build has an unknown tray implementation ({len(matches)} known matches); no patch was installed.",
            65,
        )
    offset, prefix = matches[0]
    call_offset = offset + len(prefix)
    patched = bytearray(vendor)
    patched[call_offset:call_offset + len(TRAY_REPLACEMENT)] = TRAY_REPLACEMENT
    patched[MARKER_OFFSET:MARKER_OFFSET + len(PATCHED_MARKER)] = PATCHED_MARKER
    desired = bytes(patched)
    patched_pattern = re.compile(re.escape(prefix) + re.escape(TRAY_REPLACEMENT) + re.escape(TRAY_SUFFIX))
    if len(patched_pattern.findall(desired)) != 1:
        raise SetupError("Kaon's locally derived Explorer patch failed validation.", 70)
    return desired


def target_is_open(path: Path) -> bool:
    if not command_exists("lsof"):
        return False
    return run(("/usr/sbin/lsof", "-t", "--", path)).returncode == 0


def apply_tray_guard(config: Mapping[str, Any], paths: Mapping[str, Path], state: MutableMapping[str, Any]) -> dict[str, Any]:
    vendor_path = vendor_explorer(config)
    vendor = vendor_path.read_bytes()
    targets = tray_targets(paths)
    existing_tray = state.get("tray", {})
    existing_targets = (
        tuple(Path(item) for item in existing_tray.get("target_paths", ()))
        if isinstance(existing_tray, Mapping)
        else ()
    )
    if existing_targets and existing_targets != targets:
        restore_tray_guard(config, paths, state)
    try:
        desired = derive_tray_patch(vendor)
    except SetupError as error:
        restore_tray_guard(config, paths, state)
        return {"active": False, "degraded": True, "message": str(error)}
    desired_hash = sha256_bytes(desired)
    known_hashes = set(state.setdefault("tray", {}).get("patched_hashes", []))
    known_hashes.add(desired_hash)
    inspected: list[tuple[Path, bytes | None]] = []
    for target in targets:
        current = target.read_bytes() if target.exists() and target.is_file() else None
        inspected.append((target, current))

    tray_record = {
        "patched_hashes": sorted(known_hashes),
        "current_patched_hash": desired_hash,
        "vendor_hash": sha256_bytes(vendor),
        "vendor_path": os.fspath(vendor_path),
        "target_paths": [os.fspath(item) for item in targets],
        "bottle": str(config["bottle"]),
    }
    if any(current == desired for _, current in inspected):
        # Adopt a compatible patch from an older Kaon prototype before doing
        # any further work, so it remains removable even if the second target
        # is temporarily unavailable.
        state["tray"] = tray_record
        save_state(state)

    for target, current in inspected:
        if current is not None:
            current_hash = sha256_bytes(current)
            if current_hash not in known_hashes and current != vendor and not target.is_symlink():
                return {"active": False, "degraded": True, "message": f"Refusing to overwrite an unknown modified Explorer: {target}"}

    for target, current in inspected:
        if current == desired and not target.is_symlink():
            continue
        if target_is_open(target):
            return {"active": False, "degraded": True, "message": f"Tray repair deferred while {target} is in use."}

    state["tray"] = tray_record
    save_state(state)
    local_snapshot = MutationSnapshot(targets, ())
    try:
        for target, current in inspected:
            if current == desired and not target.is_symlink():
                continue
            record_original(target, state)
            atomic_write(target, desired, 0o755)
    except Exception as error:
        try:
            local_snapshot.rollback()
        except Exception as rollback_error:
            preserved = local_snapshot.preserve()
            raise SetupError(
                f"Tray protection failed ({error}); rollback also failed ({rollback_error}). Recovery snapshot: {preserved}"
            ) from error
        else:
            local_snapshot.close()
        raise SetupError(f"Tray protection could not be installed atomically: {error}") from error
    local_snapshot.close()
    save_state(state)
    return {"active": True, "degraded": False, "message": "Bottle-local Windows tray icons are hidden."}


def restore_tray_guard(config: Mapping[str, Any], paths: Mapping[str, Path], state: MutableMapping[str, Any]) -> None:
    tray_state = state.get("tray", {})
    known = set(tray_state.get("patched_hashes", [])) if isinstance(tray_state, Mapping) else set()
    if not known:
        return
    recorded_vendor = Path(str(tray_state.get("vendor_path", "")))
    selected_vendor = recorded_vendor if recorded_vendor.is_file() else vendor_explorer(config)
    vendor = selected_vendor.read_bytes()
    recorded_targets = tray_state.get("target_paths", ())
    targets = (
        tuple(Path(item) for item in recorded_targets)
        if isinstance(recorded_targets, list) and recorded_targets
        else tray_targets(paths)
    )
    for target in targets:
        if target.is_file() and sha256_file(target) in known:
            original = state.get("files", {}).get(os.fspath(target), {})
            if isinstance(original, Mapping) and original.get("kind") == "symlink":
                atomic_symlink(target, str(original["target"]))
            else:
                # Use the currently selected vendor build, never a stale
                # Explorer backup from an older CrossOver release.
                atomic_write(target, vendor, 0o755)
    state["tray"] = {}


def dock_export() -> dict[str, Any] | None:
    result = run(("/usr/bin/defaults", "export", "com.apple.dock", "-"))
    if result.returncode != 0:
        return None
    try:
        data = plistlib.loads(result.stdout)
    except plistlib.InvalidFileException:
        return None
    return data if isinstance(data, dict) else None


def dock_tile_path(tile: Mapping[str, Any]) -> Path | None:
    try:
        url = tile["tile-data"]["file-data"]["_CFURLString"]
    except (KeyError, TypeError):
        return None
    parsed = urlparse(str(url))
    return Path(unquote(parsed.path)) if parsed.scheme == "file" else None


def dock_import(data: Mapping[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(prefix="kaon-dock-", suffix=".plist")
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            plistlib.dump(dict(data), stream, fmt=plistlib.FMT_BINARY)
        result = run(("/usr/bin/defaults", "import", "com.apple.dock", path))
        if result.returncode != 0:
            raise SetupError(f"Could not update the Dock: {result.stderr.decode(errors='replace').strip()}")
        run(("/usr/bin/killall", "Dock"))
        _record_active_dock_mutation(data)
    finally:
        path.unlink(missing_ok=True)


def hide_crossover_dock_tile(config: Mapping[str, Any], state: MutableMapping[str, Any]) -> bool:
    selected = canonical_path(str(config["crossover_app"]))
    existing_state = state.get("dock")
    if (
        isinstance(existing_state, Mapping)
        and existing_state.get("app_path")
        and canonical_path(str(existing_state["app_path"])) != selected
    ):
        restore_crossover_dock_tile(state)
    data = dock_export()
    if data is None:
        return False
    tiles = data.get("persistent-apps")
    if not isinstance(tiles, list):
        return False
    for index, tile in enumerate(tiles):
        path = dock_tile_path(tile) if isinstance(tile, Mapping) else None
        if path is not None and canonical_path(path) == selected:
            _prepare_active_dock_mutation(deepcopy(data))
            removed = tiles.pop(index)
            encoded_tile = base64.b64encode(
                plistlib.dumps(removed, fmt=plistlib.FMT_BINARY)
            ).decode("ascii")
            state["dock"] = {
                "removed_tile_plist": encoded_tile,
                "index": index,
                "app_path": str(config["crossover_app"]),
            }
            dock_import(data)
            return True
    return False


def restore_crossover_dock_tile(state: MutableMapping[str, Any]) -> bool:
    dock_state = state.get("dock")
    if not isinstance(dock_state, Mapping) or "removed_tile_plist" not in dock_state:
        return False
    data = dock_export()
    if data is None:
        return False
    tiles = data.get("persistent-apps")
    if not isinstance(tiles, list):
        return False
    app_path = canonical_path(str(dock_state.get("app_path", "")))
    if any((path := dock_tile_path(tile)) is not None and canonical_path(path) == app_path for tile in tiles if isinstance(tile, Mapping)):
        state["dock"] = {}
        return False
    try:
        removed_tile = plistlib.loads(
            base64.b64decode(str(dock_state["removed_tile_plist"]), validate=True)
        )
    except (ValueError, plistlib.InvalidFileException) as error:
        raise SetupError(f"Kaon's saved Dock tile is invalid: {error}", 65) from error
    if not isinstance(removed_tile, Mapping):
        raise SetupError("Kaon's saved Dock tile has an invalid structure.", 65)
    _prepare_active_dock_mutation(deepcopy(data))
    index = min(int(dock_state.get("index", len(tiles))), len(tiles))
    tiles.insert(index, dict(removed_tile))
    dock_import(data)
    state["dock"] = {}
    return True


def _is_native_steam_path(path: Path) -> bool:
    candidate = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    root = Path(os.path.abspath(os.path.expanduser(os.fspath(STEAM_ROOT))))
    return candidate == root or root in candidate.parents


class MutationSnapshot:
    """Compare-and-restore rollback for one install or repair transaction."""

    def __init__(self, paths: Iterable[Path], labels: Iterable[str]):
        self._temporary_path: Path | None = Path(
            tempfile.mkdtemp(prefix="kaon-transaction-")
        )
        self._keep_temporary = False
        self._records: list[dict[str, Any]] = []
        self._record_by_path: dict[str, dict[str, Any]] = {}
        self._active = False
        seen: set[str] = set()
        try:
            for path in paths:
                key = os.path.normpath(os.path.expanduser(os.fspath(path)))
                if key in seen:
                    continue
                seen.add(key)
                before = path_fingerprint(path)
                if before[0] == "other":
                    raise SetupError(
                        f"Refusing to snapshot a non-file managed path: {path}", 65
                    )
                record: dict[str, Any] = {
                    "path": path,
                    "before": before,
                    "expected": None,
                }
                if before[0] == "file":
                    backup = self._temporary_path / f"{len(self._records):04d}.backup"
                    shutil.copy2(path, backup)
                    if path_fingerprint(path) != before:
                        raise SetupError(
                            f"Managed file changed while its rollback snapshot was created: {path}",
                            75,
                        )
                    record.update(
                        {
                            "kind": "file",
                            "backup": backup,
                            "mode": int(before[2]),
                        }
                    )
                elif before[0] == "symlink":
                    record.update({"kind": "symlink", "target": str(before[1])})
                else:
                    record["kind"] = "missing"
                self._records.append(record)
                self._record_by_path[key] = record
            self._labels = tuple(dict.fromkeys(labels))
            self._loaded = {label: service_loaded(label) for label in self._labels}
            self._dock_before = dock_export()
            self._dock_expected: dict[str, Any] | None = None
        except Exception:
            if self._temporary_path is not None:
                shutil.rmtree(self._temporary_path, ignore_errors=True)
                self._temporary_path = None
            raise
        _ACTIVE_MUTATION_SNAPSHOTS.append(self)
        self._active = True

    def record_path_mutation(
        self, path: Path, fingerprint: tuple[Any, ...] | None = None
    ) -> None:
        key = os.path.normpath(os.path.expanduser(os.fspath(path)))
        record = self._record_by_path.get(key)
        if record is not None:
            record["expected"] = fingerprint or path_fingerprint(path)

    def record_dock_mutation(self, data: Mapping[str, Any]) -> None:
        self._dock_expected = deepcopy(dict(data))

    def assert_dock_preimage(self, before: Mapping[str, Any]) -> None:
        expected = self._dock_expected
        if expected is None:
            expected = self._dock_before
        if before != expected:
            raise SetupError(
                "The Dock changed after Kaon's previous update; leaving it untouched.",
                75,
            )

    def rollback(self) -> None:
        conflicts: list[str] = []
        for label in self._labels:
            bootout_agent(label, LAUNCH_AGENT_ROOT / f"{label}.plist")
        for record in reversed(self._records):
            path = record["path"]
            before = record["before"]
            current = path_fingerprint(path)
            if current == before:
                continue
            expected = record.get("expected")
            if expected is None:
                conflicts.append(f"unclaimed change at {path}")
                continue
            if current != expected:
                conflicts.append(f"newer external change at {path}")
                continue
            if _is_native_steam_path(path) and native_steam_running():
                conflicts.append(f"native Steam is using {path}")
                continue
            kind = record["kind"]
            if kind == "missing":
                if path.is_symlink() or path.is_file():
                    tracked_unlink(path)
                elif path.exists():
                    conflicts.append(f"directory appeared at managed file path {path}")
            elif kind == "symlink":
                atomic_symlink(path, str(record["target"]))
            elif kind == "file":
                atomic_copy(record["backup"], path, int(record["mode"]))

        if self._dock_expected is not None:
            current_dock = dock_export()
            if current_dock == self._dock_before:
                pass
            elif current_dock != self._dock_expected:
                conflicts.append("the Dock changed after Kaon updated it")
            elif self._dock_before is None:
                conflicts.append("the original Dock state was unavailable")
            else:
                dock_import(self._dock_before)

        domain = f"gui/{os.getuid()}"
        for label, was_loaded in self._loaded.items():
            if not was_loaded:
                continue
            path = LAUNCH_AGENT_ROOT / f"{label}.plist"
            if path.is_file():
                run(("/bin/launchctl", "enable", f"{domain}/{label}"))
                restored = run(("/bin/launchctl", "bootstrap", domain, path))
                if restored.returncode != 0:
                    raise SetupError(
                        f"Rollback restored {path} but could not reload it: {restored.stderr.decode(errors='replace').strip()}"
                    )
        if conflicts:
            raise SetupError(
                "Rollback left newer or unverified changes untouched: "
                + "; ".join(conflicts),
                75,
            )

    def close(self) -> None:
        if self._active:
            try:
                _ACTIVE_MUTATION_SNAPSHOTS.remove(self)
            except ValueError:
                pass
            self._active = False
        if self._temporary_path is not None and not self._keep_temporary:
            shutil.rmtree(self._temporary_path, ignore_errors=True)
            self._temporary_path = None

    def preserve(self) -> Path:
        if self._temporary_path is None:
            raise SetupError("Transaction snapshot is no longer available.")
        self._keep_temporary = True
        destination = (
            BACKUP_ROOT
            / "failed-transactions"
            / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{os.getpid()}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(os.fspath(self._temporary_path), destination)
        self._temporary_path = None
        return destination


def transaction_paths(configurations: Iterable[Mapping[str, Any]]) -> list[Path]:
    paths = [
        CONFIG_PATH,
        STATE_PATH,
        SUPPORT_ROOT / "state/managed-apps.json",
        SUPPORT_ROOT / "bin/kaon-setup",
        SUPPORT_ROOT / "lib/kaon_setup.py",
        SUPPORT_ROOT / "lib/kaon_repair.py",
        SUPPORT_ROOT / "lib/appinfo.py",
        SUPPORT_ROOT / "lib/launch_crossover.sh",
        SUPPORT_ROOT / "lib/launch_with_log.sh",
        STEAM_ROOT / "steamapps/libraryfolders.vdf",
        STEAM_ROOT / "config/libraryfolders.vdf",
    ]
    for label in (*LABELS.values(), *LEGACY_LABELS):
        paths.append(LAUNCH_AGENT_ROOT / f"{label}.plist")
    for config in configurations:
        selected = steam_paths(config)
        paths.extend(
            (
                selected["appinfo"],
                selected["steam_dev"],
                selected["shared_steamapps"] / "common/Kaon/launch_crossover.sh",
                selected["shared_steamapps"] / "common/Kaon/launch_with_log.sh",
                *tray_targets(selected),
            )
        )
    return paths


def steam_processes(paths: Mapping[str, Path]) -> list[int]:
    result = run(("/usr/bin/pgrep", "-f", r"Steam[/\\]steam\.exe|steam\.exe"))
    if result.returncode != 0:
        return []
    candidates = [int(line) for line in result.stdout.decode().splitlines() if line.isdigit()]
    expected = canonical_path(paths["windows_steam"])
    matched: list[int] = []
    for pid in candidates:
        if command_exists("lsof"):
            opened = run(("/usr/sbin/lsof", "-a", "-p", str(pid), "-Fn"))
            names = [line[1:] for line in opened.stdout.decode(errors="replace").splitlines() if line.startswith("n")]
            if any(canonical_path(name) == expected for name in names):
                matched.append(pid)
                continue
        command = run(("/bin/ps", "-p", str(pid), "-o", "command="))
        if canonical_path(paths["shared_root"]) in canonical_path(command.stdout.decode(errors="replace")):
            matched.append(pid)
    return matched


def ensure_windows_steam(config: Mapping[str, Any], paths: Mapping[str, Path]) -> bool:
    with acquire_steam_start_lock():
        return _ensure_windows_steam_unlocked(config, paths)


def _ensure_windows_steam_unlocked(config: Mapping[str, Any], paths: Mapping[str, Path]) -> bool:
    if steam_processes(paths):
        return False
    wine = Path(str(config["crossover_app"])) / "Contents/SharedSupport/CrossOver/bin/wine"
    arguments = [
        os.fspath(wine), "--bottle", str(config["bottle"]), "--no-update", "--no-gui", "--no-wait",
    ]
    if config.get("hide_tray"):
        arguments.extend(("--dll", "explorer.exe=n,b"))
    arguments.extend(("--cx-app", STEAM_EXE_WINDOWS, "--", "-silent"))
    log_path = LOG_ROOT / "crossover-steam.out.log"
    if log_path.is_file() and log_path.stat().st_size > 5 * 1024 * 1024:
        older = log_path.with_suffix(log_path.suffix + ".1")
        older.unlink(missing_ok=True)
        log_path.replace(older)
    try:
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
    except OSError as error:
        raise SetupError(f"Windows Steam could not be launched: {error}", 75) from error
    for _ in range(80):
        if steam_processes(paths):
            return True
        if process.poll() not in (None, 0):
            break
        time.sleep(0.25)
    raise SetupError("Windows Steam did not start within 20 seconds; see ~/Library/Logs/Kaon/crossover-steam.out.log", 75)


def stop_windows_steam(paths: Mapping[str, Path]) -> int:
    processes = steam_processes(paths)
    for pid in processes:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and steam_processes(paths):
        time.sleep(0.25)
    return len(processes)


def guard_signature(paths: Mapping[str, Path]) -> str:
    """Return a cheap signature for the files that affect managed entries."""

    digest = hashlib.sha256()
    launcher_root = paths["shared_steamapps"] / "common/Kaon"
    watched_files = (
        paths["appinfo"],
        paths["steam_dev"],
        STEAM_ROOT / "steamapps/libraryfolders.vdf",
        STEAM_ROOT / "config/libraryfolders.vdf",
        launcher_root / "launch_crossover.sh",
        launcher_root / "launch_with_log.sh",
    )
    for path in watched_files:
        try:
            metadata = path.stat()
            signature = f"{path}:{metadata.st_mtime_ns}:{metadata.st_size}\n"
        except FileNotFoundError:
            signature = f"{path}:missing\n"
        digest.update(signature.encode())
    for manifest in sorted(paths["shared_steamapps"].glob("appmanifest_*.acf")):
        if manifest.is_file() and not manifest.is_symlink():
            metadata = manifest.stat()
            digest.update(
                f"{manifest.name}:{metadata.st_mtime_ns}:{metadata.st_size}\n".encode()
            )
    return digest.hexdigest()


def reconcile(config: Mapping[str, Any], state: MutableMapping[str, Any], include_agents: bool = True) -> dict[str, Any]:
    require_native_steam_closed()
    paths = preflight(config)
    runtime = install_runtime(state)
    changes: list[str] = []
    if native_steam_mutation(lambda: ensure_platform_override(paths["steam_dev"], state)):
        changes.append("native Steam Windows platform override")
    label = f"Shared {cross_over_display_name(config)} Library"
    for library_file in (STEAM_ROOT / "steamapps/libraryfolders.vdf", STEAM_ROOT / "config/libraryfolders.vdf"):
        if native_steam_mutation(
            lambda library_file=library_file: ensure_library_entry(
                library_file, paths["shared_root"], state, label
            )
        ):
            changes.append(os.fspath(library_file))
    changes.extend(install_launchers(paths, state))
    repair_result = native_steam_mutation(
        lambda: repair_launch_entries(config, paths, check=False)
    )
    if repair_result.changed:
        changes.append(f"{len(repair_result.changed_app_ids)} game launch entries")
    tray_status = {"active": False, "degraded": False, "message": "disabled"}
    if config.get("hide_tray"):
        tray_status = apply_tray_guard(config, paths, state)
    else:
        restore_tray_guard(config, paths, state)
    if config.get("hide_dock"):
        if hide_crossover_dock_tile(config, state):
            changes.append("CrossOver Dock pin")
    else:
        restore_crossover_dock_tile(state)
    if include_agents:
        # RunAtLoad agents may start as soon as launchctl accepts them, so make
        # the complete ownership ledger visible before bootstrapping.
        save_state(state)
        configure_agents(config, paths, runtime, state)
    state["guard_signature"] = guard_signature(paths)
    state["last_repair_at"] = utc_now()
    save_state(state)
    return {
        "changes": changes,
        "managed_games": len(repair_result.managed),
        "skipped_games": len(repair_result.skipped),
        "tray": tray_status,
    }


def build_config(args: argparse.Namespace, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}
    edition_flag = args.crossover_edition or previous.get("crossover_edition")
    edition_changed = bool(
        args.crossover_edition
        and previous.get("crossover_edition")
        and args.crossover_edition != previous.get("crossover_edition")
    )
    app_flag = args.crossover_app or (None if edition_changed else previous.get("crossover_app"))
    edition, app = choose_crossover(str(edition_flag) if edition_flag else None, str(app_flag) if app_flag else None)

    def selected(name: str, default: bool) -> bool:
        value = getattr(args, name)
        if value is not None:
            return bool(value)
        if name in previous:
            return bool(previous[name])
        return default

    return {
        "schema_version": SCHEMA_VERSION,
        "crossover_edition": edition,
        "crossover_app": os.fspath(app),
        "bottle": validate_bottle_name(args.bottle or str(previous.get("bottle", "Steam"))),
        "auto_repair": selected("auto_repair", True),
        "start_at_login": selected("start_at_login", True),
        "hide_dock": selected("hide_dock", False),
        "hide_tray": selected("hide_tray", False),
        "updated_at": utc_now(),
    }


def confirm_install(config: Mapping[str, Any], assume_yes: bool, quiet: bool = False) -> None:
    if not quiet:
        print(f"CrossOver: {cross_over_display_name(config)} at {config['crossover_app']}")
        print(f"Bottle: {config['bottle']}")
        print(f"Automatic repair: {'on' if config['auto_repair'] else 'off'}")
        print(f"Start Windows Steam at login: {'on' if config['start_at_login'] else 'off'}")
        print(f"Remove selected CrossOver Dock pin: {'yes' if config['hide_dock'] else 'no'}")
        print(f"Hide every Windows tray icon in this bottle: {'yes (experimental)' if config['hide_tray'] else 'no'}")
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise SetupError("Use --yes after reviewing the requested changes.", 64)
    response = input("Continue? [y/N] ").strip().lower()
    if response not in ("y", "yes"):
        raise SetupError("Setup cancelled.", 64)


def add_post_install_startup_result(
    config: Mapping[str, Any], result: MutableMapping[str, Any]
) -> None:
    """Start Windows Steam after commit without turning success into failure."""

    if not config["start_at_login"]:
        result["windows_steam_started"] = False
        return
    try:
        result["windows_steam_started"] = ensure_windows_steam(
            config, steam_paths(config)
        )
    except SetupError as error:
        result["windows_steam_started"] = False
        result.setdefault("warnings", []).append(
            "Kaon was installed successfully, but Windows Steam could not start "
            f"automatically: {error}. Automatic startup remains enabled; use "
            "Maintenance → Start Configured Windows Steam later and review "
            "~/Library/Logs/Kaon/crossover-steam.out.log if needed."
        )


def install_action(args: argparse.Namespace) -> dict[str, Any]:
    previous = load_config(required=False)
    config = build_config(args, previous)
    confirm_install(config, args.yes, quiet=args.json)
    require_native_steam_closed()
    preflight(config)
    ensure_private_directories()
    with acquire_setup_lock():
        configurations = [config]
        if previous.get("crossover_app") and previous.get("bottle"):
            configurations.append(previous)
        snapshot = MutationSnapshot(
            transaction_paths(configurations),
            (*LABELS.values(), *LEGACY_LABELS),
        )
        try:
            require_native_steam_closed()
            state = load_state()
            atomic_json(CONFIG_PATH, config)
            result = reconcile(config, state)
            # Retire the local prototype only after the replacement has
            # installed and validated successfully.
            disable_legacy_agents(state)
            save_state(state)
        except Exception as error:
            try:
                snapshot.rollback()
            except Exception as rollback_error:
                preserved = snapshot.preserve()
                raise SetupError(
                    f"Setup failed ({error}) and automatic rollback also failed ({rollback_error}). Recovery snapshot: {preserved}"
                ) from error
            raise
        finally:
            snapshot.close()
    add_post_install_startup_result(config, result)
    return result


def repair_action(args: argparse.Namespace) -> dict[str, Any]:
    previous = load_config()
    config = build_config(args, previous)
    require_native_steam_closed()
    preflight(config)
    with acquire_setup_lock():
        snapshot = MutationSnapshot(
            transaction_paths((previous, config)),
            (*LABELS.values(), *LEGACY_LABELS),
        )
        try:
            require_native_steam_closed()
            atomic_json(CONFIG_PATH, config)
            result = reconcile(config, load_state())
        except Exception as error:
            try:
                snapshot.rollback()
            except Exception as rollback_error:
                preserved = snapshot.preserve()
                raise SetupError(
                    f"Repair failed ({error}) and automatic rollback also failed ({rollback_error}). Recovery snapshot: {preserved}"
                ) from error
            raise
        finally:
            snapshot.close()
    return result


def status_action(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    config = dict(config or load_config(required=False))
    if not config:
        return {
            "configured": False,
            "guidance": "Run kaon-setup install or open Kaon Setup.app.",
            "detected": {
                edition: [os.fspath(path) for path in crossover_candidates(edition) if path.exists()]
                for edition in ("stable", "preview")
            },
        }
    paths = steam_paths(config)
    state = load_state()
    agents = {name: service_loaded(label) for name, label in LABELS.items()}
    result: dict[str, Any] = {
        "configured": True,
        "config": config,
        "native_steam_running": native_steam_running(),
        "windows_steam_pids": steam_processes(paths),
        "agents": agents,
        "platform_override": False,
        "shared_library": {},
        "launch_repair": None,
        "launchers": {},
        "visibility": {},
    }
    steam_dev = paths["steam_dev"]
    if steam_dev.is_file():
        result["platform_override"] = PLATFORM_LINE in [line.strip() for line in steam_dev.read_text(encoding="utf-8").splitlines()]
    expected = canonical_path(paths["shared_root"])
    for library_file in (STEAM_ROOT / "steamapps/libraryfolders.vdf", STEAM_ROOT / "config/libraryfolders.vdf"):
        present = False
        try:
            parsed = parse_vdf(library_file.read_bytes())
            entries = parsed.get("libraryfolders", {})
            present = any(isinstance(entry, Mapping) and canonical_path(str(entry.get("path", ""))) == expected for entry in entries.values())
        except (OSError, SetupError):
            pass
        result["shared_library"][os.fspath(library_file)] = present
    if not native_steam_running() and paths["appinfo"].is_file() and paths["shared_steamapps"].is_dir():
        try:
            check = repair_launch_entries(config, paths, check=True)
            result["launch_repair"] = {"ok": check.ok, "managed_games": len(check.managed), "missing_games": list(check.missing_app_ids)}
        except SetupError as error:
            result["launch_repair"] = {"ok": False, "error": str(error)}
    launcher_root = paths["shared_steamapps"] / "common/Kaon"
    for name in ("launch_crossover.sh", "launch_with_log.sh"):
        target = launcher_root / name
        try:
            result["launchers"][name] = target.is_file() and target.read_bytes() == resource_path(f"resources/{name}").read_bytes()
        except SetupError:
            result["launchers"][name] = False

    if config.get("hide_tray"):
        try:
            desired = derive_tray_patch(vendor_explorer(config).read_bytes())
            actual = [
                target.is_file()
                and not target.is_symlink()
                and target.read_bytes() == desired
                for target in tray_targets(paths)
            ]
            result["visibility"]["tray"] = {
                "enabled": True,
                "active": all(actual),
                "degraded": not all(actual),
                "targets": actual,
            }
        except (OSError, SetupError) as error:
            result["visibility"]["tray"] = {
                "enabled": True,
                "active": False,
                "degraded": True,
                "error": str(error),
            }
    else:
        result["visibility"]["tray"] = {"enabled": False, "active": False, "degraded": False}

    dock_data = dock_export()
    pinned: bool | None = None
    if dock_data is not None and isinstance(dock_data.get("persistent-apps"), list):
        selected = canonical_path(str(config["crossover_app"]))
        pinned = any(
            (tile_path := dock_tile_path(tile)) is not None
            and canonical_path(tile_path) == selected
            for tile in dock_data["persistent-apps"]
            if isinstance(tile, Mapping)
        )
    dock_state = state.get("dock", {})
    result["visibility"]["dock"] = {
        "background_launch_is_headless": True,
        "remove_pin_enabled": bool(config.get("hide_dock")),
        "selected_app_pinned": pinned,
        "kaon_recorded_removed_pin": bool(
            isinstance(dock_state, Mapping) and dock_state.get("removed_tile_plist")
        ),
    }
    return result


def uninstall_action(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config()
    if not args.yes:
        if args.json or not sys.stdin.isatty():
            raise SetupError("Use --yes after reviewing the uninstall changes.", 64)
        print("Kaon will remove its active integration while preserving games, the shared library, and safety backups.")
        if input("Continue with uninstall? [y/N] ").strip().lower() not in ("y", "yes"):
            raise SetupError("Uninstall cancelled.", 64)
    require_native_steam_closed()
    paths = steam_paths(config)
    state = load_state()
    warnings: list[str] = []
    for label in LABELS.values():
        agent = LAUNCH_AGENT_ROOT / f"{label}.plist"
        bootout_agent(label, agent)
        agent.unlink(missing_ok=True)
    removed_app_ids: list[int] = []
    diverged_app_ids: list[int] = []
    if paths["appinfo"].is_file():
        try:
            removal = remove_launch_entries(paths)
            removed_app_ids = list(removal.removed_app_ids)
            diverged_app_ids = list(removal.diverged_app_ids)
        except SetupError as error:
            warnings.append(f"Launch entries could not be removed: {error}")
    else:
        warnings.append("Native Steam's appinfo cache is missing; recorded Kaon launch entries were not removed.")
    try:
        restore_tray_guard(config, paths, state)
    except SetupError as error:
        warnings.append(f"Bottle Explorer could not be restored automatically: {error}")
    try:
        restore_crossover_dock_tile(state)
    except SetupError as error:
        warnings.append(f"The saved Dock tile could not be restored: {error}")
    removed_files: list[str] = []
    restored_files: list[str] = []
    installed_hashes = state.get("installed_hashes", {})
    for path_text, digest in list(installed_hashes.items()):
        path = Path(path_text)
        if path == SUPPORT_ROOT / "bin/kaon-setup" or SUPPORT_ROOT in path.parents:
            continue
        disposition = restore_installed_file(path, str(digest), state)
        if disposition == "removed":
            removed_files.append(path_text)
        elif disposition == "restored":
            restored_files.append(path_text)
        elif disposition != "unknown":
            warnings.append(f"Left {path} untouched during uninstall ({disposition}).")
    if state.get("steam_dev", {}).get("line_added_by_kaon") and paths["steam_dev"].is_file():
        def remove_platform_override() -> None:
            lines = paths["steam_dev"].read_text(encoding="utf-8").splitlines()
            remaining = [line for line in lines if line.strip() != PLATFORM_LINE]
            atomic_write(
                paths["steam_dev"],
                (("\n".join(remaining) + "\n") if remaining else "").encode(),
                0o600,
            )

        native_steam_mutation(remove_platform_override)
    state["uninstalled_at"] = utc_now()
    archive = BACKUP_ROOT / "uninstall-state" / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-install-state.json"
    atomic_json(archive, state)
    managed_apps_state = SUPPORT_ROOT / "state/managed-apps.json"
    if managed_apps_state.is_symlink():
        warnings.append(f"Removed an unsafe symbolic link at {managed_apps_state}.")
        managed_apps_state.unlink()
    elif managed_apps_state.is_file():
        managed_archive = archive.with_name(archive.stem + "-managed-apps.json")
        atomic_copy(managed_apps_state, managed_archive, 0o600)
        managed_apps_state.unlink()
        fsync_directory(managed_apps_state.parent)
    fresh_state = {
        "schema_version": SCHEMA_VERSION,
        "files": {},
        "library_entries": {},
        "last_uninstall_at": state["uninstalled_at"],
        "last_uninstall_archive": os.fspath(archive),
    }
    save_state(fresh_state)
    CONFIG_PATH.unlink(missing_ok=True)
    return {
        "removed_launch_entries": removed_app_ids,
        "diverged_launch_entries_left_untouched": diverged_app_ids,
        "removed_files": removed_files,
        "restored_preexisting_files": restored_files,
        "games_and_shared_library_preserved": True,
        "backups_preserved": True,
        "ownership_archive": os.fspath(archive),
        "warnings": warnings,
    }


def print_result(result: Mapping[str, Any], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True, default=os.fspath))
        return
    if not result.get("configured", True):
        print("Kaon is not configured.")
        for edition, paths in result.get("detected", {}).items():
            for path in paths:
                print(f"Detected {edition}: {path}")
        return
    for key, value in result.items():
        label = key.replace("_", " ").capitalize()
        print(f"{label}: {value}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="kaon-setup", description="Set up native Steam to install and launch Windows games through CrossOver.")
    result.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=("install", "repair", "status", "uninstall", "start-steam", "stop-steam", "ensure-steam", "guard", "tray-guard"),
    )
    result.add_argument("--crossover-edition", choices=("stable", "preview", "custom"))
    result.add_argument("--crossover-app")
    result.add_argument("--bottle")
    result.add_argument("--auto-repair", action=argparse.BooleanOptionalAction, default=None)
    result.add_argument("--start-at-login", action=argparse.BooleanOptionalAction, default=None)
    result.add_argument("--hide-dock", action=argparse.BooleanOptionalAction, default=None)
    result.add_argument("--hide-tray", action=argparse.BooleanOptionalAction, default=None)
    result.add_argument("--yes", action="store_true")
    result.add_argument("--json", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action == "install":
            result = install_action(args)
        elif args.action == "repair":
            result = repair_action(args)
        elif args.action == "status":
            result = status_action()
        elif args.action == "uninstall":
            with acquire_setup_lock():
                result = uninstall_action(args)
        elif args.action in ("start-steam", "ensure-steam"):
            config = load_config()
            paths = preflight(config, require_appinfo=False)
            result = {"started": ensure_windows_steam(config, paths), "pids": steam_processes(paths)}
        elif args.action == "stop-steam":
            config = load_config()
            paths = preflight(config, require_appinfo=False)
            result = {"stopped_processes": stop_windows_steam(paths)}
        elif args.action == "guard":
            if native_steam_running():
                return 0
            try:
                lock = acquire_setup_lock()
            except SetupError as error:
                if error.exit_code == 75:
                    return 0
                raise
            with lock:
                if native_steam_running():
                    return 0
                config = load_config()
                paths = preflight(config)
                state = load_state()
                signature = guard_signature(paths)
                if state.get("guard_signature") == signature:
                    return 0
                result = reconcile(config, state, include_agents=False)
        elif args.action == "tray-guard":
            try:
                lock = acquire_setup_lock()
            except SetupError as error:
                if error.exit_code == 75:
                    return 0
                raise
            with lock:
                config = load_config()
                paths = preflight(config, require_appinfo=False)
                state = load_state()
                result = apply_tray_guard(config, paths, state)
                save_state(state)
        else:
            raise AssertionError(args.action)
        if args.action == "guard":
            return 0
        if args.action == "tray-guard" and not result.get("degraded"):
            return 0
        print_result(result, args.json)
        return 0
    except SetupError as error:
        if args.json:
            print(json.dumps({"ok": False, "error": str(error), "exit_code": error.exit_code}, indent=2))
        else:
            print(f"Kaon setup: {error}", file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
