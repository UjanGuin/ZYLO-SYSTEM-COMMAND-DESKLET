"""Offline checks for FreeLLMAPI configuration, routing and credentials."""

import json
import os
import sys
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from step_desklet.core import config
from step_desklet.core.worker import FreeLLMAPIChatClient
from step_desklet.win11.request_process import check_provider_key


def chat_response(status, body):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    response._content_consumed = True
    return response


@unittest.skipUnless(sys.platform == "win32", "Windows provider settings")
class FreeLLMAPITests(unittest.TestCase):
    def test_setup_and_desklet_use_the_same_enabled_providers(self):
        from PyQt6.QtWidgets import QApplication
        from setup_app import SetupDialog
        from step_desklet.win11.desklet import ZyloDeskletWindow

        with (
            patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}),
            TemporaryDirectory() as profile,
            patch.dict(os.environ, {"APPDATA": profile}),
        ):
            app = QApplication.instance() or QApplication([])
            dialog = SetupDialog()
            self.assertEqual(dialog.provider_combo.findData("freellmapi"), -1)
            dialog.lmstudio_enabled.setChecked(False)
            dialog.freellmapi_enabled.setChecked(True)
            dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData("freellmapi"))
            self.assertEqual(dialog.provider_combo.findData("lmstudio"), -1)
            config.write_json_file(config.get_settings_file(), dialog.values())
            desklet = ZyloDeskletWindow()
            self.assertEqual(desklet.model_btn.text(), "F")
            self.assertEqual(desklet.next_provider(), "nim")
            desklet.toggle_provider()
            self.assertEqual(desklet.next_provider(), "groq")
            desklet.toggle_provider()
            self.assertEqual(desklet.next_provider(), "freellmapi")
            desklet.close()
            dialog.close()
            app.processEvents()

    def test_optional_providers_and_unified_key_persist(self):
        with TemporaryDirectory() as profile, patch.dict("os.environ", {"APPDATA": profile}):
            config.write_env_keys(freellmapi_api_key="test-unified-key")
            config.write_json_file(config.get_settings_file(), {
                "lmstudioEnabled": False, "freeLlmApiEnabled": True,
                "defaultProvider": "freellmapi", "freeLlmApiModel": "auto:fast",
            })
            config.write_provider("freellmapi")
            settings = config.load_settings_config()
            self.assertEqual(config.enabled_providers(settings), ["nim", "groq", "freellmapi"])
            self.assertEqual(config.read_provider(), "freellmapi")
            self.assertEqual(settings["freeLlmApiModel"], "auto:fast")
            self.assertEqual(config.read_env_keys()["FREELLMAPI_API_KEY"], "test-unified-key")
            config.write_json_file(config.get_settings_file(), {"lmstudioEnabled": False})
            self.assertEqual(config.read_provider(), "nim")
            self.assertEqual(config.enabled_providers(config.load_settings_config()), ["nim", "groq"])
            self.assertEqual(config.read_env_keys()["FREELLMAPI_API_KEY"], "test-unified-key")

    def test_chat_uses_loopback_and_both_routing_modes(self):
        for model in config.FREELLMAPI_ROUTES:
            with self.subTest(model=model):
                client = FreeLLMAPIChatClient("test-key", model)
                response = chat_response(200, {"choices": [{"message": {"content": "OK"}}]})
                with patch.object(client._session, "post", return_value=response) as post:
                    self.assertEqual(client.complete(model="", messages=[{"role": "user", "content": "Hello"}]), "OK")
                self.assertFalse(client._session.trust_env)
                self.assertEqual(post.call_args.args[0], "http://127.0.0.1:31415/v1/chat/completions")
                self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")
                self.assertEqual(post.call_args.kwargs["json"]["model"], model)
                self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_setup_key_check_and_rejected_key(self):
        response = chat_response(200, {"choices": [{"message": {"content": "OK"}}]})
        with patch("requests.Session.post", return_value=response) as post:
            check_provider_key("freellmapi", "test-key", model="auto:fast")
        self.assertEqual(post.call_args.args[0], f"{config.FREELLMAPI_BASE_URL}/chat/completions")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "auto:fast")
        client = FreeLLMAPIChatClient("test-key")
        response = chat_response(401, {"error": "test-key"})
        with (
            patch.object(client._session, "post", return_value=response),
            self.assertRaisesRegex(RuntimeError, "rejected the unified key"),
        ):
            client.complete(model="auto", messages=[])
