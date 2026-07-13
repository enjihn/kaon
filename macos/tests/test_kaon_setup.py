from collections import OrderedDict
from copy import deepcopy
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import threading
import unittest
from unittest import mock


ENGINE = Path(__file__).resolve().parents[1] / "engine"
sys.path.insert(0, str(ENGINE))

import kaon_setup  # noqa: E402


class BottleValidationTests(unittest.TestCase):
    def test_rejects_unsafe_or_unsupported_names(self):
        invalid_names = (
            "",
            ".",
            "..",
            "Steam/Preview",
            "Steam\x00Preview",
            "Steam\rPreview",
            "Steam\nPreview",
            "S" * (kaon_setup.MAX_BOTTLE_NAME_LENGTH + 1),
        )
        for name in invalid_names:
            with self.subTest(name=repr(name)):
                with self.assertRaises(kaon_setup.SetupError) as raised:
                    kaon_setup.validate_bottle_name(name)
                self.assertEqual(raised.exception.exit_code, 64)

    def test_accepts_length_limit_and_ordinary_punctuation(self):
        boundary = "S" * kaon_setup.MAX_BOTTLE_NAME_LENGTH

        self.assertEqual(kaon_setup.validate_bottle_name(boundary), boundary)
        self.assertEqual(
            kaon_setup.validate_bottle_name("Steam...Preview"), "Steam...Preview"
        )


class TextVDFTests(unittest.TestCase):
    def test_round_trip_preserves_nested_keys_and_windows_paths(self):
        source = OrderedDict(
            {
                "libraryfolders": OrderedDict(
                    {
                        "0": OrderedDict(
                            {
                                "path": r"C:\Program Files (x86)\Steam",
                                "label": "A quoted \"label\"",
                                "apps": OrderedDict({"123": "456"}),
                            }
                        )
                    }
                )
            }
        )
        self.assertEqual(kaon_setup.parse_vdf(kaon_setup.encode_vdf(source)), source)

    def test_library_merge_is_idempotent_and_uses_windows_content_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library_file = root / "libraryfolders.vdf"
            shared = root / "CrossOver Steam"
            (shared / "steamapps").mkdir(parents=True)
            windows = OrderedDict(
                {
                    "libraryfolders": OrderedDict(
                        {
                            "0": OrderedDict(
                                {
                                    "path": r"C:\Program Files (x86)\Steam",
                                    "label": "",
                                    "contentid": "424242",
                                    "totalsize": "0",
                                    "update_clean_bytes_tally": "12",
                                    "time_last_update_verified": "13",
                                    "apps": OrderedDict({"99": "100"}),
                                }
                            )
                        }
                    )
                }
            )
            (shared / "steamapps/libraryfolders.vdf").write_bytes(
                kaon_setup.encode_vdf(windows)
            )
            existing = OrderedDict(
                {
                    "libraryfolders": OrderedDict(
                        {
                            "0": OrderedDict(
                                {
                                    "path": str(root / "Native Steam"),
                                    "label": "Native",
                                    "contentid": "10",
                                    "apps": OrderedDict({"1": "2"}),
                                }
                            )
                        }
                    )
                }
            )
            library_file.write_bytes(kaon_setup.encode_vdf(existing))
            state = {"schema_version": 1, "files": {}, "library_entries": {}}

            self.assertTrue(
                kaon_setup.ensure_library_entry(library_file, shared, state, "Shared")
            )
            first = library_file.read_bytes()
            self.assertFalse(
                kaon_setup.ensure_library_entry(library_file, shared, state, "Shared")
            )
            self.assertEqual(library_file.read_bytes(), first)
            parsed = kaon_setup.parse_vdf(first)["libraryfolders"]
            self.assertEqual(parsed["0"]["apps"], OrderedDict({"1": "2"}))
            self.assertEqual(parsed["1"]["path"], str(shared))
            self.assertEqual(parsed["1"]["contentid"], "424242")
            self.assertEqual(parsed["1"]["apps"], OrderedDict({"99": "100"}))


class TrayPatchTests(unittest.TestCase):
    def vendor_fixture(self, prefix: bytes) -> bytes:
        data = bytearray(b"\x00" * 1024)
        data[0:2] = b"MZ"
        data[
            kaon_setup.MARKER_OFFSET : kaon_setup.MARKER_OFFSET
            + len(kaon_setup.ORIGINAL_MARKER)
        ] = kaon_setup.ORIGINAL_MARKER
        pattern = prefix + b"\xff\x15\x11\x22\x33\x44" + kaon_setup.TRAY_SUFFIX
        data[400 : 400 + len(pattern)] = pattern
        return bytes(data)

    def test_derives_both_known_stable_and_preview_patterns(self):
        for prefix in kaon_setup.KNOWN_TRAY_PREFIXES:
            with self.subTest(prefix=prefix.hex()):
                patched = kaon_setup.derive_tray_patch(self.vendor_fixture(prefix))
                self.assertEqual(
                    patched[
                        kaon_setup.MARKER_OFFSET : kaon_setup.MARKER_OFFSET
                        + len(kaon_setup.PATCHED_MARKER)
                    ],
                    kaon_setup.PATCHED_MARKER,
                )
                self.assertIn(prefix + kaon_setup.TRAY_REPLACEMENT, patched)

    def test_unknown_tray_build_fails_without_guessing(self):
        with self.assertRaises(kaon_setup.SetupError):
            kaon_setup.derive_tray_patch(self.vendor_fixture(b"not-a-known-prefix"))

    def test_open_already_patched_targets_are_adopted_as_active(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vendor_path = root / "vendor-explorer.exe"
            vendor = self.vendor_fixture(kaon_setup.KNOWN_TRAY_PREFIXES[0])
            desired = kaon_setup.derive_tray_patch(vendor)
            vendor_path.write_bytes(vendor)
            targets = (root / "windows/explorer.exe", root / "windows/system32/explorer.exe")
            for target in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(desired)
            state = {"files": {}}
            config = {"bottle": "Steam"}

            with mock.patch.object(kaon_setup, "vendor_explorer", return_value=vendor_path), mock.patch.object(
                kaon_setup, "tray_targets", return_value=targets
            ), mock.patch.object(kaon_setup, "target_is_open", return_value=True), mock.patch.object(
                kaon_setup, "save_state"
            ), mock.patch.object(kaon_setup, "dock_export", return_value=None):
                result = kaon_setup.apply_tray_guard(config, {}, state)

            self.assertTrue(result["active"])
            self.assertFalse(result["degraded"])
            self.assertEqual(state["tray"]["target_paths"], [str(item) for item in targets])

    def test_open_unpatched_target_defers_without_partial_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vendor_path = root / "vendor-explorer.exe"
            vendor = self.vendor_fixture(kaon_setup.KNOWN_TRAY_PREFIXES[1])
            vendor_path.write_bytes(vendor)
            targets = (root / "windows/explorer.exe", root / "windows/system32/explorer.exe")
            for target in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(vendor)
            state = {"files": {}}

            with mock.patch.object(kaon_setup, "vendor_explorer", return_value=vendor_path), mock.patch.object(
                kaon_setup, "tray_targets", return_value=targets
            ), mock.patch.object(kaon_setup, "target_is_open", return_value=True), mock.patch.object(
                kaon_setup, "save_state"
            ):
                result = kaon_setup.apply_tray_guard({"bottle": "Steam"}, {}, state)

            self.assertTrue(result["degraded"])
            self.assertEqual([target.read_bytes() for target in targets], [vendor, vendor])


class AgentTests(unittest.TestCase):
    def test_generated_agent_is_a_valid_property_list(self):
        generated = kaon_setup.agent_plist(
            "io.github.enjihn.kaon.test",
            ("/tmp/kaon setup", "guard", "--yes"),
            "test",
            WatchPaths=["/tmp/a path"],
            StartInterval=60,
        )
        encoded = plistlib.dumps(generated)
        self.assertEqual(plistlib.loads(encoded)["ProgramArguments"][0], "/tmp/kaon setup")


class SteamStartupTests(unittest.TestCase):
    def test_concurrent_start_attempts_launch_only_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            running = threading.Event()
            start_together = threading.Barrier(2)
            launches = []
            results = []
            errors = []

            class FakeProcess:
                def poll(self):
                    return None

            def fake_popen(*args, **kwargs):
                launches.append((args, kwargs))
                running.set()
                return FakeProcess()

            def worker():
                try:
                    start_together.wait()
                    results.append(
                        kaon_setup.ensure_windows_steam(
                            {"crossover_app": str(root / "CrossOver.app"), "bottle": "Steam", "hide_tray": False},
                            {"windows_steam": root / "steam.exe", "shared_root": root},
                        )
                    )
                except Exception as error:  # pragma: no cover - assertion reports it
                    errors.append(error)

            with mock.patch.object(kaon_setup, "SUPPORT_ROOT", root / "support"), mock.patch.object(
                kaon_setup, "BACKUP_ROOT", root / "support/backups"
            ), mock.patch.object(kaon_setup, "LOG_ROOT", root / "logs"), mock.patch.object(
                kaon_setup, "LAUNCH_AGENT_ROOT", root / "agents"
            ), mock.patch.object(
                kaon_setup, "steam_processes", side_effect=lambda paths: [123] if running.is_set() else []
            ), mock.patch.object(kaon_setup.subprocess, "Popen", side_effect=fake_popen):
                threads = [threading.Thread(target=worker) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)

            self.assertFalse(errors)
            self.assertEqual(len(launches), 1)
            self.assertEqual(sorted(results), [False, True])

    def test_post_install_start_failure_is_a_warning(self):
        result = {}
        config = {
            "start_at_login": True,
            "crossover_app": "/Applications/CrossOver.app",
            "bottle": "Steam",
        }
        with mock.patch.object(
            kaon_setup,
            "ensure_windows_steam",
            side_effect=kaon_setup.SetupError("timed out", 75),
        ):
            kaon_setup.add_post_install_startup_result(config, result)

        self.assertFalse(result["windows_steam_started"])
        self.assertIn("installed successfully", result["warnings"][0])


class DockStateTests(unittest.TestCase):
    def test_removed_tile_with_bookmark_bytes_round_trips_through_json(self):
        app_path = "/Applications/CrossOver Preview.app"
        tile = {
            "tile-data": {
                "file-data": {"_CFURLString": "file:///Applications/CrossOver%20Preview.app/"},
                "book": b"binary bookmark data",
            }
        }
        exported = {"persistent-apps": [tile]}
        imported = []
        state = {}
        config = {"crossover_app": app_path}

        with mock.patch.object(kaon_setup, "dock_export", return_value=deepcopy(exported)), mock.patch.object(
            kaon_setup, "dock_import", side_effect=lambda value: imported.append(deepcopy(value))
        ):
            self.assertTrue(kaon_setup.hide_crossover_dock_tile(config, state))
        json.dumps(state)
        self.assertEqual(imported[-1]["persistent-apps"], [])

        with mock.patch.object(kaon_setup, "dock_export", return_value={"persistent-apps": []}), mock.patch.object(
            kaon_setup, "dock_import", side_effect=lambda value: imported.append(deepcopy(value))
        ):
            self.assertTrue(kaon_setup.restore_crossover_dock_tile(state))
        self.assertEqual(
            imported[-1]["persistent-apps"][0]["tile-data"]["book"],
            b"binary bookmark data",
        )


class TransactionTests(unittest.TestCase):
    def test_snapshot_restores_files_symlinks_and_missing_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original"
            original.write_bytes(b"original bytes")
            link_target = root / "target"
            link_target.write_text("target", encoding="utf-8")
            link = root / "link"
            link.symlink_to("target")
            missing = root / "created-later"

            with mock.patch.object(kaon_setup, "service_loaded", return_value=False), mock.patch.object(
                kaon_setup, "dock_export", return_value=None
            ):
                snapshot = kaon_setup.MutationSnapshot(
                    (original, link, missing),
                    (),
                )
            try:
                kaon_setup.atomic_write(original, b"changed")
                kaon_setup.atomic_write(link, b"not a link")
                kaon_setup.atomic_write(missing, b"temporary")
                snapshot.rollback()
            finally:
                snapshot.close()

            self.assertEqual(original.read_bytes(), b"original bytes")
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.readlink(), Path("target"))
            self.assertFalse(missing.exists())

    def test_snapshot_never_overwrites_a_newer_external_edit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "managed"
            path.write_bytes(b"original")
            with mock.patch.object(kaon_setup, "service_loaded", return_value=False), mock.patch.object(
                kaon_setup, "dock_export", return_value=None
            ):
                snapshot = kaon_setup.MutationSnapshot((path,), ())
            try:
                kaon_setup.atomic_write(path, b"kaon")
                path.write_bytes(b"newer external bytes")
                with self.assertRaises(kaon_setup.SetupError):
                    snapshot.rollback()
            finally:
                snapshot.close()
            self.assertEqual(path.read_bytes(), b"newer external bytes")

    def test_snapshot_never_overwrites_an_unclaimed_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "managed"
            path.write_bytes(b"original")
            with mock.patch.object(kaon_setup, "service_loaded", return_value=False), mock.patch.object(
                kaon_setup, "dock_export", return_value=None
            ):
                snapshot = kaon_setup.MutationSnapshot((path,), ())
            try:
                path.write_bytes(b"external bytes")
                with self.assertRaises(kaon_setup.SetupError):
                    snapshot.rollback()
            finally:
                snapshot.close()
            self.assertEqual(path.read_bytes(), b"external bytes")

    def test_fresh_ownership_state_preserves_each_install_cycles_preexisting_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "managed-source"
            destination = root / "launcher"
            source.write_bytes(b"kaon managed bytes")

            with mock.patch.object(kaon_setup, "BACKUP_ROOT", root / "backups"):
                for original in (b"manual version one", b"manual version two"):
                    destination.write_bytes(original)
                    state = {"files": {}, "installed_hashes": {}}
                    kaon_setup.install_file(source, destination, state)
                    disposition = kaon_setup.restore_installed_file(
                        destination,
                        state["installed_hashes"][str(destination)],
                        state,
                    )
                    self.assertEqual(disposition, "restored")
                    self.assertEqual(destination.read_bytes(), original)

    def test_source_runtime_carries_launcher_templates_for_future_repairs(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            support = home / "Library/Application Support/Kaon"
            state = {"files": {}, "installed_hashes": {}}
            patches = (
                mock.patch.object(kaon_setup, "SUPPORT_ROOT", support),
                mock.patch.object(kaon_setup, "BACKUP_ROOT", support / "backups"),
                mock.patch.object(kaon_setup, "LOG_ROOT", home / "Library/Logs/Kaon"),
                mock.patch.object(kaon_setup, "LAUNCH_AGENT_ROOT", home / "Library/LaunchAgents"),
            )
            for patcher in patches:
                patcher.start()
            try:
                runtime = kaon_setup.install_runtime(state)
                installed_engine = support / "lib/kaon_setup.py"
                self.assertTrue(runtime.is_file())
                self.assertTrue((support / "lib/launch_crossover.sh").is_file())
                self.assertTrue((support / "lib/launch_with_log.sh").is_file())
                with mock.patch.object(kaon_setup, "__file__", str(installed_engine)):
                    resolved = kaon_setup.resource_path("resources/launch_crossover.sh")
                self.assertEqual(resolved, support / "lib/launch_crossover.sh")
            finally:
                for patcher in reversed(patches):
                    patcher.stop()


if __name__ == "__main__":
    unittest.main()
