"""Offline checks of chat continuity and Voice Mode integration."""

import hashlib
import os
import sys
import threading
import unittest
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
                self.assertTrue(setup.values()["voice"]["spokenReplies"])
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
                    speak.assert_called_once_with("spoken", "", reply=True)
                chats.close()
                setup.close()
            finally:
                voice.shutdown()
                desklet.close()

    def test_release_submits_the_final_text_and_resets_the_session_for_next_hold(self):
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
            session = Mock(spec=["run", "close"])
            session.run.return_value = SimpleNamespace(text=text)
            sessions.append(session)
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
                session.run.assert_called_once()
                self.assertEqual(len(session.run.call_args.args[0]), 1)
                session.close.assert_called_once()
                callback.assert_called_once_with(session.run.return_value.text, "voice")

    def test_selected_speaker_uses_windows_speech_audio(self):
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
            voice._synthesize("A spoken reply", voice._speaking_id)
            synthesize.assert_called_once_with("A spoken reply")
            event.assert_called_once_with("audio", voice._speaking_id, audio)

    def test_whisper_transcribes_buffered_audio_once_on_release(self):
        from types import SimpleNamespace

        from PyQt6.QtTest import QTest

        from step_desklet.win11.voice import VoiceController
        from step_desklet.win11.voice_models import ASSETS

        self.assertLess(ASSETS[0][-1], 370_000_000)
        submit = Mock(return_value=True)
        voice = VoiceController(self.app, submit, lambda: None, lambda: False)
        self.addCleanup(voice.shutdown)
        voice.settings = {"enabled": True}
        voice._model_ready = True
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
        self.assertNotIn("language", asr_session.run.call_args.kwargs)
        submit.assert_called_once_with("Whisper recognized this.", "voice")

    def test_upgrade_and_missing_speaker_route_voice_to_windows(self):
        from step_desklet.core.config import validate_settings_config
        from step_desklet.win11.voice import VoiceController

        voice = VoiceController(self.app, Mock(), lambda: None, lambda: False)
        self.addCleanup(voice.shutdown)
        legacy = {"enabled": True, "spokenReplies": True, "speaker": "stale", "ttsEngine": "pocket"}
        voice.settings = validate_settings_config({"voice": legacy})["voice"]
        self.assertNotIn("ttsEngine", voice.settings)
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

    def test_cleanup_removes_only_retired_voice_downloads(self):
        from step_desklet.win11.voice_models import STT, remove_retired_models

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            retired = root / "tts" / "pocket-tts-english-novc-q8_0.gguf"
            older = root / "stt" / "moonshine-streaming-small-Q8_0.gguf"
            whisper = root / "stt" / STT
            custom = root / "tts" / "personal-model.gguf"
            for file in (retired, older, whisper, custom):
                file.parent.mkdir(exist_ok=True)
                file.touch()
            remove_retired_models(root)
            remove_retired_models(root)
            self.assertFalse(retired.exists())
            self.assertFalse(older.exists())
            self.assertTrue(whisper.exists())
            self.assertTrue(custom.exists())

    def test_desklet_reset_stops_playback_and_discards_queued_audio(self):
        from step_desklet.win11.tray_app import ZyloWin11Controller

        profile = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"APPDATA": profile, "LOCALAPPDATA": profile}))
        controller = ZyloWin11Controller(self.app)
        self.addCleanup(controller.shutdown_cleanup)
        voice = controller.voice
        process = Mock()
        process.poll.return_value = None
        voice._native_voice = process
        voice._audio_queue.append(b"queued")
        previous = voice._speaking_id
        with patch.object(voice.player, "stop") as stop:
            controller.desklet.clear_btn.click()
            stop.assert_called_once()
        process.kill.assert_called_once()
        voice._on_event("audio", previous, b"late")
        self.assertEqual(voice._audio_queue, [])
        self.assertIsNone(voice._native_voice)

    def test_desklet_holds_spoken_text_until_native_end_or_last_audio_chunk(self):
        from PyQt6.QtMultimedia import QMediaPlayer

        from step_desklet.win11.tray_app import ZyloWin11Controller

        profile = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"APPDATA": profile, "LOCALAPPDATA": profile}))
        controller = ZyloWin11Controller(self.app)
        controller.desklet.show()
        self.addCleanup(controller.shutdown_cleanup)
        desklet, voice = controller.desklet, controller.voice
        voice.settings = {"spokenReplies": True}
        device = Mock()
        device.isNull.return_value = False
        with (
            patch("step_desklet.win11.request_process.DeskletRequest.start_request"),
            patch.object(voice, "_device", return_value=device),
            patch.object(voice, "_speak_on_windows_default") as native,
            patch.object(voice._tts_jobs, "put"),
            patch.object(voice.output, "setDevice"),
            patch.object(voice.player, "setSourceDevice"),
            patch.object(voice.player, "play"),
        ):
            device.isDefault.return_value = True
            self.assertTrue(desklet.submit_text("First question", "voice"))
            desklet._request.outcome.emit("response", "Native spoken answer.")
            native.assert_called_once_with("Native spoken answer.")
            self.assertFalse(desklet._autoclear_timer.isActive())
            desklet._clear_response_only()
            self.assertEqual(desklet.response_label.text(), "Native spoken answer.")
            voice._on_event("native_done", voice._speaking_id - 1, None)
            self.assertFalse(desklet._autoclear_timer.isActive())
            voice._on_event("native_done", voice._speaking_id, None)
            self.assertTrue(desklet._autoclear_timer.isActive())

            device.isDefault.return_value = False
            self.assertTrue(desklet.submit_text("Second question", "voice"))
            desklet._request.outcome.emit("response", "Audio spoken answer.")
            ident = voice._speaking_id
            voice._on_event("audio", ident, b"first chunk")
            voice._play_next(QMediaPlayer.MediaStatus.EndOfMedia)
            self.assertFalse(desklet._autoclear_timer.isActive())
            voice._on_event("audio", ident, b"final chunk")
            voice._on_event("audio_done", ident, None)
            self.assertFalse(desklet._autoclear_timer.isActive())
            voice._play_next(QMediaPlayer.MediaStatus.EndOfMedia)
            self.assertTrue(desklet._autoclear_timer.isActive())

            self.assertTrue(desklet.submit_text("Typed question", "desklet"))
            desklet._request.outcome.emit("response", "Typed answer.")
            self.assertTrue(desklet._autoclear_timer.isActive())

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

    def test_welcome_is_centered_and_first_send_slides_composer(self):
        from PyQt6.QtCore import QPoint

        from step_desklet.win11.chat_window import ChatsWindow
        from step_desklet.win11.desklet import ZyloDeskletWindow

        profile = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, APPDATA=profile, LOCALAPPDATA=profile))
        self.enterContext(patch("step_desklet.win11.chat_window.QWebEngineView", None))
        desklet = ZyloDeskletWindow()
        self.addCleanup(desklet.close)
        desklet.chat_store = ChatStore()
        window = ChatsWindow(desklet)
        self.addCleanup(window.close)
        window.show()
        window.workspace.motion_enabled = False
        for width, height in ((760, 510), (1240, 810)):
            window.resize(width, height)
            window.sidebar_toggle.click()
            self.app.processEvents()
            pane = window.welcome.parentWidget()
            frame_bottom = window.composer_frame.mapTo(pane, QPoint(0, window.composer_frame.height())).y()
            content_top = window.header.parentWidget().geometry().bottom() + 1
            content_bottom = pane.layout().contentsRect().bottom() + 1
            self.assertLessEqual(abs(window.welcome.y() + frame_bottom - content_top - content_bottom), 1)

        window.workspace.motion_enabled = True
        with patch("step_desklet.win11.request_process.DeskletRequest.start_request"):
            window.composer.setPlainText("First message")
            self.app.processEvents()
            start = window.composer_frame.mapTo(window.workspace, QPoint())
            window._send()
            origin, ghost, welcome = window._send_ghosts
            self.assertEqual(origin, start)
            self.assertEqual(ghost.pos(), start)
            self.assertEqual(ghost.pixmap().toImage().pixelColor(0, 0).alpha(), 0)
            target = window.composer_frame.mapTo(window.workspace, QPoint())
            window._send_animation.setCurrentTime(window._send_animation.duration() // 2)
            self.assertLess(start.y(), ghost.pos().y())
            self.assertLess(ghost.pos().y(), target.y())
            self.assertLess(welcome.graphicsEffect().opacity(), 1)
            window._send_animation.setCurrentTime(window._send_animation.duration())
            self.assertIsNone(window._send_ghosts)
            first = window.viewer.prompt_started
            desklet._request.outcome.emit("response", "First reply")
            window.composer.setPlainText("Follow-up")
            window._send()
            self.assertGreater(window.viewer.prompt_started, first)
            self.assertIsNone(window._send_ghosts)
            desklet._request.outcome.emit("response", "Second reply")
            window._new()
            window.workspace.motion_enabled = False
            window.composer.setPlainText("Reduced motion")
            window._send()
            self.assertIsNone(window._send_ghosts)
            self.assertIsNone(window.viewer.prompt_started)

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

    def test_chats_lays_out_new_replies_in_a_long_transcript_after_send_and_resize(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        from step_desklet.win11.chat_window import ChatsWindow
        from step_desklet.win11.desklet import ZyloDeskletWindow

        profile = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, APPDATA=profile, LOCALAPPDATA=profile))
        self.enterContext(patch("step_desklet.win11.chat_window.QWebEngineView", None))
        desklet = ZyloDeskletWindow()
        self.addCleanup(desklet.close)
        store = desklet.chat_store = ChatStore()
        for number in range(12):
            chat, message = store.begin(f"Earlier question {number}", "freellmapi", "chat")
            answer = "An earlier paragraph with enough text to span several lines. " * 4
            store.finish(chat, message, answer, answer, "freellmapi")
        window = ChatsWindow(desklet)
        self.addCleanup(window.close)
        window.show()
        with patch("step_desklet.win11.request_process.DeskletRequest.start_request", autospec=True):
            for number in range(3):
                window.composer.setPlainText(f"Follow-up {number}")
                QTest.keyClick(window.composer, Qt.Key.Key_Return)
                marker = f"Visible reply {number}"
                desklet._request.outcome.emit("response", f"{marker}\n\n" + ("Reply details.\n\n" * 20))
                self.assertIn(marker, desklet.response_label.text())
                self.assertIn(marker, store.messages(chat)[-1]["text"])
        for width in (1160, 820, 1000):
            window.resize(width, 780)
            self.app.processEvents()
            scrollbar = window.viewer.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            self.app.processEvents()
            document = window.viewer.document()
            for number in range(3):
                block = document.find(f"Visible reply {number}").block()
                self.assertTrue(block.isValid())
                self.assertGreater(block.layout().lineCount(), 0)

    def test_unpaired_bold_in_provider_lists_renders_in_both_views(self):
        from PyQt6.QtGui import QTextDocument

        from step_desklet.win11.chat_window import ChatsWindow, normalize_markdown
        from step_desklet.win11.desklet import ZyloDeskletWindow

        profile = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, APPDATA=profile, LOCALAPPDATA=profile))
        desklet = ZyloDeskletWindow()
        self.addCleanup(desklet.close)
        store = desklet.chat_store = ChatStore()
        chat, message = store.begin("Suggest a microphone", "freellmapi", "chat")
        reply = "**Lavalier / Clip-on\n- BOYA BY-M1** \u2013 Wired\n- **Second microphone** \u2013 *Wireless*"
        store.finish(chat, message, reply, reply, "freellmapi")
        window = ChatsWindow(desklet)
        self.addCleanup(window.close)
        window.show()
        desklet._on_worker_response(reply)
        document = QTextDocument()
        document.setHtml(desklet.response_label.text())
        for rendered in (document, window.viewer.document()):
            self.assertNotIn("**", rendered.toPlainText())
            for label in ("Lavalier / Clip-on", "BOYA BY-M1", "Second microphone"):
                cursor = rendered.find(label)
                self.assertFalse(cursor.isNull())
                self.assertGreaterEqual(cursor.charFormat().fontWeight(), 700)
            self.assertTrue(rendered.find("Wireless").charFormat().fontItalic())
        code = "~~~text\n**Lavalier / Clip-on\n- BOYA BY-M1** \u2013 Wired\n~~~"
        escaped = r"- BOYA BY-M1\** - Wired"
        self.assertEqual(normalize_markdown(code + "\n" + escaped), code + "\n" + escaped)

    def test_live_chat_focus_glow_and_reply_reveal(self):
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QWheelEvent
        from PyQt6.QtTest import QTest

        from step_desklet.win11.chat_window import ChatsWindow
        from step_desklet.win11.desklet import ZyloDeskletWindow

        profile = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, APPDATA=profile, LOCALAPPDATA=profile))
        self.enterContext(patch("step_desklet.win11.chat_window.QWebEngineView", None))
        desklet = ZyloDeskletWindow()
        self.addCleanup(desklet.close)
        store = desklet.chat_store = ChatStore()
        settings = {"accentMode": "gradient", "gradientStart": "#ff7c14",
                    "gradientMid": "#db2777", "gradientEnd": "#7c3aed"}
        for number in range(5):
            chat, message = store.begin(f"Older prompt {number}", "groq", "chat")
            answer = "An earlier answer with enough text to make the thread scroll. " * 18
            store.finish(chat, message, answer, answer, "groq")
        window = ChatsWindow(desklet)
        self.addCleanup(window.close)
        window.workspace.motion_enabled = True
        window.show()
        desklet.apply_theme(settings)
        self.assertEqual([color.name() for color in window.workspace.colors],
                         [settings[key] for key in ("gradientStart", "gradientMid", "gradientEnd")])
        with patch("step_desklet.win11.request_process.DeskletRequest.start_request", autospec=True):
            window.composer.setPlainText("A new prompt")
            window._send()
            QTest.qWait(280)
            viewer = window.viewer
            scrollbar = viewer.verticalScrollBar()
            focused_at = viewer._latest_top()
            self.assertGreater(focused_at, 0)
            self.assertEqual(scrollbar.value(), focused_at)
            self.assertFalse(scrollbar.isVisible())
            self.assertEqual(viewer._thinking_block.text(), "Thinking")
            self.assertTrue(window.workspace.busy)
            self.assertGreater(window.workspace.time, 0)
            self.assertTrue(window.progress.isHidden())

            desklet.chat_preview.emit("partial", "Here is the **streaming** reply.")
            QTest.qWait(100)
            self.assertIsNone(viewer._thinking_block)
            self.assertIsNotNone(viewer._reply_block)
            started = viewer.reply_started
            self.assertIsNotNone(started)
            desklet._request.outcome.emit("response", "Here is the **finished** reply.")
            self.assertEqual(viewer.reply_started, started)
            self.assertEqual(scrollbar.value(), focused_at)
            self.assertIn("Here is the finished reply.", viewer.toPlainText())
            self.assertIn("Older prompt 0", viewer.toPlainText())
            self.assertFalse(window.workspace.busy)

        viewport = viewer.viewport()
        point = viewport.rect().center()
        scroll_up = QWheelEvent(
            QPointF(point), QPointF(viewport.mapToGlobal(point)), QPoint(), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )
        self.app.sendEvent(viewport, scroll_up)
        QTest.qWait(250)
        self.assertLess(scrollbar.value(), focused_at)
        previous = scrollbar.value()
        QTest.keyClick(viewer, Qt.Key.Key_PageUp)
        self.assertLess(scrollbar.value(), previous)

        window.showMinimized()
        self.app.processEvents()
        self.assertFalse(window.workspace._timer.isActive())
        window.showNormal()
        self.app.processEvents()
        self.assertTrue(window.workspace._timer.isActive())
        window.close()
        self.assertFalse(window.workspace._timer.isActive())

    def test_token_pacing_and_async_title_use_the_chosen_provider(self):
        from PyQt6.QtTest import QTest

        from step_desklet.win11.chat_window import ChatsWindow
        from step_desklet.win11.desklet import ZyloDeskletWindow

        profile = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, APPDATA=profile, LOCALAPPDATA=profile))
        self.enterContext(patch("step_desklet.win11.chat_window.QWebEngineView", None))
        desklet = ZyloDeskletWindow()
        self.addCleanup(desklet.close)
        store = desklet.chat_store = ChatStore()
        window = ChatsWindow(desklet)
        self.addCleanup(window.close)
        window.show()
        window.workspace.motion_enabled = True
        expanded_width = window._sidebar_pane.width()
        card_width = window.sidebar.width()
        self.assertEqual(window.items.verticalScrollBar().sizeHint().width(), 4)
        self.assertGreater(window.sidebar.x(), 0)
        self.assertGreater(window.sidebar.y(), 0)
        self.assertLess(card_width, expanded_width)
        window.sidebar_toggle.click()
        window._sidebar_animation.setCurrentTime(125)
        self.assertTrue(window.workspace.backdrop_paused)
        self.assertTrue(window.sidebar.isVisible())
        self.assertGreater(window._sidebar_pane.width(), 0)
        self.assertLess(window._sidebar_pane.width(), expanded_width)
        self.assertEqual(window.sidebar.width(), card_width)

        window.sidebar_toggle.click()
        window._sidebar_animation.setCurrentTime(window._sidebar_animation.duration())
        self.assertEqual(window._sidebar_pane.width(), expanded_width)
        self.assertFalse(window.workspace.backdrop_paused)
        window.sidebar_toggle.click()
        window._sidebar_animation.setCurrentTime(window._sidebar_animation.duration())
        self.assertFalse(window.sidebar.isVisible())

        window.sidebar_toggle.click()
        window._sidebar_animation.setCurrentTime(window._sidebar_animation.duration())
        self.assertTrue(window.sidebar.isVisible())
        provider = "groq"
        window.provider.setCurrentIndex(window.provider.findData(provider))
        with patch("step_desklet.win11.request_process.DeskletRequest.start_request", autospec=True) as start:
            window.composer.setPlainText("Tell me about high-speed rail")
            window._send()
            chat = store.active()
            self.assertTrue(start.call_args.args[1]["preview"])
            for token in ("Fast", " **trains**", " connect", " cities. "):
                desklet._request.preview.emit("token", token)
            window._reveal_tokens()
            self.assertIn("Fast", window.viewer.toPlainText())
            self.assertNotIn(" cities.", window.viewer.toPlainText())
            with patch.object(window.viewer, "setHtml", wraps=window.viewer.setHtml) as reload:
                window._reveal_tokens()
                reload.assert_not_called()
            preview = window.viewer.document().find("trains")
            self.assertFalse(preview.isNull())
            self.assertGreater(preview.charFormat().fontWeight(), 400)

            desklet._request.outcome.emit("response", "Fast **trains** connect cities.")
            self.assertTrue(window._draining_tokens)
            self.assertTrue(window.workspace.busy)
            title_request, payload = start.call_args.args
            self.assertEqual(payload["operation"], "title")
            self.assertEqual(payload["provider"], provider)
            self.assertEqual(window.header.text(), "Tell me about high-speed rail")
            title_request.outcome.emit("response", "High-speed rail")
            self.assertEqual(window.header.text(), "High-speed rail")
            self.assertTrue(window.workspace.busy)
            with patch.object(window.viewer, "setHtml", wraps=window.viewer.setHtml) as reload:
                QTest.qWait(250)
                reload.assert_not_called()
            self.assertFalse(window._draining_tokens)
            self.assertFalse(window.workspace.busy)
            self.assertEqual(store.messages(chat)[-1]["text"], "Fast **trains** connect cities.")
            doc = window.viewer.document()
            self.assertEqual(doc.find("Fast trains connect cities.").charFormat().fontFamilies(),
                             ["Segoe UI"])
            self.assertEqual(doc.find("Tell me about high-speed rail").charFormat().fontFamilies(),
                             ["Segoe UI"])
            self.assertEqual(len(window.viewer._action_rows), 2)

            window.header_new.click()
            window.composer.setPlainText("Fallback title prompt")
            window._send()
            desklet._request.outcome.emit("response", "A new reply.")
            title_request, _ = start.call_args.args
            title_request.outcome.emit("error", "No title available.")
            self.assertEqual(window.header.text(), "Fallback title prompt")
            title_request.outcome.emit("response", "Late title")
            self.assertEqual(window.header.text(), "Late title")
            title_request.outcome.emit("response", "Duplicate result")
            self.assertEqual(window.header.text(), "Late title")

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
