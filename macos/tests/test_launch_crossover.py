# SPDX-License-Identifier: GPL-3.0-or-later
# Kaon - macOS Steam / CrossOver integration
# Copyright (C) 2026 Kaon contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


LAUNCHER = Path(__file__).resolve().parents[1] / "resources" / "launch_crossover.sh"
REPAIR_MESSAGE = (
    "Kaon configuration is incomplete or damaged. "
    "Open Kaon Setup and choose Repair."
)


class LaunchCrossOverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.config = (
            self.home / "Library" / "Application Support" / "Kaon" / "config.json"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_launcher(self) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["HOME"] = os.fspath(self.home)
        return subprocess.run(
            ["/bin/zsh", os.fspath(LAUNCHER), "Steam", "C:\\Game.exe"],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )

    def test_missing_configuration_has_repair_guidance(self) -> None:
        result = self.run_launcher()

        self.assertEqual(result.returncode, 78)
        self.assertIn("Open Kaon Setup and choose Repair.", result.stderr)

    def test_invalid_crossover_app_configuration_has_repair_guidance(self) -> None:
        invalid_documents = (
            b"{not valid json",
            json.dumps({}).encode(),
            json.dumps({"crossover_app": ""}).encode(),
            json.dumps({"crossover_app": "   "}).encode(),
        )
        self.config.parent.mkdir(parents=True)
        for document in invalid_documents:
            with self.subTest(document=document):
                self.config.write_bytes(document)
                result = self.run_launcher()

                self.assertEqual(result.returncode, 78)
                self.assertIn(REPAIR_MESSAGE, result.stderr)
                self.assertNotIn("plutil", result.stderr.lower())

    def test_valid_crossover_app_continues_to_wine_launcher_check(self) -> None:
        selected_app = self.home / "Applications" / "CrossOver Preview.app"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(
            json.dumps({"crossover_app": os.fspath(selected_app)}),
            encoding="utf-8",
        )

        result = self.run_launcher()

        self.assertEqual(result.returncode, 69)
        self.assertIn("selected CrossOver Wine launcher", result.stderr)
        self.assertNotIn(REPAIR_MESSAGE, result.stderr)


if __name__ == "__main__":
    unittest.main()
