"""Unit tests for zylo_skills/ per-skill disable toggles, SHA-256 trust allowlist, and execution history."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.test_support import ensure_runtime_stubs_if_missing

ensure_runtime_stubs_if_missing()

from step_desklet.core import skills as skills_mod  # noqa: E402
from step_desklet.core import worker as worker_mod  # noqa: E402


class _SkillCallClient:
    def __init__(self, skill_id: str):
        self.skill_id = skill_id
        self.calls = 0
        self.last_feedback = ""

    def cancel(self):
        pass

    def complete(self, **kwargs):
        self.calls += 1
        messages = kwargs["messages"]
        if self.calls == 1:
            return json.dumps(
                {
                    "mode": "skill",
                    "skill": self.skill_id,
                    "args": {"Target": "hello"},
                    "reason": "test skill invocation",
                }
            )
        self.last_feedback = messages[-1]["content"]
        return json.dumps({"mode": "final", "message": "done"})


class SkillSecurityAndHistoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_env = {
            "HOME": os.environ.get("HOME"),
            "APPDATA": os.environ.get("APPDATA"),
        }
        os.environ["HOME"] = self._tmp.name
        os.environ["APPDATA"] = self._tmp.name

    def tearDown(self):
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _drop_custom_skill(self, name: str = "custom_probe.ps1", body: str = "Write-Output 'hi'\n") -> Path:
        user_dir = skills_mod.ensure_skills_directory()
        script = user_dir / name
        script.write_text(
            "# @skill-name: custom-probe\n"
            "# @skill-description: Custom probe test script\n"
            "# @skill-params: Target (optional)\n"
            + body,
            encoding="utf-8",
        )
        return script

    def test_custom_skill_untrusted_until_approved_and_invalidated_on_edit(self):
        script = self._drop_custom_skill()
        discovered = skills_mod.discover_skills()
        skill = discovered["custom-probe"]

        self.assertFalse(skill.trusted)
        ok, reason = skills_mod.authorize_skill_execution(skill)
        self.assertFalse(ok)
        self.assertIn("not trusted", reason.lower())

        # Approve current SHA-256
        skills_mod.trust_skill(skill)
        discovered_trusted = skills_mod.discover_skills()
        self.assertTrue(discovered_trusted["custom-probe"].trusted)
        ok_after, _ = skills_mod.authorize_skill_execution(discovered_trusted["custom-probe"])
        self.assertTrue(ok_after)

        # Tamper with file -> SHA-256 mismatch must revoke trust immediately
        script.write_text(script.read_text(encoding="utf-8") + "Write-Output 'tampered'\n", encoding="utf-8")
        discovered_tampered = skills_mod.discover_skills()
        self.assertFalse(discovered_tampered["custom-probe"].trusted)
        ok_tampered, tamper_reason = skills_mod.authorize_skill_execution(discovered_tampered["custom-probe"])
        self.assertFalse(ok_tampered)
        self.assertIn("changed", tamper_reason.lower())

    def test_disable_individual_skill_blocks_prompt_exposure_and_execution(self):
        self._drop_custom_skill()
        discovered = skills_mod.discover_skills()
        skills_mod.trust_skill(discovered["custom-probe"])

        skills_mod.set_skill_enabled("custom-probe", False)
        refreshed = skills_mod.discover_skills()
        self.assertFalse(refreshed["custom-probe"].enabled)

        prompt_block = skills_mod.format_skills_prompt_block(refreshed)
        self.assertNotIn("custom-probe", prompt_block)

        ok, msg = skills_mod.authorize_skill_execution(refreshed["custom-probe"])
        self.assertFalse(ok)
        self.assertIn("disabled", msg.lower())

    def test_upgrade_adds_new_bundled_skills_to_existing_profiles(self):
        user_dir = skills_mod.get_user_skills_dir()
        user_dir.mkdir(parents=True, exist_ok=True)
        existing = user_dir / "media_controls.ps1"
        custom_source = "# @skill-name: media-controls\nWrite-Output 'custom'\n"
        existing.write_text(custom_source, encoding="utf-8")

        discovered = skills_mod.discover_skills()

        for skill_id, filename in (("installed-apps", "installed_apps.ps1"), ("find-file", "find_file.ps1")):
            self.assertEqual(discovered[skill_id].script_path, (user_dir / filename).resolve())
            self.assertTrue(discovered[skill_id].trusted)
        self.assertEqual(existing.read_text(encoding="utf-8"), custom_source)

    def test_upgrade_replaces_only_the_verified_predecessor(self):
        from unittest.mock import patch

        folder = skills_mod.get_user_skills_dir()
        folder.mkdir(parents=True, exist_ok=True)
        old_script = folder / "installed_apps.ps1"
        old_script.write_text("# @skill-name: installed-apps\nWrite-Output 'old'\n", encoding="utf-8")
        old_digest = skills_mod.compute_script_sha256(old_script)
        old = skills_mod.discover_skills()["installed-apps"]
        skills_mod.trust_skill(old)

        with patch.object(skills_mod, "_SUPERSEDED_BUNDLED_SKILLS", {"installed_apps.ps1": {old_digest}}):
            refreshed = skills_mod.discover_skills()["installed-apps"]
            self.assertNotEqual(refreshed.sha256, old_digest)
            self.assertTrue(refreshed.trusted)
            old_script.write_text("# @skill-name: installed-apps\nWrite-Output 'custom'\n", encoding="utf-8")
            custom_digest = skills_mod.compute_script_sha256(old_script)
            self.assertEqual(skills_mod.discover_skills()["installed-apps"].sha256, custom_digest)

    def test_worker_blocks_untrusted_skill_with_permission_outcome_and_logs_history(self):
        self._drop_custom_skill()
        client = _SkillCallClient("custom-probe")
        ai_worker = worker_mod.StepAIWorker(
            api_key="test",
            client=client,
            initial_working_dir=self._tmp.name,
        )

        reply = ai_worker.process_request_sync("run custom probe")
        self.assertEqual(reply, "done")
        self.assertTrue(client.last_feedback.startswith("Command requires permission.\n"))
        self.assertIn("status: requires_permission", client.last_feedback)

        history = skills_mod.read_skill_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["skill"], "custom-probe")
        self.assertEqual(history[0]["status"], worker_mod.CMD_STATUS_REQUIRES_PERMISSION)
        self.assertTrue(history[0]["sha256"])


if __name__ == "__main__":
    unittest.main()
