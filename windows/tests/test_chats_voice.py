"""Offline checks of chat continuity and Voice Mode integration."""

import hashlib
import io
import os
import sys
import threading
import unittest
import wave
from array import array
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from step_desklet.core.chats import ChatStore


class ArchiveTests(unittest.TestCase):
    def test_migration_restart_and_retry_keep_the_original_chat(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "old.json"
            legacy.write_text('[{"role":"user","content":"old question"}]', encoding="utf-8")
            store = ChatStore(root / "chat.db", legacy=legacy, boot="100")
            self.assertEqual(len(store.list("old question")), 1)
            chat, message = store.begin("new question", "groq", "voice")
            store.finish(chat, message, "display only", "safe context", "groq")
            self.assertEqual(store.history(chat, 10)[-1]["content"], "safe context")
            self.assertEqual(ChatStore(store.path, legacy=legacy, boot="101").active(), chat)
            retry = store.branch(chat, message)
            self.assertEqual(store.messages(retry), [])
            self.assertEqual(len(store.messages(chat)), 2)
            restarted = ChatStore(store.path, legacy=legacy, boot="200")
            self.assertIsNone(restarted.active())
            self.assertEqual(len(restarted.list()), 3)


@unittest.skipUnless(sys.platform == "win32", "Windows desktop")
class VoiceAndChatsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_automatic_download_keeps_only_verified_models(self):
        from step_desklet.win11 import voice_models

        data = b"GGUF voice fixture"
        digest = hashlib.sha256(data).hexdigest()
        asset = ("stt", "fixture.gguf", "https://example.invalid/model", digest, len(data))
        response = Mock()
        response.headers = {}
        response.iter_content.return_value = [data]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with TemporaryDirectory() as tmp, patch.object(voice_models, "ASSETS", (asset,)):
            root = Path(tmp)
            with patch.object(voice_models.requests, "get", return_value=response) as get:
                self.assertTrue(voice_models.install_models(root, lambda *_: None, threading.Event()))
                self.assertEqual((root / "stt" / "fixture.gguf").read_bytes(), data)
                self.assertTrue(voice_models.install_models(root, lambda *_: None, threading.Event()))
                get.assert_called_once()
                self.assertFalse((root / "stt" / "fixture.gguf.part").exists())
                (root / "stt" / "fixture.gguf").unlink()
                response.iter_content.return_value = [b"corrupt"]
                with self.assertRaisesRegex(ValueError, "Could not verify"):
                    voice_models.install_models(root, lambda *_: None, threading.Event())
                self.assertFalse((root / "stt" / "fixture.gguf").exists())

    def test_desklet_formatting_and_voice_setup(self):
        from setup_app import SetupDialog
        from step_desklet.win11.chat_window import ChatsWindow
        from step_desklet.win11.desklet import ZyloDeskletWindow
        from step_desklet.win11.voice import VoiceController, win_shortcut

        with TemporaryDirectory() as profile, patch.dict(os.environ, {"APPDATA": profile, "LOCALAPPDATA": profile}):
            desklet = ZyloDeskletWindow()
            desklet.chat_store = ChatStore(boot="100")
            desklet._on_worker_response("*italics* and **bold**")
            self.assertIn("font-style:italic", desklet.response_label.text())
            self.assertIn("font-weight:700", desklet.response_label.text())
            submit = Mock(return_value=True)
            voice = VoiceController(self.app, submit, lambda: None, lambda: False)
            try:
                self.assertEqual(win_shortcut("Ctrl+Alt+Space")[:2], (3, 32))
                setup = SetupDialog(voice=voice)
                self.assertEqual(setup.values()["voice"]["shortcut"], "Meta+Space")
                setup.custom_instructions.setPlainText("Keep replies concise.")
                setup.weather_location.setText("23.7, 87.7")
                self.assertEqual(setup.values()["customInstructions"], "Keep replies concise.")
                self.assertEqual(setup.values()["weatherLocation"], "23.7, 87.7")
                setup.chat_bubble_color = "#406b60"
                self.assertEqual(setup.values()["chatBubbleColor"], "#406b60")
                self.assertEqual(setup.values()["voice"]["ttsEngine"], "windows")
                setup.voice_tts.setCurrentIndex(setup.voice_tts.findData("pocket"))
                self.assertEqual(setup.values()["voice"]["ttsEngine"], "pocket")
                chats = ChatsWindow(desklet, voice)
                chats.show()
                self.app.processEvents()
                self.assertTrue(chats.isVisible())
                voice._state = "finalizing"
                voice._on_event("transcript", voice._capture_id, "Voice question")
                voice._on_event("transcript", voice._capture_id, "Voice question")
                submit.assert_called_once_with("Voice question", "voice")
                voice.settings = {"spokenReplies": True}
                with patch.object(voice, "_speak") as speak:
                    voice.speak("chat", "typed")
                    voice.speak("voice", "spoken")
                    speak.assert_called_once_with("spoken", "")
                chats.close()
                setup.close()
            finally:
                voice.shutdown()
                desklet.close()

    def test_release_submits_the_final_text_and_resets_the_stream_for_next_hold(self):
        from types import SimpleNamespace

        from PyQt6.QtTest import QTest

        from step_desklet.win11.voice import VoiceController

        submit = Mock(return_value=True)
        voice = VoiceController(self.app, submit, lambda: None, lambda: False)
        self.addCleanup(voice.shutdown)
        voice.settings = {"enabled": True}
        voice._model_ready = True
        voice._asr = Mock()
        sessions = []
        for text in ("First recording", "Second recording"):
            stream = Mock(spec=["feed", "text", "finalize", "snapshot", "reset"])
            stream.text.return_value = SimpleNamespace(display=text)
            stream.snapshot.return_value = SimpleNamespace(text=text)
            sessions.append(Mock(spec=["stream", "close"], **{"stream.return_value": stream}))
        voice._asr.session.side_effect = sessions
        with patch.object(voice, "_start_audio", return_value=True):
            for count, session in enumerate(sessions, 1):
                target = Mock(return_value=True)
                voice.begin(submit=target if count == 2 else submit)
                voice._pcm_pending.append(0.2)
                voice._peak = 0.2
                voice.release()
                callback = target if count == 2 else submit
                for _ in range(30):
                    if callback.called:
                        break
                    QTest.qWait(10)
                stream = session.stream.return_value
                stream.finalize.assert_called_once()
                stream.reset.assert_called_once()
                session.close.assert_called_once()
                callback.assert_called_once_with(stream.snapshot.return_value.text, "voice")

    def test_voice_reply_uses_windows_speech_when_pocket_tts_is_unavailable(self):
        from step_desklet.win11.voice import VoiceController

        voice = VoiceController(self.app, Mock(), lambda: None, lambda: False)
        self.addCleanup(voice.shutdown)
        audio = b"RIFF test WAV"
        with (
            patch.object(voice, "_windows_speech", return_value=audio) as synthesize,
            patch.object(voice, "_on_event") as event,
        ):
            voice.voice_event.disconnect()
            voice.voice_event.connect(event)
            voice._synthesize("A spoken reply", voice._speaking_id, engine="pocket")
            synthesize.assert_called_once_with("A spoken reply")
            event.assert_called_once_with("audio", voice._speaking_id, audio)

    def test_whisper_transcribes_buffered_audio_once_on_release(self):
        from types import SimpleNamespace

        from PyQt6.QtTest import QTest

        from step_desklet.win11.voice import VoiceController
        from step_desklet.win11.voice_models import ASSETS, STT

        self.assertLess(ASSETS[0][-1], 370_000_000)
        submit = Mock(return_value=True)
        voice = VoiceController(self.app, submit, lambda: None, lambda: False)
        self.addCleanup(voice.shutdown)
        voice.settings = {"enabled": True}
        voice._model_ready = True
        voice._asr_name = STT
        asr_session = Mock(spec=["close", "run"])
        asr_session.run.return_value = SimpleNamespace(text="Whisper recognized this.")
        voice._asr = Mock()
        voice._asr.session.return_value = asr_session
        with patch.object(voice, "_start_audio", return_value=True):
            voice.begin()
            voice._pcm_pending.extend([0.2] * 16000)
            voice._peak = 0.2
            asr_session.run.assert_not_called()
            voice.release()
            for _ in range(30):
                if submit.called:
                    break
                QTest.qWait(10)
        asr_session.run.assert_called_once()
        self.assertEqual(len(asr_session.run.call_args.args[0]), 16000)
        self.assertEqual(asr_session.run.call_args.kwargs["language"], "auto")
        submit.assert_called_once_with("Whisper recognized this.", "voice")

    def test_upgrade_and_missing_speaker_route_voice_to_windows(self):
        from step_desklet.core.config import validate_settings_config
        from step_desklet.win11.voice import VoiceController

        voice = VoiceController(self.app, Mock(), lambda: None, lambda: False)
        self.addCleanup(voice.shutdown)
        legacy = {"enabled": True, "spokenReplies": True, "speaker": "stale", "ttsEngine": "pocket"}
        voice.settings = validate_settings_config({"voice": legacy})["voice"]
        self.assertEqual(voice.settings["ttsEngine"], "windows")
        current = validate_settings_config({"voice": {**legacy, "ttsEngineExplicit": True}})
        self.assertEqual(current["voice"]["ttsEngine"], "pocket")
        default = Mock()
        default.isNull.return_value = False
        default.isDefault.return_value = True
        with (
            patch.object(voice, "_device", side_effect=[None, default]),
            patch.object(voice, "_speak_on_windows_default") as speak,
        ):
            voice.speak("voice", "The **spoken** reply.")
            speak.assert_called_once_with("The spoken reply.")
            voice.speak("chat", "The typed reply.")
            speak.assert_called_once()

    def test_spoken_reply_splits_at_the_first_sentence_and_bounds_the_following_sections(self):
        from step_desklet.win11.voice import speech_chunks

        text = "Hello there. " + "This exercise has several important steps. " * 12
        chunks = list(speech_chunks(text.strip()))
        self.assertEqual(chunks[0], "Hello there.")
        self.assertEqual(" ".join(chunks), text.strip())
        self.assertTrue(all(0 < len(chunk) <= 160 for chunk in chunks))

    def test_pocket_audio_boosts_quiet_speech_and_rejects_silence(self):
        from step_desklet.win11.voice import audible_pocket_audio

        def wav(samples):
            output = io.BytesIO()
            with wave.open(output, "wb") as speech:
                speech.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                speech.writeframes(samples.tobytes())
            return output.getvalue()

        quiet = array("h", [-300, 400, 600, -800] * 4000)
        with wave.open(io.BytesIO(audible_pocket_audio(wav(quiet))), "rb") as speech:
            boosted = array("h", speech.readframes(speech.getnframes()))
            self.assertEqual(speech.getframerate(), 24000)
        self.assertEqual(len(boosted), len(quiet))
        self.assertGreater(max(map(abs, boosted)), 2000)
        self.assertLessEqual(max(map(abs, boosted)), 31000)
        with self.assertRaisesRegex(ValueError, "too quiet"):
            audible_pocket_audio(wav(array("h", [0] * 2000)))

    def test_markdown_tables_render_as_tables_in_chats_and_the_desklet(self):
        from PyQt6.QtGui import QTextDocument, QTextTable

        from step_desklet.win11.chat_window import ChatsWindow
        from step_desklet.win11.desklet import ZyloDeskletWindow

        profile = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"APPDATA": profile, "LOCALAPPDATA": profile}))
        desklet = ZyloDeskletWindow()
        self.addCleanup(desklet.close)
        store = desklet.chat_store = ChatStore()
        chat, message = store.begin("Compare prices", "groq", "chat")
        reply = "Prices:\n| Item | Cost |\n| :--- | ---: |\n| Pen | 10 |\nPrices may vary."
        store.finish(chat, message, reply, reply, "groq")
        window = ChatsWindow(desklet)
        self.addCleanup(window.close)
        window.show()
        desklet._on_worker_response(reply)
        document = QTextDocument()
        document.setHtml(desklet.response_label.text())
        for rendered in (document, window.viewer.document()):
            tables = [frame for frame in rendered.rootFrame().childFrames() if isinstance(frame, QTextTable)]
            self.assertEqual(len(tables), 1)
            self.assertEqual((tables[0].rows(), tables[0].columns()), (2, 2))
            self.assertEqual(tables[0].format().headerRowCount(), 1)
            self.assertIn("Prices may vary.", rendered.toPlainText())

    def test_chat_icon_actions_and_composer_keys(self):
        from PyQt6.QtCore import Qt, QUrl
        from PyQt6.QtGui import QTextCursor
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QApplication

        from step_desklet.win11.chat_window import ChatsWindow
        from step_desklet.win11.desklet import ZyloDeskletWindow

        profile = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch("step_desklet.win11.chat_window.QWebEngineView", None))
        self.enterContext(patch.dict(os.environ, {"APPDATA": str(profile), "LOCALAPPDATA": str(profile)}))
        desklet = ZyloDeskletWindow()
        self.addCleanup(desklet.close)
        desklet.chat_store = ChatStore(profile / "chats.db", legacy=profile / "unused", boot="100")
        window = ChatsWindow(desklet)
        self.addCleanup(window.close)
        window.show()
        self.assertTrue(window.welcome.isVisible())
        chat, message = desklet.chat_store.begin("Original prompt", "groq", "chat")
        desklet.chat_store.finish(chat, message, "Original **answer**", "Original answer", "groq")
        window._select(chat)
        desklet._settings["chatBubbleColor"] = "#406b60"
        window.refresh()
        self.assertEqual(window.viewer.bubble_color.name(), "#406b60")
        self.assertEqual(len(window.viewer._bubble_blocks), 1)
        self.assertTrue(window.transcript_wrap.isVisible())
        self.assertTrue(window.welcome.isHidden())
        transcript = window.viewer.toPlainText()
        self.assertIn("Original answer", transcript)
        self.assertNotIn("Copy", transcript)
        self.assertNotIn("Edit and resend", transcript)
        copy_route = next(path for path in window._actions if path.startswith("/copy/"))
        window._link(QUrl("https://zylo.invalid" + copy_route))
        self.assertEqual(QApplication.clipboard().text(), "Original **answer**")
        window._link(QUrl(f"https://zylo.invalid/edit/{message}"))
        self.assertEqual(window.composer.toPlainText(), "Original prompt")
        window.composer.setPlainText("Edited")
        window.composer.moveCursor(QTextCursor.MoveOperation.End)
        QTest.keyClick(window.composer, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClicks(window.composer, "second line")
        with patch.object(desklet, "submit_text", return_value=True) as send:
            QTest.keyClick(window.composer, Qt.Key.Key_Return)
            send.assert_called_once_with(
                "Edited\nsecond line", "chat", window.provider.currentData(), chat_id=window._chat_id,
            )
            QTest.keyClick(window.composer, Qt.Key.Key_Enter)
            send.assert_called_once()
        self.assertNotEqual(desklet.chat_store.active(), chat)
        self.assertEqual(desklet.chat_store.messages(chat)[0]["text"], "Original prompt")

    def test_writing_tool_keeps_its_operation_when_retried_or_edited(self):
        from PyQt6.QtCore import QUrl

        from step_desklet.win11.chat_window import ChatsWindow
        from step_desklet.win11.desklet import ZyloDeskletWindow

        profile = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"APPDATA": profile, "LOCALAPPDATA": profile}))
        desklet = ZyloDeskletWindow()
        self.addCleanup(desklet.close)
        store = desklet.chat_store = ChatStore()
        window = ChatsWindow(desklet)
        self.addCleanup(window.close)
        window.show()
        window.composer.setPlainText("Text to improve.")
        with patch("step_desklet.win11.request_process.DeskletRequest.start_request", autospec=True) as request:
            window._run_writing_tool("rewrite")
            self.assertEqual(request.call_args.args[1]["writing_task"], "rewrite")
            self.assertEqual(window.composer.toPlainText(), "")
            row = store.messages(store.active())[0]
            self.assertEqual(row["source"], "writing:rewrite")
            desklet._request.outcome.emit("response", "Improved text.")
            window._retry()
            self.assertEqual(request.call_args.args[1]["writing_task"], "rewrite")
            desklet._request.outcome.emit("response", "Improved text.")
            row = store.messages(store.active())[0]
            window._link(QUrl(f"https://zylo.invalid/edit/{row['id']}"))
            window.composer.setPlainText("New text to improve.")
            window._send()
            self.assertEqual(request.call_args.args[1]["writing_task"], "rewrite")
            self.assertEqual(request.call_args.args[1]["prompt"], "New text to improve.")

    def test_new_chat_stays_on_its_own_thread_and_shows_prompt_and_reply_without_web(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        from step_desklet.win11.chat_window import ChatsWindow
        from step_desklet.win11.desklet import ZyloDeskletWindow

        profile = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {"APPDATA": str(profile), "LOCALAPPDATA": str(profile)}))
        legacy = profile / "legacy.json"
        legacy.write_text('[{"role":"assistant","content":"Imported reply"}]', encoding="utf-8")
        desklet = ZyloDeskletWindow()
        self.addCleanup(desklet.close)
        store = desklet.chat_store = ChatStore(profile / "chats.db", legacy=legacy, boot="100")
        imported = store.list()[0]["id"]
        store.select(imported)
        window = ChatsWindow(desklet)
        self.addCleanup(window.close)
        window.show()
        self.assertIn("Imported reply", window.viewer.toPlainText())
        window.has_math = True
        window.new.click()
        self.assertTrue(window.welcome.isVisible())
        self.assertEqual(store.active(), imported)
        with (
            patch("step_desklet.win11.request_process.DeskletRequest.start_request", autospec=True) as request,
            patch.object(window, "_start_web_view") as start_web,
        ):
            window.composer.setPlainText("Fresh prompt")
            QTest.keyClick(window.composer, Qt.Key.Key_Return)
            created = window._chat_id
            self.assertNotEqual(created, imported)
            self.assertEqual(request.call_args.args[1]["history"], [])
            self.assertIn("Fresh prompt", window.viewer.toPlainText())
            store.select(imported)
            desklet._request.outcome.emit("response", "Fresh reply")
            self.assertEqual(window._chat_id, created)
            self.assertEqual(window.header.text(), "Fresh prompt")
            self.assertIn("Fresh reply", window.viewer.toPlainText())
            self.assertNotIn("Imported reply", window.viewer.toPlainText())
            self.assertEqual(store.messages(imported)[0]["text"], "Imported reply")
            start_web.assert_not_called()
