"""Bounded file lookup and open routing on Windows."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from tests.test_support import ensure_runtime_stubs_if_missing

ensure_runtime_stubs_if_missing()

from step_desklet.core import worker as worker_mod
from step_desklet.core.skills import ZyloSkill


@unittest.skipUnless(sys.platform == "win32", "Windows file finder")
class WindowsFileLookupTests(unittest.TestCase):
    def test_finder_keeps_to_configured_folders_and_depths(self):
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        script = Path(__file__).resolve().parents[1] / "zylo_skills" / "find_file.ps1"
        depths = {"Videos": 1, "OneDrive/Pictures": 1, "OneDrive/Music": 1,
                  "OneDrive/Documents": 2, "Downloads": 2, "OneDrive/Desktop": 2}

        with TemporaryDirectory() as profile:
            expected = set()
            for folder, depth in depths.items():
                inside = Path(profile, folder, *("child" for _ in range(depth)))
                outside = inside / "too-deep"
                outside.mkdir(parents=True)
                (inside / "Mayar Khela.pdf").touch()
                (outside / "mayarkhela.pdf").touch()
                expected.add(str(inside / "Mayar Khela.pdf"))
            Path(profile, "mayarkhela.pdf").touch()  # Not one of the configured roots.
            args = [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(script), "-Name", "mayarkhela.pdf"]
            output = subprocess.check_output(args, env={**os.environ, "USERPROFILE": profile}, timeout=15)
            matches = json.loads(output)
            self.assertEqual(matches["Count"], len(expected))
            self.assertEqual({os.path.normcase(path) for path in matches["Paths"]},
                             {os.path.normcase(path) for path in expected})

    def test_open_named_file_uses_skill_and_no_model(self):
        self.check_route("open mayarkhela.pdf", 1, opens=True)

    def test_find_only_duplicate_and_no_match_never_open(self):
        self.check_route("find file mayarkhela.pdf", 1, opens=False)
        self.check_route("open mayarkhela.pdf", 2, opens=False)
        self.check_route("open mayarkhela.pdf", 0, opens=False)

    def check_route(self, request, count, *, opens):
        with TemporaryDirectory() as workspace:
            paths = [Path(workspace, str(index), "mayarkhela.pdf") for index in range(count)]
            for path in paths:
                path.parent.mkdir()
                path.touch()
            skill = ZyloSkill("find-file", "Find files", "Name", Path("find_file.ps1"), "bundled")
            result = {"status": "completed", "exit_code": 0,
                      "stdout": json.dumps({"Count": count, "Paths": list(map(str, paths))})}
            client = Mock()
            worker = worker_mod.StepAIWorker(api_key="", client=client, initial_working_dir=workspace)
            with (
                patch.object(worker_mod, "discover_skills", return_value={"find-file": skill}),
                patch.object(worker_mod, "authorize_skill_execution", return_value=(True, "Authorized")),
                patch.object(worker_mod, "record_skill_execution"),
                patch.object(worker, "_run_windows_powershell", return_value=result) as scan,
                patch.object(worker_mod, "open_windows_item", return_value=worker_mod.LaunchOutcome.STARTED) as launch,
            ):
                reply = worker.process_request_sync(request)
                self.assertIn("-Name 'mayarkhela.pdf'", scan.call_args.args[0])
                scan.assert_called_once()
                if opens:
                    launch.assert_called_once_with(str(paths[0]))
                    self.assertEqual(reply, "Started opening mayarkhela.pdf.")
                else:
                    launch.assert_not_called()
                if not count:
                    self.assertIn("couldn't find", reply)
                client.complete.assert_not_called()
