# SPDX-License-Identifier: GPL-3.0-or-later
# Kaon - macOS Steam / CrossOver integration
# Copyright (C) 2026 Kaon contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
sys.path.insert(0, str(ENGINE_DIR))

import kaon_repair  # noqa: E402  pylint: disable=wrong-import-position


FAKE_APPINFO_MODULE = r'''
import json


class Appinfo:
    def __init__(self, vdf_path, choose_apps=False, apps=None):
        self.vdf_path = vdf_path
        with open(vdf_path, encoding="utf-8") as stream:
            parsed = json.load(stream)
        parsed = {int(app_id): value for app_id, value in parsed.items()}
        if choose_apps:
            parsed = {app_id: parsed[app_id] for app_id in apps}
        self.parsedAppInfo = parsed

    def update_app(self, app_id):
        assert app_id in self.parsedAppInfo

    def write_data(self):
        with open(self.vdf_path, "w", encoding="utf-8") as stream:
            json.dump(self.parsedAppInfo, stream, sort_keys=True)
'''


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RepairTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.vdf = self.root / "Steam" / "appcache" / "appinfo.vdf"
        self.vdf.parent.mkdir(parents=True)
        self.shared_steamapps = self.root / "CrossOver" / "Steam" / "steamapps"
        self.shared_steamapps.mkdir(parents=True)
        (self.shared_steamapps / "appmanifest_100.acf").write_text(
            '"AppState" { "appid" "100" }\n', encoding="utf-8"
        )
        self.module = self.root / "codec" / "appinfo.py"
        self.module.parent.mkdir()
        self.module.write_text(FAKE_APPINFO_MODULE, encoding="utf-8")
        self.backup_dir = self.root / "Kaon" / "backups" / "autoheal"
        self.original = {
            "100": {
                "sections": {
                    "appinfo": {
                        "common": {"name": "Fixture Game", "type": "Game"},
                        "config": {
                            "contenttype": "3",
                            "launch": {
                                "0": {
                                    "executable": "bin/Game Name.exe",
                                    "arguments": "-dx12",
                                    "workingdir": "bin",
                                    "config": {"oslist": "windows"},
                                }
                            },
                        },
                    },
                    "extended": {"developer": "Fixture Studio"},
                }
            }
        }
        self.vdf.write_text(json.dumps(self.original), encoding="utf-8")
        self.patches = (
            mock.patch.object(kaon_repair, "native_steam_running", return_value=False),
            mock.patch.object(kaon_repair, "metadata_editor_running", return_value=False),
            mock.patch.object(kaon_repair, "STABILITY_DELAY", 0.0),
        )
        for patcher in self.patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temporary.cleanup()

    def invoke(self, *, check: bool = False) -> kaon_repair.RepairResult:
        return kaon_repair.repair(
            vdf=self.vdf,
            shared_steamapps=self.shared_steamapps,
            appinfo_module_path=self.module.parent,
            backup_dir=self.backup_dir,
            bottle="Steam Preview",
            description="Play through CrossOver Preview (Kaon)",
            check=check,
        )

    def test_desired_entry_quotes_arguments_and_preserves_workdir(self) -> None:
        original = self.original["100"]["sections"]["appinfo"]["config"][
            "launch"
        ]["0"]
        entry = kaon_repair.desired_entry(
            original, "Steam Preview", "Play through CrossOver Preview (Kaon)"
        )
        self.assertEqual(entry["executable"], "../Kaon/launch_with_log.sh")
        self.assertEqual(
            entry["arguments"], '"Steam Preview" "bin/Game Name.exe" -dx12'
        )
        self.assertEqual(entry["workingdir"], "bin")
        self.assertEqual(entry["config"], {"oslist": "windows"})

    def test_mixed_platform_menu_selects_windows_and_preserves_gates(self) -> None:
        launch = {
            "0": {
                "executable": "ThomasWasAlone.app",
                "config": {"oslist": "macos"},
            },
            "1": {
                "executable": "ThomasWasAlone.exe",
                "config": {
                    "oslist": "windows",
                    "osarch": "64",
                    "BetaKey": "public-test",
                },
            },
        }
        selected = kaon_repair.pick_original_launch(launch)
        self.assertEqual(selected["executable"], "ThomasWasAlone.exe")
        entry = kaon_repair.desired_entry(
            selected,
            "Steam",
            "Play through CrossOver (Kaon)",
        )
        self.assertEqual(
            entry["config"],
            {"oslist": "windows", "osarch": "64", "BetaKey": "public-test"},
        )

    def test_repair_is_validated_and_idempotent(self) -> None:
        before_hash = file_hash(self.vdf)
        first = self.invoke()
        self.assertEqual(first.changed_app_ids, (100,))
        self.assertTrue(first.state_changed)
        self.assertNotEqual(file_hash(self.vdf), before_hash)

        repaired = json.loads(self.vdf.read_text(encoding="utf-8"))
        sections = repaired["100"]["sections"]
        self.assertEqual(sections["extended"], {"developer": "Fixture Studio"})
        self.assertEqual(
            sections["appinfo"]["config"]["contenttype"], "3"
        )
        launch = sections["appinfo"]["config"]["launch"]
        kaon_entries = [
            option for option in launch.values() if kaon_repair.is_kaon_entry(option)
        ]
        self.assertEqual(len(kaon_entries), 1)

        state_path = kaon_repair.owned_state_path(self.backup_dir)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["apps"]["100"]["bottle"], "Steam Preview")
        self.assertTrue(list(self.backup_dir.glob("*-appinfo.vdf")))

        repaired_hash = file_hash(self.vdf)
        second = self.invoke()
        self.assertEqual(second.changed_app_ids, ())
        self.assertFalse(second.state_changed)
        self.assertEqual(file_hash(self.vdf), repaired_hash)
        self.assertTrue(self.invoke(check=True).ok)

    def test_check_reports_missing_without_writing(self) -> None:
        before = self.vdf.read_bytes()
        result = self.invoke(check=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.missing_app_ids, (100,))
        self.assertEqual(self.vdf.read_bytes(), before)
        self.assertFalse(kaon_repair.owned_state_path(self.backup_dir).exists())

    def test_conflicting_description_aborts_without_writing(self) -> None:
        data = deepcopy(self.original)
        launch = data["100"]["sections"]["appinfo"]["config"]["launch"]
        launch["1"] = {
            "executable": "NotKaon.command",
            "description": "Play through CrossOver Preview (Kaon)",
        }
        self.vdf.write_text(json.dumps(data), encoding="utf-8")
        before = self.vdf.read_bytes()
        with self.assertRaises(kaon_repair.KaonRepairError):
            self.invoke()
        self.assertEqual(self.vdf.read_bytes(), before)

    def test_remove_uses_exact_owned_entry_and_is_idempotent(self) -> None:
        self.invoke()
        repaired = self.vdf.read_bytes()

        removal_check = kaon_repair.remove(
            vdf=self.vdf,
            appinfo_module_path=self.module,
            backup_dir=self.backup_dir,
            check=True,
        )
        self.assertEqual(removal_check.removable_app_ids, (100,))
        self.assertEqual(removal_check.diverged_app_ids, ())
        self.assertEqual(self.vdf.read_bytes(), repaired)

        removed = kaon_repair.remove(
            vdf=self.vdf,
            appinfo_module_path=self.module,
            backup_dir=self.backup_dir,
        )
        self.assertEqual(removed.removed_app_ids, (100,))
        self.assertTrue(removed.state_changed)
        current = json.loads(self.vdf.read_text(encoding="utf-8"))
        launch = current["100"]["sections"]["appinfo"]["config"]["launch"]
        self.assertEqual(list(launch), ["0"])
        self.assertEqual(
            current["100"]["sections"]["extended"],
            {"developer": "Fixture Studio"},
        )
        state = json.loads(
            kaon_repair.owned_state_path(self.backup_dir).read_text(encoding="utf-8")
        )
        self.assertEqual(state["apps"], {})

        again = kaon_repair.remove(
            vdf=self.vdf,
            appinfo_module_path=self.module,
            backup_dir=self.backup_dir,
        )
        self.assertEqual(again.removed_app_ids, ())
        self.assertFalse(again.state_changed)

    def test_remove_leaves_diverged_kaon_entry_untouched(self) -> None:
        self.invoke()
        data = json.loads(self.vdf.read_text(encoding="utf-8"))
        launch = data["100"]["sections"]["appinfo"]["config"]["launch"]
        kaon_entry = next(
            option for option in launch.values() if kaon_repair.is_kaon_entry(option)
        )
        kaon_entry["arguments"] += " --user-edited"
        self.vdf.write_text(json.dumps(data), encoding="utf-8")
        before = self.vdf.read_bytes()

        result = kaon_repair.remove(
            vdf=self.vdf,
            appinfo_module_path=self.module,
            backup_dir=self.backup_dir,
        )
        self.assertEqual(result.removed_app_ids, ())
        self.assertEqual(result.diverged_app_ids, (100,))
        self.assertEqual(self.vdf.read_bytes(), before)
        state = json.loads(
            kaon_repair.owned_state_path(self.backup_dir).read_text(encoding="utf-8")
        )
        self.assertIn("100", state["apps"])


if __name__ == "__main__":
    unittest.main()
