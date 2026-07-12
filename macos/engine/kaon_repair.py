# SPDX-License-Identifier: GPL-3.0-or-later
# Kaon - macOS Steam / CrossOver integration
# Copyright (C) 2026 Kaon contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
"""Safely reconcile Kaon launch entries in Steam's ``appinfo.vdf``.

The binary VDF codec is loaded from Kaon's GPL-3.0 Steam Metadata Editor
subtree.  This module deliberately keeps all mutation policy here: it works on
a same-directory staging copy, validates that only Kaon launch entries changed,
backs up the live cache by content hash, and atomically replaces the cache only
while native Steam is stopped and the source is unchanged.

Kaon ownership metadata is kept in Kaon's Application Support directory.  The
user's Steam Metadata Editor configuration is never read or modified.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence


LAUNCHER = "../Kaon/launch_with_log.sh"
KAON_LAUNCHERS = frozenset(
    {
        LAUNCHER,
        "../Kaon/launch_crossover.sh",
    }
)
STATE_VERSION = 1
STABILITY_DELAY = 2.0
MAX_BACKUPS = 20


class KaonRepairError(RuntimeError):
    """Base error for a repair which could not be completed safely."""

    exit_code = 70


class InvalidRepairInput(KaonRepairError):
    """A required file or directory is missing, unsafe, or incompatible."""

    exit_code = 66


class RepairDeferred(KaonRepairError):
    """A transient owner or race requires a later retry."""

    exit_code = 75


@dataclass(frozen=True)
class SkippedApp:
    app_id: int
    reason: str


@dataclass(frozen=True)
class ManagedApp:
    app_id: int
    name: str
    arguments: str


@dataclass(frozen=True)
class RepairResult:
    """Structured result returned by :func:`repair`."""

    managed: tuple[ManagedApp, ...]
    changed_app_ids: tuple[int, ...]
    missing_app_ids: tuple[int, ...]
    skipped: tuple[SkippedApp, ...]
    state_changed: bool = False
    check: bool = False
    appinfo_postimage: tuple[str, int] | None = None
    state_postimage: tuple[str, int] | None = None

    @property
    def ok(self) -> bool:
        return not self.missing_app_ids

    @property
    def changed(self) -> bool:
        return bool(self.changed_app_ids or self.state_changed)


@dataclass(frozen=True)
class RemoveResult:
    """Structured result returned by :func:`remove`."""

    removed_app_ids: tuple[int, ...]
    removable_app_ids: tuple[int, ...]
    diverged_app_ids: tuple[int, ...]
    state_changed: bool = False
    check: bool = False

    @property
    def ok(self) -> bool:
        return not self.diverged_app_ids

    @property
    def changed(self) -> bool:
        return bool(self.removed_app_ids or self.state_changed)


def _run_quietly(arguments: Sequence[str]) -> bool:
    try:
        result = subprocess.run(
            arguments,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def native_steam_running() -> bool:
    return _run_quietly(("/usr/bin/pgrep", "-x", "steam_osx"))


def metadata_editor_running() -> bool:
    return _run_quietly(
        (
            "/usr/bin/pgrep",
            "-f",
            r"(?:Steam-Metadata-Editor|steammetadataeditor)/.*(?:src/main\.py|steammetadataeditor)",
        )
    )


def installed_app_ids(steamapps: Path) -> list[int]:
    app_ids: set[int] = set()
    for manifest in steamapps.glob("appmanifest_*.acf"):
        match = re.fullmatch(r"appmanifest_(\d+)\.acf", manifest.name)
        if match and manifest.is_file():
            app_ids.add(int(match.group(1)))
    return sorted(app_ids)


def is_kaon_entry(option: object) -> bool:
    """Return true for launch entries owned by Kaon's installed launchers."""

    return isinstance(option, dict) and option.get("executable") in KAON_LAUNCHERS


def _description_conflict(option: object, description: str) -> bool:
    return (
        isinstance(option, dict)
        and option.get("description") == description
        and option.get("executable") not in KAON_LAUNCHERS
    )


def numeric_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def pick_original_launch(launch_options: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    explicit_windows: list[dict[str, Any]] = []
    unqualified: list[dict[str, Any]] = []
    for key in sorted(launch_options, key=numeric_sort_key):
        option = launch_options[key]
        if not isinstance(option, dict) or not option.get("executable"):
            continue
        config = option.get("config")
        oslist = config.get("oslist") if isinstance(config, Mapping) else None
        platforms = {
            platform.lower()
            for platform in re.split(r"[,\s]+", str(oslist or ""))
            if platform
        }
        if "windows" in platforms:
            explicit_windows.append(option)
        elif platforms.intersection({"macos", "linux"}):
            continue
        else:
            unqualified.append(option)
    if explicit_windows:
        return explicit_windows[0]
    if unqualified:
        return unqualified[0]
    raise ValueError("no usable Windows launch option")


def next_launch_key(launch_options: Mapping[str, object]) -> str:
    numeric_keys = [int(key) for key in launch_options if key.isdigit()]
    return str(max(numeric_keys, default=-1) + 1)


def quote_argument(argument: str) -> str:
    if not argument or any(character.isspace() for character in argument) or '"' in argument:
        return '"' + argument.replace('"', r'\"') + '"'
    return argument


def desired_entry(
    original: Mapping[str, Any], bottle: str, description: str
) -> dict[str, Any]:
    executable = original.get("executable")
    if not isinstance(executable, str) or not executable:
        raise ValueError("original launch option has no executable")

    arguments = f"{quote_argument(bottle)} {quote_argument(executable)}"
    original_arguments = original.get("arguments")
    if original_arguments:
        arguments += f" {original_arguments}"

    original_config = original.get("config")
    config = deepcopy(original_config) if isinstance(original_config, Mapping) else {}
    config["oslist"] = "windows"
    entry: dict[str, Any] = {
        "executable": LAUNCHER,
        "arguments": arguments,
        "description": description,
        "config": config,
    }
    working_directory = original.get("workingdir")
    if working_directory:
        entry["workingdir"] = working_directory
    return entry


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fsync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def owned_state_path(backup_dir: Path) -> Path:
    """Resolve the Kaon-owned state file adjacent to the backup hierarchy."""

    backup_dir = Path(backup_dir)
    if backup_dir.parent.name == "backups":
        kaon_root = backup_dir.parent.parent
    else:
        kaon_root = backup_dir.parent
    return kaon_root / "state" / "managed-apps.json"


def _load_owned_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "apps": {}}
    if path.is_symlink() or not path.is_file():
        raise InvalidRepairInput(f"refusing unsafe Kaon state path: {path}")
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise InvalidRepairInput(f"invalid Kaon ownership state: {path}: {error}") from error
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        raise InvalidRepairInput(f"unsupported Kaon ownership state: {path}")
    apps = data.get("apps")
    if not isinstance(apps, dict):
        raise InvalidRepairInput(f"invalid apps table in Kaon ownership state: {path}")
    return data


def _stage_json(path: Path, data: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.kaon-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _copy_backup_once(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    if hash_file(source) != hash_file(destination):
        destination.unlink(missing_ok=True)
        raise KaonRepairError(f"backup verification failed: {destination}")


def _prune_backups(backup_dir: Path, pattern: str) -> None:
    backups = sorted(backup_dir.glob(pattern), key=lambda item: item.stat().st_mtime)
    for old_backup in backups[:-MAX_BACKUPS]:
        old_backup.unlink(missing_ok=True)


def backup_files(vdf: Path, state_path: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")

    vdf_hash = hash_file(vdf)
    if not list(backup_dir.glob(f"*-{vdf_hash[:16]}-appinfo.vdf")):
        _copy_backup_once(
            vdf, backup_dir / f"{stamp}-{vdf_hash[:16]}-appinfo.vdf"
        )

    if state_path.exists():
        state_hash = hash_file(state_path)
        if not list(backup_dir.glob(f"*-{state_hash[:16]}-managed-apps.json")):
            _copy_backup_once(
                state_path,
                backup_dir / f"{stamp}-{state_hash[:16]}-managed-apps.json",
            )

    _prune_backups(backup_dir, "*-appinfo.vdf")
    _prune_backups(backup_dir, "*-managed-apps.json")


def _resolve_appinfo_file(module_path: Path) -> Path:
    module_path = Path(module_path).expanduser()
    if module_path.is_dir():
        module_path = module_path / "appinfo.py"
    if not module_path.is_file() or module_path.is_symlink():
        raise InvalidRepairInput(f"appinfo module is missing or unsafe: {module_path}")
    return module_path.resolve()


def _load_appinfo_module(module_path: Path) -> ModuleType:
    module_file = _resolve_appinfo_file(module_path)
    module_name = "kaon_appinfo_" + hashlib.sha256(
        os.fsencode(module_file)
    ).hexdigest()[:16]
    specification = importlib.util.spec_from_file_location(module_name, module_file)
    if specification is None or specification.loader is None:
        raise InvalidRepairInput(f"could not load appinfo module: {module_file}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    if not hasattr(module, "Appinfo"):
        raise InvalidRepairInput(f"appinfo module has no Appinfo class: {module_file}")
    return module


def _assert_safe_vdf(vdf: Path) -> None:
    if not vdf.exists():
        raise InvalidRepairInput(f"appinfo file is missing: {vdf}")
    metadata = vdf.lstat()
    if vdf.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise InvalidRepairInput("refusing a symlink or non-regular appinfo file")
    if metadata.st_uid != os.getuid():
        raise InvalidRepairInput("appinfo is not owned by the current user")


def _assert_safe_inputs(vdf: Path, shared_steamapps: Path) -> None:
    _assert_safe_vdf(vdf)
    if not shared_steamapps.is_dir() or shared_steamapps.is_symlink():
        raise InvalidRepairInput(f"shared Steam library is missing or unsafe: {shared_steamapps}")


def _assert_steam_closed() -> None:
    if native_steam_running():
        raise RepairDeferred("Mac Steam is running; repair deferred")
    if metadata_editor_running():
        raise RepairDeferred("Steam Metadata Editor is running; repair deferred")


def _stable_source_hash(vdf: Path) -> str:
    _assert_steam_closed()
    first_hash = hash_file(vdf)
    if STABILITY_DELAY:
        time.sleep(STABILITY_DELAY)
    _assert_steam_closed()
    if hash_file(vdf) != first_hash:
        raise RepairDeferred("appinfo changed while waiting for a stable source")
    return first_hash


def _acquire_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_stream = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        lock_stream.close()
        raise RepairDeferred("another Kaon repair is already active") from error
    return lock_stream


def _iter_kaon_keys(launch_options: Mapping[str, object]) -> Iterable[str]:
    for key, option in launch_options.items():
        if is_kaon_entry(option):
            yield key


def _build_owned_state(
    previous_state: Mapping[str, Any],
    managed: Sequence[ManagedApp],
    entries: Mapping[int, Mapping[str, Any]],
    bottle: str,
    description: str,
) -> dict[str, Any]:
    state = deepcopy(dict(previous_state))
    state["version"] = STATE_VERSION
    state_apps = state.setdefault("apps", {})
    if not isinstance(state_apps, dict):
        raise InvalidRepairInput("Kaon ownership state apps table is not a dictionary")
    for app in managed:
        state_apps[str(app.app_id)] = {
            "name": app.name,
            "bottle": bottle,
            "description": description,
            "entry": deepcopy(dict(entries[app.app_id])),
        }
    return state


def _format_conflicts(app_id: int, conflicts: Sequence[str]) -> KaonRepairError:
    return KaonRepairError(
        f"conflicting non-Kaon launch entries use the Kaon description for "
        f"app {app_id}: {', '.join(conflicts)}"
    )


def repair(
    vdf: Path | str,
    shared_steamapps: Path | str,
    appinfo_module_path: Path | str,
    backup_dir: Path | str,
    bottle: str,
    description: str,
    check: bool = False,
) -> RepairResult:
    """Check or repair Kaon launch entries.

    ``RepairDeferred`` means a background agent should retry later.  All other
    unsafe or incompatible inputs raise ``KaonRepairError`` without replacing
    the live appinfo cache.
    """

    vdf = Path(vdf).expanduser()
    shared_steamapps = Path(shared_steamapps).expanduser()
    backup_dir = Path(backup_dir).expanduser()
    if not bottle or "\x00" in bottle:
        raise InvalidRepairInput("bottle name must not be empty or contain NUL")
    if not description or "\x00" in description:
        raise InvalidRepairInput("description must not be empty or contain NUL")

    _assert_safe_inputs(vdf, shared_steamapps)
    appinfo_module = _load_appinfo_module(Path(appinfo_module_path))
    state_path = owned_state_path(backup_dir)
    lock_stream = _acquire_lock(backup_dir.parent / "kaon-autoheal.lock")
    temporary_vdf: Path | None = None
    staged_state: Path | None = None
    try:
        previous_state = _load_owned_state(state_path)
        source_hash = _stable_source_hash(vdf)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".appinfo.kaon-", suffix=".tmp", dir=vdf.parent
        )
        os.close(descriptor)
        temporary_vdf = Path(temporary_name)
        shutil.copy2(vdf, temporary_vdf)

        try:
            appinfo = appinfo_module.Appinfo(os.fspath(temporary_vdf))
        except Exception as error:
            raise InvalidRepairInput(
                f"could not parse appinfo cache: {error}"
            ) from error
        source_app_count = len(appinfo.parsedAppInfo)
        source_app_ids = set(appinfo.parsedAppInfo)
        source_sections = {
            app_id: deepcopy(app["sections"])
            for app_id, app in appinfo.parsedAppInfo.items()
        }

        changed_app_ids: list[int] = []
        missing_app_ids: list[int] = []
        managed: list[ManagedApp] = []
        managed_baselines: dict[int, dict[str, Any]] = {}
        desired_entries: dict[int, dict[str, Any]] = {}
        skipped: list[SkippedApp] = []

        for app_id in installed_app_ids(shared_steamapps):
            app = appinfo.parsedAppInfo.get(app_id)
            if app is None:
                skipped.append(SkippedApp(app_id, "not present in native appinfo cache"))
                continue

            current_sections = deepcopy(app["sections"])
            sections = deepcopy(current_sections)
            metadata = sections.get("appinfo", {})
            common = metadata.get("common", {})
            app_name = str(common.get("name", app_id))
            if str(common.get("type", "")).lower() != "game":
                skipped.append(
                    SkippedApp(app_id, f"type is {common.get('type', 'unknown')}")
                )
                continue

            launch_options = metadata.get("config", {}).get("launch")
            if not isinstance(launch_options, dict) or not launch_options:
                skipped.append(SkippedApp(app_id, "no usable launch menu"))
                continue

            conflicts = [
                key
                for key, option in launch_options.items()
                if _description_conflict(option, description)
            ]
            if conflicts:
                raise _format_conflicts(app_id, conflicts)

            existing_keys = sorted(
                _iter_kaon_keys(launch_options), key=numeric_sort_key
            )
            preferred_key = existing_keys[0] if existing_keys else None
            for key in existing_keys:
                del launch_options[key]

            baseline_sections = deepcopy(sections)
            try:
                original = pick_original_launch(launch_options)
            except ValueError as error:
                skipped.append(SkippedApp(app_id, str(error)))
                continue

            entry = desired_entry(original, bottle, description)
            launch_key = preferred_key or next_launch_key(launch_options)
            launch_options[launch_key] = entry
            modified_sections = deepcopy(sections)
            managed_baselines[app_id] = baseline_sections
            desired_entries[app_id] = entry
            managed.append(ManagedApp(app_id, app_name, entry["arguments"]))

            if current_sections != modified_sections:
                if check:
                    missing_app_ids.append(app_id)
                else:
                    app["sections"] = modified_sections
                    changed_app_ids.append(app_id)

        result_for_check = RepairResult(
            managed=tuple(managed),
            changed_app_ids=(),
            missing_app_ids=tuple(missing_app_ids),
            skipped=tuple(skipped),
            state_changed=False,
            check=True,
        )
        if check:
            _assert_steam_closed()
            if hash_file(vdf) != source_hash:
                raise RepairDeferred("appinfo changed during check")
            return result_for_check

        desired_state = _build_owned_state(
            previous_state, managed, desired_entries, bottle, description
        )
        state_changed = desired_state != previous_state

        if changed_app_ids:
            for app_id in changed_app_ids:
                appinfo.update_app(app_id)
            appinfo.write_data()
            fsync_file(temporary_vdf)

            try:
                verified = appinfo_module.Appinfo(os.fspath(temporary_vdf))
            except Exception as error:
                raise KaonRepairError(
                    f"could not validate staged appinfo cache: {error}"
                ) from error
            if (
                len(verified.parsedAppInfo) != source_app_count
                or set(verified.parsedAppInfo) != source_app_ids
            ):
                raise KaonRepairError("app count changed while staging repaired metadata")

            managed_ids = {item.app_id for item in managed}
            for app_id in source_app_ids - managed_ids:
                if verified.parsedAppInfo[app_id]["sections"] != source_sections[app_id]:
                    raise KaonRepairError(
                        f"non-managed app metadata changed for app {app_id}"
                    )

            for item in managed:
                app_id = item.app_id
                verified_sections = deepcopy(
                    verified.parsedAppInfo[app_id]["sections"]
                )
                launch_options = verified_sections["appinfo"]["config"]["launch"]
                matches = [
                    option
                    for option in launch_options.values()
                    if is_kaon_entry(option)
                ]
                if len(matches) != 1 or matches[0] != desired_entries[app_id]:
                    raise KaonRepairError(
                        f"staged Kaon launch entry failed validation for app {app_id}"
                    )
                for key in list(_iter_kaon_keys(launch_options)):
                    del launch_options[key]
                if verified_sections != managed_baselines[app_id]:
                    raise KaonRepairError(
                        f"non-Kaon metadata changed for managed app {app_id}"
                    )

        if state_changed:
            staged_state = _stage_json(state_path, desired_state)

        _assert_steam_closed()
        if hash_file(vdf) != source_hash:
            raise RepairDeferred("source appinfo changed before atomic install")

        if changed_app_ids or state_changed:
            backup_files(vdf, state_path, backup_dir)
            _assert_steam_closed()
            if hash_file(vdf) != source_hash:
                raise RepairDeferred("source appinfo changed during backup")

        if changed_app_ids:
            os.replace(temporary_vdf, vdf)
            temporary_vdf = None
            fsync_directory(vdf.parent)

        if staged_state is not None:
            os.replace(staged_state, state_path)
            staged_state = None
            fsync_directory(state_path.parent)

        _assert_steam_closed()
        appinfo_postimage = (
            (hash_file(vdf), stat.S_IMODE(vdf.stat().st_mode))
            if changed_app_ids
            else None
        )
        state_postimage = (
            (hash_file(state_path), stat.S_IMODE(state_path.stat().st_mode))
            if state_changed
            else None
        )

        return RepairResult(
            managed=tuple(managed),
            changed_app_ids=tuple(changed_app_ids),
            missing_app_ids=(),
            skipped=tuple(skipped),
            state_changed=state_changed,
            check=False,
            appinfo_postimage=appinfo_postimage,
            state_postimage=state_postimage,
        )
    finally:
        if temporary_vdf is not None:
            temporary_vdf.unlink(missing_ok=True)
        if staged_state is not None:
            staged_state.unlink(missing_ok=True)
        lock_stream.close()


def _state_record_entry(app_id: int, record: object) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise InvalidRepairInput(
            f"invalid Kaon ownership record for app {app_id}: expected a dictionary"
        )
    entry = record.get("entry")
    if not isinstance(entry, dict):
        raise InvalidRepairInput(
            f"invalid Kaon ownership record for app {app_id}: entry is missing"
        )
    description = record.get("description")
    if entry.get("executable") not in KAON_LAUNCHERS or entry.get(
        "description"
    ) != description:
        raise InvalidRepairInput(
            f"invalid Kaon ownership record for app {app_id}: "
            "launcher or description does not match"
        )
    return entry


def remove(
    vdf: Path | str,
    appinfo_module_path: Path | str,
    backup_dir: Path | str,
    check: bool = False,
) -> RemoveResult:
    """Remove only launch entries that exactly match Kaon's ownership state.

    A Kaon-like entry that differs from the recorded dictionary is never
    removed.  Its app id is returned in ``diverged_app_ids`` for the installer
    to explain or handle interactively.
    """

    vdf = Path(vdf).expanduser()
    backup_dir = Path(backup_dir).expanduser()
    _assert_safe_vdf(vdf)
    appinfo_module = _load_appinfo_module(Path(appinfo_module_path))
    state_path = owned_state_path(backup_dir)

    lock_stream = _acquire_lock(backup_dir.parent / "kaon-autoheal.lock")
    temporary_vdf: Path | None = None
    staged_state: Path | None = None
    try:
        previous_state = _load_owned_state(state_path)
        previous_apps = previous_state.get("apps", {})
        if not isinstance(previous_apps, dict):
            raise InvalidRepairInput("Kaon ownership state apps table is invalid")

        source_hash = _stable_source_hash(vdf)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".appinfo.kaon-remove-", suffix=".tmp", dir=vdf.parent
        )
        os.close(descriptor)
        temporary_vdf = Path(temporary_name)
        shutil.copy2(vdf, temporary_vdf)

        try:
            appinfo = appinfo_module.Appinfo(os.fspath(temporary_vdf))
        except Exception as error:
            raise InvalidRepairInput(
                f"could not parse appinfo cache for removal: {error}"
            ) from error
        source_app_count = len(appinfo.parsedAppInfo)
        source_app_ids = set(appinfo.parsedAppInfo)
        source_sections = {
            app_id: deepcopy(app["sections"])
            for app_id, app in appinfo.parsedAppInfo.items()
        }
        desired_sections: dict[int, dict[str, Any]] = {}
        removed_app_ids: list[int] = []
        removable_app_ids: list[int] = []
        diverged_app_ids: list[int] = []
        state_after = deepcopy(previous_state)
        state_after_apps = state_after["apps"]

        for app_id_text, record in sorted(
            previous_apps.items(),
            key=lambda item: numeric_sort_key(str(item[0])),
        ):
            try:
                app_id = int(app_id_text)
            except (TypeError, ValueError) as error:
                raise InvalidRepairInput(
                    f"invalid app id in Kaon ownership state: {app_id_text!r}"
                ) from error
            expected_entry = _state_record_entry(app_id, record)
            app = appinfo.parsedAppInfo.get(app_id)
            if app is None:
                # There is no current metadata from which an owned entry could
                # be removed, so the stale ownership record can be retired.
                del state_after_apps[str(app_id_text)]
                continue

            sections = deepcopy(app["sections"])
            launch_options = (
                sections.get("appinfo", {}).get("config", {}).get("launch")
            )
            if not isinstance(launch_options, dict):
                del state_after_apps[str(app_id_text)]
                continue

            exact_keys = [
                key for key, option in launch_options.items() if option == expected_entry
            ]
            if not exact_keys:
                if any(is_kaon_entry(option) for option in launch_options.values()):
                    diverged_app_ids.append(app_id)
                else:
                    del state_after_apps[str(app_id_text)]
                continue

            removable_app_ids.append(app_id)
            for key in exact_keys:
                del launch_options[key]
            desired_sections[app_id] = sections
            if not check:
                app["sections"] = sections
                removed_app_ids.append(app_id)
                del state_after_apps[str(app_id_text)]

        if check:
            _assert_steam_closed()
            if hash_file(vdf) != source_hash:
                raise RepairDeferred("appinfo changed during removal check")
            return RemoveResult(
                removed_app_ids=(),
                removable_app_ids=tuple(removable_app_ids),
                diverged_app_ids=tuple(diverged_app_ids),
                state_changed=False,
                check=True,
            )

        state_changed = state_after != previous_state
        if removed_app_ids:
            for app_id in removed_app_ids:
                appinfo.update_app(app_id)
            appinfo.write_data()
            fsync_file(temporary_vdf)

            try:
                verified = appinfo_module.Appinfo(os.fspath(temporary_vdf))
            except Exception as error:
                raise KaonRepairError(
                    f"could not validate staged appinfo removal: {error}"
                ) from error
            if (
                len(verified.parsedAppInfo) != source_app_count
                or set(verified.parsedAppInfo) != source_app_ids
            ):
                raise KaonRepairError(
                    "app count changed while staging Kaon entry removal"
                )
            removed_ids = set(removed_app_ids)
            for app_id in source_app_ids - removed_ids:
                if verified.parsedAppInfo[app_id]["sections"] != source_sections[app_id]:
                    raise KaonRepairError(
                        f"non-target app metadata changed while removing app {app_id}"
                    )
            for app_id in removed_app_ids:
                if verified.parsedAppInfo[app_id]["sections"] != desired_sections[app_id]:
                    raise KaonRepairError(
                        f"staged Kaon entry removal failed validation for app {app_id}"
                    )

        if state_changed:
            staged_state = _stage_json(state_path, state_after)

        _assert_steam_closed()
        if hash_file(vdf) != source_hash:
            raise RepairDeferred("source appinfo changed before removal install")

        if removed_app_ids or state_changed:
            backup_files(vdf, state_path, backup_dir)
            _assert_steam_closed()
            if hash_file(vdf) != source_hash:
                raise RepairDeferred("source appinfo changed during removal backup")

        if removed_app_ids:
            os.replace(temporary_vdf, vdf)
            temporary_vdf = None
            fsync_directory(vdf.parent)

        if staged_state is not None:
            os.replace(staged_state, state_path)
            staged_state = None
            fsync_directory(state_path.parent)

        _assert_steam_closed()

        return RemoveResult(
            removed_app_ids=tuple(removed_app_ids),
            removable_app_ids=tuple(removable_app_ids),
            diverged_app_ids=tuple(diverged_app_ids),
            state_changed=state_changed,
            check=False,
        )
    finally:
        if temporary_vdf is not None:
            temporary_vdf.unlink(missing_ok=True)
        if staged_state is not None:
            staged_state.unlink(missing_ok=True)
        lock_stream.close()


def _print_result(result: RepairResult) -> None:
    if result.check:
        for app_id in result.missing_app_ids:
            managed = next(item for item in result.managed if item.app_id == app_id)
            print(f"missing: {app_id} {managed.name}")
    else:
        changed = set(result.changed_app_ids)
        for item in result.managed:
            marker = "repaired" if item.app_id in changed else "verified"
            print(f"{marker}: {item.app_id} {item.name}: {item.arguments}")
    for item in result.skipped:
        print(f"skipped: {item.app_id} ({item.reason})")
    protected = len(result.managed) - len(result.missing_app_ids)
    action = "check" if result.check else "repair"
    print(f"Kaon {action}: {protected}/{len(result.managed)} installed games protected")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vdf", type=Path, required=True)
    parser.add_argument("--shared-steamapps", type=Path, required=True)
    parser.add_argument("--appinfo-module-path", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--bottle", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        result = repair(
            vdf=arguments.vdf,
            shared_steamapps=arguments.shared_steamapps,
            appinfo_module_path=arguments.appinfo_module_path,
            backup_dir=arguments.backup_dir,
            bottle=arguments.bottle,
            description=arguments.description,
            check=arguments.check,
        )
    except KaonRepairError as error:
        print(f"Kaon repair: {error}", file=sys.stderr)
        return error.exit_code
    _print_result(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
