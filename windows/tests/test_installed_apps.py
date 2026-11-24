"""Windows app launches must check the local catalog before using a provider."""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from tests.test_support import ensure_runtime_stubs_if_missing

ensure_runtime_stubs_if_missing()

from step_desklet.core import installed_apps, worker as worker_mod
from step_desklet.core.installed_apps import InstalledApp, app_matches, open_app_target
from step_desklet.core.skills import ZyloSkill


class InstalledAppsTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(installed_apps, "get_config_dir", return_value=Path(folder)))
        self.client = Mock()
        self.client.complete.return_value = '{"mode":"final","message":"web fallback"}'
        self.worker = worker_mod.StepAIWorker(api_key="", client=self.client, initial_working_dir=os.getcwd())

    def request(self, prompt, apps, *, authorized=True, rebuild=False):
        skill = ZyloSkill("installed-apps", "Read Windows apps", "Name", Path("installed_apps.ps1"), "bundled")
        result = {"status": "completed", "exit_code": 0, "stdout": json.dumps([
            {"Name": app.name, "Kind": app.kind, "Target": app.target, "WindowExecutable": app.window_executable}
            for app in apps
        ])}
        with (
            patch.object(worker_mod, "IS_WINDOWS", True),
            patch.object(worker_mod, "discover_skills", return_value={"installed-apps": skill}),
            patch.object(worker_mod, "authorize_skill_execution", return_value=(authorized, "Disabled")),
            patch.object(worker_mod, "record_skill_execution") as audit,
            patch.object(self.worker, "_run_windows_powershell", return_value=result) as scan,
            patch.object(worker_mod, "open_windows_item", return_value=worker_mod.LaunchOutcome.WINDOW_VISIBLE) as launch,
        ):
            reply = self.worker.rebuild_app_catalog(use_groq=True) if rebuild else self.worker.process_request_sync(prompt)
            return reply, scan, launch, audit

    def test_local_match_launches_without_calling_provider(self):
        app = InstalledApp("ComfyUI", "shortcut", r"C:\Users\Test\Desktop\ComfyUI.lnk")
        reply, scan, launch, audit = self.request("Please open ComfyUI Desktop", [app])
        self.assertEqual(reply, "Opened ComfyUI.")
        scan.assert_called_once()
        launch.assert_called_once_with(app.target, app_title="ComfyUI", app_executable="")
        self.client.complete.assert_not_called()
        self.assertEqual(audit.call_args.kwargs["output_preview"], "")
        _, repeat_scan, repeat_launch, _ = self.request("Open ComfyUI Desktop", [])
        repeat_scan.assert_not_called()
        repeat_launch.assert_called_once_with(app.target, app_title="ComfyUI", app_executable="")

    def test_groq_rebuild_sends_names_and_preserves_last_list_on_error(self):
        app = InstalledApp("ComfyUI", "shortcut", r"C:\Users\Test\Desktop\ComfyUI.lnk")
        component = InstalledApp("Windows SDK", "installed", "")
        uncertain = InstalledApp("Optional module", "installed", "")
        self.client.complete.return_value = '{"ids":[]}'
        catalog, _, launch, _ = self.request("", [component, app, uncertain], rebuild=True)
        self.assertEqual(catalog.source, "groq")
        self.assertEqual(catalog.apps, [app])
        messages = self.client.complete.call_args.kwargs["messages"]
        self.assertEqual(json.loads(messages[-1]["content"]), {"1": "Optional module"})
        self.assertNotIn(app.target, str(messages))
        launch.assert_not_called()

        self.client.complete.return_value = '{"ids":[999]}'
        with self.assertRaises(ValueError):
            self.request("", [component, app, uncertain], rebuild=True)
        self.assertEqual(installed_apps.load_app_catalog(), catalog)

    def test_cached_app_uses_another_launcher_after_an_unconfirmed_handoff(self):
        shortcut = InstalledApp("VLC media player", "shortcut", r"C:\Apps\VLC.lnk")
        executable = InstalledApp("vlc", "executable", r"C:\Apps\vlc.exe")
        installed_apps.save_app_catalog([shortcut, executable], "local")
        skill = ZyloSkill("installed-apps", "Read Windows apps", "Name", Path("installed_apps.ps1"), "bundled")
        with (
            patch.object(worker_mod, "IS_WINDOWS", True),
            patch.object(worker_mod, "discover_skills", return_value={"installed-apps": skill}),
            patch.object(worker_mod, "authorize_skill_execution", return_value=(True, "Verified")),
            patch.object(worker_mod, "open_windows_item", side_effect=[
                worker_mod.LaunchOutcome.ACCEPTED, worker_mod.LaunchOutcome.WINDOW_VISIBLE,
            ]) as launch,
        ):
            self.assertEqual(
                self.worker.process_request_sync("Open VLC"),
                "Opened VLC media player.",
            )
            self.assertEqual([call.args[0] for call in launch.call_args_list], [shortcut.target, executable.target])
            self.client.complete.assert_not_called()

    def test_stale_shortcut_is_replaced_for_this_request_by_a_fresh_launcher(self):
        old = InstalledApp("Gemini", "shortcut", r"C:\Apps\old.lnk")
        new = InstalledApp("Gemini", "shortcut", r"C:\Apps\new.lnk", r"C:\Apps\Gemini.exe")
        with (
            patch.object(self.worker, "_get_app_catalog", side_effect=[([old], ""), ([new], "")]) as catalog,
            patch.object(self.worker, "launch_item", side_effect=[
                FileNotFoundError(2, "File not found"), worker_mod.LaunchOutcome.WINDOW_VISIBLE,
            ]) as launch,
        ):
            self.assertEqual(self.worker._handle_installed_apps(None, "Gemini", None), ("Opened Gemini.", ""))
            self.assertFalse(catalog.call_args.kwargs["cached"])
            launch.assert_called_with(new.target, app_title="Gemini", app_executable=new.window_executable)

    def test_failed_native_gemini_launch_uses_official_web_app(self):
        app = InstalledApp("Gemini", "shortcut", r"C:\Apps\Gemini.lnk")
        with (
            patch.object(self.worker, "_get_app_catalog", return_value=([app], "")),
            patch.object(self.worker, "launch_item", side_effect=[
                OSError("Launch failed"), worker_mod.LaunchOutcome.ACCEPTED,
            ]) as launch,
        ):
            reply, _ = self.worker._handle_installed_apps(None, "Gemini", None)
            self.assertIn("couldn't confirm a Gemini desktop window", reply)
            self.assertIn("default browser", reply)
            launch.assert_called_with("https://gemini.google.com/app")
            self.assertEqual(launch.call_count, 2)

    def test_process_without_a_window_is_not_reported_as_open(self):
        app = InstalledApp("ComfyUI", "executable", r"C:\Apps\ComfyUI.exe")
        with (
            patch.object(self.worker, "_get_app_catalog", return_value=([app], "")),
            patch.object(self.worker, "launch_item", return_value=worker_mod.LaunchOutcome.STARTED),
        ):
            reply, _ = self.worker._handle_installed_apps(None, "ComfyUI", None)
            self.assertIn("couldn't confirm a window", reply)

    def test_model_cannot_claim_it_opened_an_item_without_a_launch(self):
        self.client.complete.return_value = '{"mode":"final","message":"Opened the app."}'
        reply, _, launch, _ = self.request("Open Gemini", [])
        self.assertIn("couldn’t verify", reply)
        self.assertEqual(self.client.complete.call_count, 2)
        launch.assert_not_called()

    def test_missing_app_falls_back_without_sharing_inventory(self):
        app = InstalledApp("Private Test App", "installed", "")
        reply, scan, launch, _ = self.request("Open Gemini", [app])
        self.assertEqual(reply, "web fallback")
        scan.assert_called_once()
        launch.assert_not_called()
        model_turn = self.client.complete.call_args.kwargs["messages"][1]["content"]
        self.assertIn("found no matching local application", model_turn)
        self.assertNotIn(app.name, model_turn)

    def test_ambiguous_match_does_not_launch(self):
        apps = [
            InstalledApp("Studio One", "executable", r"C:\Apps\StudioOne.exe"),
            InstalledApp("Studio Two", "executable", r"C:\Apps\StudioTwo.exe"),
        ]
        reply, _, launch, _ = self.request("Open Studio", apps)
        self.assertIn("Which installed app", reply)
        self.assertIn("Studio One, Studio Two", reply)
        launch.assert_not_called()
        self.client.complete.assert_not_called()

    def test_inventory_stays_local_and_respects_disable_setting(self):
        app = InstalledApp("Private Test App", "installed", "")
        reply, _, launch, _ = self.request("Show all installed apps", [app])
        self.assertIn(app.name, reply)
        self.assertNotIn(app.name, str(self.worker.messages))
        launch.assert_not_called()
        reply, scan, launch, _ = self.request("Show all installed apps", [app], authorized=False)
        self.assertEqual(reply, "Disabled")
        scan.assert_not_called()
        launch.assert_not_called()
        self.client.complete.assert_not_called()

    def test_alias_prefers_registered_shortcut(self):
        shortcut = InstalledApp("Google Chrome", "shortcut", r"C:\Apps\Chrome.lnk")
        exe = InstalledApp("chrome", "executable", r"C:\Apps\Chrome.exe")
        self.assertEqual(app_matches("chrome", [exe, shortcut]), [shortcut])
        app = InstalledApp("Calculator", "apps-folder", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App")
        self.assertEqual(app.launch_target, r"shell:AppsFolder\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App")
        self.assertFalse(InstalledApp("Remote", "executable", r"\\remote\app.exe").launch_target)

    def test_paths_urls_and_compound_commands_keep_existing_routing(self):
        for prompt in (
            "Open https://gemini.google.com/",
            "Open reports.pdf",
            "Open Gemini in Chrome",
            "Open Chrome and search for GPU news",
            "Start command",
            "Open it",
            r"Launch C:\Apps\ComfyUI.exe",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(open_app_target(prompt), "")
