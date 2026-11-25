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
    REGISTERED_FOLDERS = {
        "MyVideos": "Films",
        "MyPictures": "Cloud Sync/Images",
        "MyMusic": "Cloud Sync/Audio",
        "MyDocuments": "Cloud Sync/Files",
        "Downloads": "Incoming",
        "DesktopDirectory": "Cloud Sync/Bureau",
    }

    def fixture_script(self, profile):
        source = Path(__file__).resolve().parents[1] / "zylo_skills" / "find_file.ps1"
        script = source.read_text(encoding="utf-8")
        folders = "; ".join(f"'{key}' = '{value}'" for key, value in self.REGISTERED_FOLDERS.items())
        substitutions = {
            "$registeredPath = Get-RegisteredFolderPath $policy.Folder": (
                "$fixtureFolders = @{" + folders + "}\n"
                "    $registeredPath = Join-Path $env:USERPROFILE $fixtureFolders[$policy.Folder]"
            ),
            # TemporaryDirectory may itself be deeper than level 7 on a test runner.
            "$level = Get-FolderLevel $root": (
                r"$level = Get-FolderLevel ('X:\Users\Fixture' + $root.Substring($env:USERPROFILE.Length))"
            ),
        }
        for old, new in substitutions.items():
            self.assertEqual(script.count(old), 1)
            script = script.replace(old, new)
        target = Path(profile, "find_file_fixture.ps1")
        target.write_text(script, encoding="utf-8")
        return target

    def test_finder_keeps_to_configured_folders_and_depths(self):
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        depths = {"MyVideos": 2, "MyPictures": 1, "MyMusic": 1,
                  "MyDocuments": 2, "Downloads": 3, "DesktopDirectory": 2}

        with TemporaryDirectory() as profile:
            script = self.fixture_script(profile)
            env = {**os.environ, "USERPROFILE": profile, "APPDATA": profile}
            expected = set()
            for key, depth in depths.items():
                folder = self.REGISTERED_FOLDERS[key]
                inside = Path(profile, folder, *("child" for _ in range(depth)))
                outside = inside / "too-deep"
                outside.mkdir(parents=True)
                file = inside / "Mayar Khela.pdf"
                file.touch()
                if key == "DesktopDirectory":
                    subprocess.run(["attrib", "+h", str(inside)], check=True, capture_output=True)
                    subprocess.run(["attrib", "+h", str(file)], check=True, capture_output=True)
                (outside / "mayarkhela.pdf").touch()
                expected.add(str(inside / "Mayar Khela.pdf"))
            Path(profile, "mayarkhela.pdf").touch()  # Not one of the configured roots.
            args = [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(script), "-Name", "mayarkhela.pdf"]
            output = subprocess.check_output(args, env=env, timeout=15)
            matches = json.loads(output)
            self.assertFalse(matches["Incomplete"])
            self.assertEqual(matches["Count"], len(expected))
            self.assertEqual({os.path.normcase(path) for path in matches["Paths"]},
                             {os.path.normcase(path) for path in expected})

            downloads = Path(profile, self.REGISTERED_FOLDERS["Downloads"])
            (downloads / "Mayar Khela.txt").touch()
            typo_args = [*args[:-1], "mayarkehla.pdf"]
            typo_matches = json.loads(subprocess.check_output(
                typo_args, env=env, timeout=15,
            ))
            self.assertTrue(typo_matches["Paths"])
            self.assertEqual({os.path.normcase(path) for path in typo_matches["Paths"]},
                             {os.path.normcase(path) for path in expected})

            exact = downloads / "mayarkhela.pdf"
            exact.touch()
            exact_matches = json.loads(subprocess.check_output(
                args, env=env, timeout=15,
            ))
            self.assertEqual(exact_matches["Count"], 1)
            self.assertEqual(list(map(os.path.normcase, exact_matches["Paths"])), [os.path.normcase(exact)])

            long_name = downloads / "2026 - Mayar Khela (annotated play with commentary).pdf"
            long_name.touch()
            for query in ("Mayar Khela", "Khela Mayar.pdf", "mayarkehla.pdf", "Mayar Khla.pdf", "Mayar Kheela.pdf"):
                with self.subTest(query=query):
                    partial_matches = json.loads(subprocess.check_output(
                        [*args[:-1], query], env=env, timeout=15,
                    ))
                    self.assertIn(os.path.normcase(long_name), map(os.path.normcase, partial_matches["Paths"]))

    def test_open_named_file_uses_skill_and_no_model(self):
        for request, filename in (
            ("open mayarkhela.pdf", "mayarkhela.pdf"),
            ("open archive.001", "archive.001"),
            ("open .gitignore", ".gitignore"),
            ("open the file build txt", "build txt"),
            ("open the file file notes.custom-extension", "file notes.custom-extension"),
        ):
            with self.subTest(filename=filename):
                self.check_route(request, 1, opens=True, filename=filename, expected_query=filename)

    def test_abbreviated_filename_ranks_above_generic_match(self):
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        with TemporaryDirectory() as profile:
            script = self.fixture_script(profile)
            folder = Path(profile, self.REGISTERED_FOLDERS["DesktopDirectory"], "Main")
            folder.mkdir(parents=True)
            expected = folder / "Visva-Bharati Admission 2026-27.pdf"
            expected.touch()
            (folder / "Admission.pdf").touch()
            for query in ("vb admission.pdf", "vb admission pdf"):
                with self.subTest(query=query):
                    output = subprocess.check_output(
                        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                         "-File", str(script), "-Name", query],
                        env={**os.environ, "USERPROFILE": profile, "APPDATA": profile}, timeout=15,
                    )
                    paths = json.loads(output)["Paths"]
                    self.assertTrue(paths)
                    self.assertEqual(os.path.normcase(paths[0]), os.path.normcase(expected))

    def test_saved_folder_level_can_narrow_expand_or_remove_the_depth_limit(self):
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        with TemporaryDirectory() as profile:
            script = self.fixture_script(profile)
            videos = Path(profile, self.REGISTERED_FOLDERS["MyVideos"])
            files = []
            for subfolders in (0, 2, 5, 7):
                folder = videos.joinpath(*(["x"] * subfolders))
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / "test-depth.mp4"
                path.touch()
                files.append(os.path.normcase(path))
            settings = Path(profile, "ZYLO", "settings.json")
            settings.parent.mkdir()
            env = {**os.environ, "USERPROFILE": profile, "APPDATA": profile}
            args = [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(script), "-Name", "test-depth.mp4"]
            for level, count in ((4, 1), (9, 3), (0, 4), (True, 2)):
                with self.subTest(level=level):
                    settings.write_text(json.dumps({"fileSearchLevels": {"videos": level}}), encoding="utf-8")
                    result = json.loads(subprocess.check_output(args, env=env, timeout=15))
                    self.assertEqual(set(map(os.path.normcase, result["Paths"])), set(files[:count]))

    def test_find_only_duplicate_and_no_match_never_open(self):
        self.check_route("find file mayarkhela.pdf", 1, opens=False)
        self.check_route("open mayarkhela.pdf", 2, opens=False)
        self.check_route("open mayarkhela.pdf", 0, opens=False)

    def test_similar_file_requires_confirmation_on_the_next_request(self):
        for choice, opens in (("Yes", True), ("No", False), ("Yes, please!", True)):
            with self.subTest(choice=choice):
                self.check_route("open mayarkhela.pdf", 1, opens=opens,
                                 filename="Mayar Khela.pdf", confirmation=choice)

    def test_multiple_suggestions_require_a_number(self):
        self.check_route("open mayarkhela.pdf", 2, opens=True,
                         filename="Mayar Khela.pdf", confirmation="open 2", selected=1)

    def test_explicit_file_prompt_without_an_extension(self):
        self.check_route("open the file mayarkhela", 1, opens=True, filename="Mayar Khela.pdf",
                         confirmation="Yes", expected_query="mayarkhela")
        worker = worker_mod.StepAIWorker(api_key="", client=Mock())
        self.assertEqual(worker._detect_open_file_intent("open the file explorer"), (False, ""))

    def test_a_bare_file_name_offers_to_open_the_match_in_the_first_reply(self):
        self.check_route("vb admission pdf", 1, opens=True, filename="Visva-Bharati Admission 2026-27.pdf",
                         confirmation="Yes", expected_query="vb admission.pdf")

    def check_route(self, request, count, *, opens, filename="mayarkhela.pdf", confirmation=None,
                    selected=0, expected_query="mayarkhela.pdf"):
        with TemporaryDirectory() as workspace:
            paths = [Path(workspace, str(index), filename) for index in range(count)]
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
                self.assertIn(f"-Name '{expected_query}'", scan.call_args.args[0])
                if confirmation:
                    launch.assert_not_called()
                    self.assertIn("Did you mean", reply)
                    next_worker = worker_mod.StepAIWorker(api_key="", client=client, initial_working_dir=workspace)
                    next_worker.pending_file_matches = worker.pending_file_matches
                    reply = next_worker.process_request_sync(confirmation)
                    self.assertFalse(next_worker.pending_file_matches)
                scan.assert_called_once()
                if opens:
                    launch.assert_called_once_with(str(paths[selected]))
                    self.assertEqual(reply, f"Started opening {filename}.")
                else:
                    launch.assert_not_called()
                if not count:
                    self.assertIn("couldn't find", reply)
                client.complete.assert_not_called()
