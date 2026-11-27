"""Offline checks for the assistant preferences and writing tools."""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from step_desklet.core.assistant_context import assistant_context


@unittest.skipUnless(sys.platform == "win32", "Windows desklet")
class AssistantPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"APPDATA": self.enterContext(TemporaryDirectory())}))

    def test_weather_is_opt_in_and_cached(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.json.return_value = {"current": {
            "temperature_2m": 26, "relative_humidity_2m": 54, "precipitation": 0, "wind_speed_10m": 8,
        }}
        with patch("step_desklet.core.assistant_context.requests.get", return_value=response) as fetch:
            assistant_context({}, "What's the weather?")
            settings = {"weatherLocation": "23.7, 87.7"}
            self.assertIn("Current local date and time", assistant_context(settings, "Hello"))
            fetch.assert_not_called()
            for _ in range(2):
                self.assertIn("26 °C", assistant_context(settings, "What's the weather?"))
            fetch.assert_called_once()

    def test_writing_uses_existing_provider_without_action_dispatch_or_chat_history(self):
        from step_desklet.core.config import get_settings_file, write_json_file
        from step_desklet.win11.request_process import run_request_worker

        write_json_file(get_settings_file(), {
            "customInstructions": "Use British English.", "weatherLocation": "23.7, 87.7",
        })
        payload = {
            "provider": "lmstudio", "base_url": "http://127.0.0.1:1234/v1", "model": "",
            "options": {"conversation_path": None}, "prompt": "Proofread this weather report.",
            "writing_task": "proofread", "history": [{"role": "user", "content": "Prior text"}],
        }
        for result, expected in (
            ('{"mode":"final","message":"Edited"}', ["response", "Edited"]),
            ('{"mode":"run","command":"irrelevant"}',
             ["error", "The provider returned no usable writing result. Try again."]),
        ):
            stdout = io.StringIO()
            stdin = SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))
            with (
                patch.object(sys, "stdin", stdin), redirect_stdout(stdout),
                patch("step_desklet.core.worker.LMStudioChatClient.complete", return_value=result) as complete,
                patch("step_desklet.core.worker.StepAIWorker.process_request") as execute,
                patch("step_desklet.core.assistant_context.requests.get") as fetch,
            ):
                self.assertEqual(run_request_worker(), 0)
            complete.assert_called_once()
            execute.assert_not_called()
            fetch.assert_not_called()
            self.assertEqual(complete.call_args.kwargs["model"], "")
            system, user = complete.call_args.kwargs["messages"]
            self.assertIn("British English", system["content"])
            self.assertNotIn("Current local date", system["content"])
            self.assertEqual(user, {"role": "user", "content": payload["prompt"]})
            self.assertEqual(json.loads(stdout.getvalue().splitlines()[-1]), expected)
