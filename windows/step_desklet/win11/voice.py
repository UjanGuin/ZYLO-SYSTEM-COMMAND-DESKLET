"""Push-to-talk capture, local inference, and playback for Windows."""

import base64
import ctypes
import io
import os
import queue
import re
import secrets
import socket
import subprocess
import threading
import time
import wave
from array import array
from ctypes import wintypes

import requests
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtMultimedia import QAudio, QAudioFormat, QAudioOutput, QAudioSource, QMediaDevices, QMediaPlayer
from PyQt6.QtWidgets import QDialog, QLabel, QInputDialog, QProgressBar, QPushButton, QVBoxLayout

from step_desklet.win11.voice_models import (
    ASSETS, LEGACY_STT, STT, TTS, VOICE_PRESET, VoiceDownloads, engine_location, file_hash, install_models, models_root,
)


DEFAULT_SHORTCUT = "Meta+Space"
FALLBACK_SHORTCUT = "Ctrl+Alt+Space"
FIRST_SPEECH_CHUNK = 80
SPEECH_CHUNK = 160
SPEECH_END = re.compile(r"[.!?](?:[\"')\]]+)?(?=\s|$)")
NATIVE_SPEECH_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
$text = [Console]::In.ReadToEnd()
try {
    $speaker = New-Object -ComObject SAPI.SpVoice
    $null = $speaker.Speak($text, 16)
} catch {
    Add-Type -AssemblyName System.Speech
    $synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $synthesizer.SetOutputToDefaultAudioDevice()
        $synthesizer.Speak($text)
    } finally {
        $synthesizer.Dispose()
    }
}
"""
WINDOWS_SPEECH_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
$path = [IO.Path]::GetTempFileName()
try {
    $speaker = New-Object -ComObject SAPI.SpVoice
    $audio = New-Object -ComObject SAPI.SpFileStream
    $audio.Open($path, 3, $false)
    try {
        $speaker.AudioOutputStream = $audio
        $null = $speaker.Speak([Console]::In.ReadToEnd(), 16)
    } finally {
        $audio.Close()
    }
    $wave = [IO.File]::ReadAllBytes($path)
    [Console]::OpenStandardOutput().Write($wave, 0, $wave.Length)
} finally {
    [IO.File]::Delete($path)
}
"""


def win_shortcut(text):
    sequence = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
    if sequence.count() != 1:
        raise ValueError("Choose one keyboard combination.")
    combo = sequence[0]
    qt_modifiers = combo.keyboardModifiers()
    modifiers = keys = 0
    held = []
    for qt_key, flag, vk in (
        (Qt.KeyboardModifier.AltModifier, 1, 0x12),
        (Qt.KeyboardModifier.ControlModifier, 2, 0x11),
        (Qt.KeyboardModifier.ShiftModifier, 4, 0x10),
        (Qt.KeyboardModifier.MetaModifier, 8, 0x5B),
    ):
        if qt_modifiers & qt_key:
            modifiers |= flag
            held.append((0x5B, 0x5C) if flag == 8 else (vk,))
    key = int(combo.key())
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24 and key != Qt.Key.Key_F12:
        keys = 0x70 + key - Qt.Key.Key_F1
    elif key == Qt.Key.Key_Space or ord("A") <= key <= ord("Z") or ord("0") <= key <= ord("9"):
        keys = key
    if not modifiers or not keys:
        raise ValueError("Use a modifier with a letter, number, space, or function key other than F12.")
    return modifiers, keys, held


def speech_chunks(text):
    limit = FIRST_SPEECH_CHUNK
    while text:
        endings = list(SPEECH_END.finditer(text[:limit]))
        if endings:
            end = endings[0].end() if limit == FIRST_SPEECH_CHUNK else endings[-1].end()
        elif len(text) <= limit:
            end = len(text)
        else:
            end = text.rfind(" ", 0, limit + 1)
            if end <= 0:
                end = limit
        yield text[:end].strip()
        text = text[end:].lstrip()
        limit = SPEECH_CHUNK


def audible_pocket_audio(payload):
    with wave.open(io.BytesIO(payload), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("Pocket TTS returned an unsupported sample format.")
        samples = array("h", source.readframes(source.getnframes()))
        params = source.getparams()

    if not samples:
        raise ValueError("Pocket TTS returned no audio samples.")
    rms = (sum(sample * sample for sample in samples) / len(samples)) ** 0.5
    if rms < 256:
        raise ValueError("Pocket TTS returned audio too quiet to play.")
    peak = max(map(abs, samples))
    gain = min(3.0, 3277 / rms, 31000 / peak)
    if gain <= 1.0:
        return payload
    boosted = array("h", (round(sample * gain) for sample in samples))
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setparams(params)
        destination.writeframes(boosted.tobytes())
    return output.getvalue()


class PcmConverter:
    def __init__(self, audio_format):
        self.format = audio_format
        self.index = 0
        self.next_index = 0.0
        self.tail = b""

    def feed(self, raw):
        formats = {
            QAudioFormat.SampleFormat.Int16: ("h", 32768.0, 0),
            QAudioFormat.SampleFormat.Int32: ("i", 2147483648.0, 0),
            QAudioFormat.SampleFormat.UInt8: ("B", 128.0, 128),
            QAudioFormat.SampleFormat.Float: ("f", 1.0, 0),
        }
        kind, scale, zero = formats[self.format.sampleFormat()]
        channels = self.format.channelCount()
        raw = self.tail + raw
        frame_size = self.format.bytesPerFrame()
        length = len(raw) - len(raw) % frame_size
        self.tail = raw[length:]
        values = array(kind)
        values.frombytes(raw[:length])
        output = array("f")
        step = self.format.sampleRate() / 16000
        frames = len(values) // channels
        while self.next_index < self.index + frames:
            pos = (int(self.next_index) - self.index) * channels
            sample = (sum(values[pos:pos + channels]) / channels - zero) / scale
            output.append(max(-1.0, min(1.0, sample)))
            self.next_index += step
        self.index += frames
        return output


class VoiceController(QObject):
    voice_event = pyqtSignal(str, int, object)
    status = pyqtSignal(str)
    test_level = pyqtSignal(int)

    def __init__(self, app, submit, open_settings, busy):
        super().__init__(app)
        self.app = app
        self.submit = submit
        self.open_settings = open_settings
        self.busy = busy
        self.settings = {}
        self.hotkey_id = None
        self.held_keys = []
        self.hotkey_text = ""
        self._hotkey_result = "Voice mode is off."
        self._next_id = 0x6400
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                           wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self._hotkey_jobs = queue.Queue()
        self.keys_timer = QTimer(self)
        self.keys_timer.setInterval(20)
        self.keys_timer.timeout.connect(self._poll_release)
        self.overlay = QDialog()
        self.overlay.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                                    | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.overlay.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.overlay.setWindowTitle("liNi Voice")
        self.overlay.setStyleSheet("QDialog{background:#1c2030;color:#eef2ff} QLabel{color:#eef2ff;padding:4px}")
        self.overlay.resize(440, 150)
        layout = QVBoxLayout(self.overlay)
        self.heading = QLabel("Listening · Release to send")
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.transcript = QLabel()
        self.transcript.setTextFormat(Qt.TextFormat.PlainText)
        self.transcript.setWordWrap(True)
        self.meter = QProgressBar()
        self.meter.setTextVisible(False)
        layout.addWidget(self.heading)
        layout.addWidget(self.transcript)
        layout.addWidget(self.meter)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.cancel)
        layout.addWidget(cancel)
        self.overlay.rejected.connect(self.cancel)
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.player.mediaStatusChanged.connect(self._play_next)
        self.player.errorOccurred.connect(self._play_error)
        self._buffer = None
        self._audio_queue = []
        self._speaking_id = 0
        self._native_voice = None
        self._native_lock = threading.Lock()
        self._pending_audio_text = ""
        self._play_start_timer = QTimer(self)
        self._play_start_timer.setSingleShot(True)
        self._play_start_timer.timeout.connect(lambda: self._play_error(None, "The output device did not start playing."))
        self.player.playbackStateChanged.connect(self._playback_state)
        self._capture_id = 0
        self._capture_submit = None
        self._source = None
        self._io = None
        self._state = "idle"
        self._test = False
        self._peak = 0.0
        self._model_ready = False
        self._stt_error = ""
        self._tts_ready = False
        self._warm_stt = False
        self._warm_tts = False
        self._closed = False
        self._asr = None
        self._asr_name = ""
        self._session = None
        self._stream = None
        self._batch_pcm = array("f")
        self._refresh_recognizer = False
        self._tts_process = None
        self._tts_address = None
        self._tts_token = None
        self._tts_lock = threading.Lock()
        self._stop = threading.Event()
        self._jobs = queue.Queue()
        self._tts_jobs = queue.Queue()
        self.voice_event.connect(self._on_event)
        threading.Thread(target=self._run_hotkeys, daemon=True, name="liNi shortcut").start()
        threading.Thread(target=self._run_jobs, args=(self._jobs,), daemon=True, name="liNi transcription").start()
        threading.Thread(target=self._run_jobs, args=(self._tts_jobs,), daemon=True, name="liNi speech").start()
        VoiceDownloads.for_app().finished.connect(self._download_finished)

    @staticmethod
    def inputs():
        return QMediaDevices.audioInputs()

    @staticmethod
    def outputs():
        return QMediaDevices.audioOutputs()

    @staticmethod
    def device_key(device):
        return bytes(device.id()).hex()

    def _device(self, key, output=False):
        devices = self.outputs() if output else self.inputs()
        if key:
            return next((d for d in devices if self.device_key(d) == key), None)
        return QMediaDevices.defaultAudioOutput() if output else QMediaDevices.defaultAudioInput()

    def reload(self, settings):
        self.settings = settings.get("voice", {})
        if not self.settings.get("enabled"):
            self.cancel()
            self._hotkey_jobs.put("")
            self._hotkey_result = "Voice mode is off."
            self._model_ready = self._tts_ready = False
            self._jobs.put((self._unload_stt, "stt_closed", 0))
            self._stop_tts_server()
        else:
            if self.settings.get("ttsEngine") != "pocket":
                self._tts_ready = False
                self._stop_tts_server()
            if not self.settings.get("globalShortcut", True):
                self._hotkey_jobs.put("")
                self._hotkey_result = "Global shortcut is off."
            else:
                key = self.settings.get("shortcut", DEFAULT_SHORTCUT)
                self._hotkey_jobs.put(key)
            self._warm_models()
        self.status.emit(self._hotkey_result)

    def _unregister(self):
        if self.hotkey_id is not None:
            self.user32.UnregisterHotKey(None, self.hotkey_id)
        self.hotkey_id = None
        self.hotkey_text = ""

    def _run_hotkeys(self):
        message = wintypes.MSG()
        self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        while True:
            try:
                choice = self._hotkey_jobs.get(timeout=0.02 if self.hotkey_id is not None else None)
            except queue.Empty:
                choice = False
            if choice is None:
                self._unregister()
                return
            if choice is not False:
                if not choice:
                    self._unregister()
                else:
                    self.voice_event.emit("shortcut_status", 0, self._register(choice))
            if self.hotkey_id is not None:
                while self.user32.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1):
                    if message.wParam == self.hotkey_id:
                        self.voice_event.emit("hotkey", 0, None)

    def _register(self, text):
        candidates = [text]
        if text == DEFAULT_SHORTCUT and self.hotkey_id is None:
            candidates.append(FALLBACK_SHORTCUT)
        for candidate in candidates:
            if candidate == self.hotkey_text:
                return f"Shortcut active: {candidate.replace('Meta', 'Win')}"
            try:
                mods, key, held = win_shortcut(candidate)
            except ValueError as error:
                return str(error)
            self._next_id += 1
            if self.user32.RegisterHotKey(None, self._next_id, mods | 0x4000, key):
                self._unregister()
                self.hotkey_id = self._next_id
                self.hotkey_text = candidate
                self.held_keys = [(key,), *held]
                return "Win+Space unavailable. Using Ctrl+Alt+Space." if candidate != text else (
                    f"Shortcut active: {candidate.replace('Meta', 'Win')}"
                )
        if self.hotkey_text:
            return f"Shortcut unavailable. Still using {self.hotkey_text.replace('Meta', 'Win')}."
        return "Shortcut unavailable. Choose another shortcut in Setup → Voice."

    def _download_finished(self, ok, _):
        if self.settings.get("enabled") and (ok or (models_root() / "stt" / STT).is_file()):
            if self._state == "idle":
                self._warm_models()
            else:
                self._refresh_recognizer = True

    def _warm_models(self):
        root = models_root()
        available = next((name for name in (STT, LEGACY_STT[1]) if (root / "stt" / name).is_file()), "")
        if available and not self._warm_stt and (not self._model_ready or available != self._asr_name):
            self._warm_stt = True
            self._stt_error = ""
            self._jobs.put((self._load_stt, "stt_ready", 0))
        if (self.settings.get("ttsEngine") == "pocket" and (root / "tts" / TTS).is_file()
                and engine_location(root) and not (self._tts_ready or self._warm_tts)):
            self._warm_tts = True
            threading.Thread(
                target=self._run_job, args=(self._start_tts_server, "tts_ready", 0),
                daemon=True, name="liNi speech warmup",
            ).start()

    def _run_jobs(self, jobs):
        while True:
            item = jobs.get()
            if item is None:
                return
            self._run_job(*item)

    def _run_job(self, operation, label, ident):
        try:
            result = operation()
            if label and not self._stop.is_set():
                self.voice_event.emit(label, ident, result)
        except Exception as error:
            if not self._stop.is_set():
                self.voice_event.emit("error:" + label, ident, str(error))

    @staticmethod
    def _checked_model(index):
        folder, name, _, sha, _ = ASSETS[index] if isinstance(index, int) else index
        file = models_root() / folder / name
        if file_hash(file) != sha:
            raise ValueError("Model verification failed. Download the voice models again in Setup.")
        return file

    def _load_stt(self):
        from transcribe_cpp import Model

        error = None
        for asset in (ASSETS[0], LEGACY_STT):
            if not (models_root() / asset[0] / asset[1]).is_file():
                continue
            if self._asr is not None and self._asr_name == asset[1]:
                return self._asr_name
            try:
                model = self._checked_model(asset)
                try:
                    replacement = Model(model)
                except (OSError, RuntimeError):
                    replacement = Model(model, backend="cpu")
            except (OSError, RuntimeError, ValueError) as failure:
                error = failure
                continue
            self._unload_stt()
            self._asr = replacement
            self._asr_name = asset[1]
            return self._asr_name
        if self._asr is not None:
            return self._asr_name
        raise RuntimeError(str(error) if error else "Download the recognition model in Voice settings.")

    def _unload_stt(self):
        self._close_stream()
        if self._asr:
            self._asr.close()
            self._asr = None
        self._asr_name = ""

    def _close_stream(self):
        stream, self._stream = self._stream, None
        session, self._session = self._session, None
        self._batch_pcm = array("f")
        try:
            if stream is not None:
                stream.reset()
        finally:
            if session is not None:
                session.close()

    def _begin_stream(self):
        self._close_stream()
        self._session = self._asr.session()
        if self._asr_name != STT:
            self._stream = self._session.stream()

    def _feed_stream(self, pcm):
        if self._stream is not None:
            self._stream.feed(pcm)
            preview = self._stream.text()
            return preview.display.strip()
        if self._session is not None:
            self._batch_pcm.extend(pcm)
        return ""

    def _finish_stream(self):
        try:
            if self._stream is not None:
                self._stream.finalize()
                return self._stream.snapshot().text.strip()
            if self._session is not None and self._batch_pcm:
                return self._session.run(self._batch_pcm, language="auto").text.strip()
            return ""
        finally:
            self._close_stream()

    def _notice(self, title, detail="", *, transient=False):
        self.heading.setText(title)
        self.transcript.setText(detail)
        if transient:
            self.meter.setValue(0)
        screen = self.app.primaryScreen()
        if screen and not self.overlay.isVisible():
            area = screen.availableGeometry()
            self.overlay.move(area.center().x() - self.overlay.width() // 2, area.bottom() - 190)
        self.overlay.show()
        if transient:
            QTimer.singleShot(3300, lambda: self.overlay.hide() if self._state == "idle" else None)

    def begin(self, global_key=False, *, submit=None):
        if self._state != "idle" or self._test or not self.settings.get("enabled"):
            return
        if not self._model_ready:
            message = "Speech model is loading." if self._warm_stt else (
                self._stt_error or "Download voice models in Setup → Voice."
            )
            self._notice("Voice unavailable", message, transient=True)
            return
        if self.busy():
            self._notice("liNi is working", "Wait for the current reply or stop it before speaking.", transient=True)
            return
        self.stop_speaking()
        self._capture_id += 1
        self._capture_submit = submit
        self._peak = 0
        self._pcm_pending = array("f")
        self._state = "listening"
        self._jobs.put((self._begin_stream, "stream_open", self._capture_id))
        if not self._start_audio(self.settings.get("microphone", "")):
            self.cancel()
            self._notice("Microphone unavailable", "Check the device and microphone permission.", transient=True)
            return
        self._notice("Listening · Release to send")
        if global_key:
            self.keys_timer.start()

    def _start_audio(self, device_key):
        device = self._device(device_key)
        if device is None or device.isNull():
            return False
        audio_format = QAudioFormat()
        audio_format.setSampleRate(16000)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(audio_format):
            audio_format = device.preferredFormat()
        if not audio_format.isValid() or audio_format.bytesPerFrame() <= 0:
            return False
        self._converter = PcmConverter(audio_format)
        self._source = QAudioSource(device, audio_format, self)
        self._source.stateChanged.connect(self._audio_state)
        self._io = self._source.start()
        if self._io is None or self._source.error() != QAudio.Error.NoError:
            self._stop_audio()
            return False
        self._io.readyRead.connect(self._read_audio)
        return True

    def _audio_state(self, state):
        source = self._source
        if source and state == QAudio.State.StoppedState and source.error() != QAudio.Error.NoError:
            self.cancel()
            self._notice("Microphone disconnected", "No recording was sent.", transient=True)

    def _read_audio(self):
        if self._io is None:
            return
        pcm = self._converter.feed(bytes(self._io.readAll()))
        peak = max((abs(x) for x in pcm), default=0)
        level = min(100, int(peak * 140))
        if self._test:
            self.test_level.emit(level)
        elif self._state == "listening":
            self._peak = max(self._peak, peak)
            self.meter.setValue(level)
            self._pcm_pending.extend(pcm)
            if len(self._pcm_pending) >= 1280:
                self._queue_pcm()

    def _queue_pcm(self):
        chunk, self._pcm_pending = self._pcm_pending, array("f")
        if chunk:
            self._jobs.put((lambda: self._feed_stream(chunk), "partial", self._capture_id))

    def _poll_release(self):
        down = self.user32.GetAsyncKeyState
        if down(0x1B) & 0x8000:
            self.cancel()
        elif not all(any(down(vk) & 0x8000 for vk in group) for group in self.held_keys):
            self.release()

    def _stop_audio(self):
        self._io = None
        source, self._source = self._source, None
        if source:
            source.stop()
            source.deleteLater()

    def release(self):
        if self._state != "listening":
            return
        self._read_audio()
        self._state = "finalizing"
        self.keys_timer.stop()
        self._stop_audio()
        if self._peak < 0.006:
            self.cancel()
            self._notice("No speech detected", "Speak a little closer to the microphone and try again.", transient=True)
            return
        self._queue_pcm()
        self.heading.setText("Transcribing…")
        self._jobs.put((self._finish_stream, "transcript", self._capture_id))

    def cancel(self):
        if self._state in ("listening", "finalizing"):
            self._jobs.put((self._close_stream, "", self._capture_id))
        self._capture_id += 1
        self._capture_submit = None
        self._state = "idle"
        self.keys_timer.stop()
        self._stop_audio()
        self._test = False
        self.overlay.hide()
        self.stop_speaking()
        self._refresh_stt_after_capture()

    def _refresh_stt_after_capture(self):
        if self._refresh_recognizer:
            self._refresh_recognizer = False
            if self.settings.get("enabled") and not self._stop.is_set():
                self._warm_models()

    def test_microphone(self, device):
        self.cancel()
        self._test = True
        if not self._start_audio(device):
            self._test = False
            self.status.emit("Microphone unavailable. Check Windows microphone permission.")
            return
        ident = self._capture_id
        QTimer.singleShot(5000, lambda: self.stop_microphone_test() if self._capture_id == ident else None)

    def stop_microphone_test(self):
        if self._test:
            self._stop_audio()
            self._test = False
            self.test_level.emit(0)

    def _on_event(self, label, ident, data):
        if self._closed:
            return
        if label == "shortcut_status":
            if self.settings.get("enabled") and self.settings.get("globalShortcut"):
                self._hotkey_result = str(data)
                self.status.emit(self._hotkey_result)
        elif label == "hotkey":
            if self.settings.get("globalShortcut"):
                self.begin(global_key=True)
        elif label in ("stt_ready", "error:stt_ready"):
            self._warm_stt = False
            self._model_ready = label == "stt_ready" and self.settings.get("enabled", False)
            if self._model_ready:
                model = "Whisper Small" if data == STT else "Moonshine (download Whisper Small to upgrade)"
                self.status.emit(f"{model} ready. " + self._hotkey_result)
            elif label.startswith("error:"):
                self._stt_error = "Speech recognition could not start: " + str(data)
                self.status.emit(self._stt_error)
        elif label in ("tts_ready", "error:tts_ready"):
            self._warm_tts = False
            self._tts_ready = (
                label == "tts_ready" and bool(data)
                and self.settings.get("enabled", False) and self.settings.get("ttsEngine") == "pocket"
            )
            if self._tts_ready:
                self.status.emit("Spoken reply model ready. " + self._hotkey_result)
            elif label.startswith("error:") and self.settings.get("ttsEngine") == "pocket":
                self.status.emit("Using Windows speech while Pocket TTS is unavailable.")
        elif label == "audio" and ident == self._speaking_id:
            self._audio_queue.append(data)
            if self.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState and self._buffer is None:
                self._play_next()
        elif label == "error:audio" and ident == self._speaking_id:
            text = self._pending_audio_text
            self.stop_speaking()
            if text:
                self._speak_on_windows_default(text)
        elif label == "native_done" and ident == self._speaking_id:
            self.status.emit("Spoken reply finished.")
        elif label == "error:native_done" and ident == self._speaking_id:
            self.status.emit(str(data))
            if self._state == "idle":
                self._notice("Spoken reply unavailable", str(data), transient=True)
        elif ident == self._capture_id:
            if label == "partial" and self._state != "idle" and data:
                self.transcript.setText(str(data)[-420:])
            elif label == "transcript" and self._state == "finalizing":
                self._state = "idle"
                self.overlay.hide()
                self._refresh_stt_after_capture()
                submit, self._capture_submit = self._capture_submit or self.submit, None
                text = str(data).strip()
                if not text:
                    self._notice("No speech recognized", "Hold to talk and try speaking again.", transient=True)
                    return
                if self.settings.get("review") and text:
                    text, accepted = QInputDialog.getMultiLineText(None, "Review voice message", "Transcript", text)
                    if not accepted:
                        return
                if text.strip() and not submit(text.strip(), "voice"):
                    self._notice("Message not sent", "The assistant is already working.", transient=True)
            elif label.startswith("error:") and self._state != "idle":
                self.cancel()
                detail = str(data).strip() or "The local speech engine returned an error."
                self.status.emit("Transcription failed: " + detail)
                self._notice("Transcription failed", detail[:260], transient=True)

    @staticmethod
    def _local_session():
        session = requests.Session()
        session.trust_env = False
        return session

    def _start_tts_server(self):
        root = models_root()
        engine = engine_location(root)
        if not engine or self._stop.is_set():
            return False
        model = self._checked_model(1)
        if not install_models(root, lambda *_: None, self._stop, assets=(VOICE_PRESET,)):
            return False
        voice = root / VOICE_PRESET[0] / VOICE_PRESET[1]
        with socket.socket() as address:
            address.bind(("127.0.0.1", 0))
            port = address.getsockname()[1]
        host = f"http://127.0.0.1:{port}"
        token = secrets.token_urlsafe(32)
        env = {**os.environ, "CRISPASR_API_KEYS": token}
        for key in tuple(env):
            if key.endswith(("_API_KEY", "_BACKUP_API_KEY")):
                env.pop(key)
        with self._tts_lock:
            if self._stop.is_set() or not self.settings.get("enabled") or self.settings.get("ttsEngine") != "pocket":
                return False
            if self._tts_process and self._tts_process.poll() is None:
                return bool(self._tts_address)
            process = subprocess.Popen(
                [str(engine), "--server", "--backend", "pocket-tts", "-m", str(model),
                 "--voice", str(voice), "--host", "127.0.0.1", "--port", str(port), "--ws-port", "-1", "--no-gpu"],
                cwd=engine.parent, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._tts_process = process
        try:
            with self._local_session() as session:
                for _ in range(100):
                    if process.poll() is not None or self._stop.wait(0.15):
                        break
                    try:
                        with session.get(host + "/health", timeout=0.7, allow_redirects=False,
                                         headers={"Authorization": "Bearer " + token}) as response:
                            if response.ok and response.json().get("backend") == "pocket-tts":
                                with self._tts_lock:
                                    if self._tts_process is not process or self._stop.is_set():
                                        return False
                                    self._tts_address, self._tts_token = host, token
                                return True
                    except (requests.RequestException, ValueError):
                        pass
            raise RuntimeError("The local Pocket TTS engine did not start.")
        except Exception:
            self._stop_tts_server()
            raise

    def _stop_tts_server(self):
        with self._tts_lock:
            process, self._tts_process = self._tts_process, None
            self._tts_address = self._tts_token = None
            if process and process.poll() is None:
                process.terminate()

    def speak(self, source, text):
        if source == "voice" and self.settings.get("spokenReplies"):
            self._speak(text, self.settings.get("speaker", ""))

    def test_speaker(self, device, engine=None):
        self._speak("Hello. This is liNi's spoken reply test.", device, engine=engine)

    def _speak(self, text, device_key, *, engine=None):
        device = self._device(device_key, output=True)
        if device_key and (device is None or device.isNull()):
            self.status.emit("Saved speaker unavailable. Using the Windows default.")
            device = self._device("", output=True)
        self.stop_speaking()
        tick = chr(96) * 3
        text = re.sub(tick + r"[\s\S]*?" + tick, " Code example is in Chats. ", text)
        text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"[*#_]", "", text)
        text = " ".join(text.split())
        if text:
            engine = engine or self.settings.get("ttsEngine", "windows")
            if engine == "pocket" and not self._tts_ready:
                self.status.emit("Pocket TTS is unavailable. Using Windows speech.")
                engine = "windows"
            if device is None or device.isNull() or (engine == "windows" and device.isDefault()):
                self._speak_on_windows_default(text)
                return
            self.status.emit("Preparing spoken reply.")
            self.output.setDevice(device)
            self._pending_audio_text = text
            ident = self._speaking_id
            self._tts_jobs.put((lambda: self._synthesize(text, ident, engine=engine), "audio_done", ident))

    def _speak_on_windows_default(self, text):
        self.status.emit("Speaking on the Windows default output.")
        ident = self._speaking_id
        threading.Thread(
            target=self._run_job, args=(lambda: self._native_speech(text, ident), "native_done", ident),
            daemon=True, name="liNi Windows speech",
        ).start()

    def _native_speech(self, text, ident):
        with self._native_lock:
            if ident != self._speaking_id or self._stop.is_set():
                return
            process = subprocess.Popen(
                self._windows_speech_command(NATIVE_SPEECH_SCRIPT), stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._native_voice = process
        try:
            process.communicate(text.encode("utf-8"), timeout=max(30, 10 + len(text) // 6))
            if process.returncode and ident == self._speaking_id:
                raise RuntimeError("Windows speech could not speak on the default output.")
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError("Windows speech timed out.") from None
        finally:
            with self._native_lock:
                if self._native_voice is process:
                    self._native_voice = None

    def _synthesize(self, text, ident, *, engine="windows"):
        with self._tts_lock:
            source, token = (self._tts_address, self._tts_token) if engine == "pocket" else (None, None)
        chunks = speech_chunks(text)
        with self._local_session() as session:
            for index, chunk in enumerate(chunks):
                if ident != self._speaking_id or self._stop.is_set():
                    return
                try:
                    audio = None
                    if source and token:
                        try:
                            with session.post(source + "/v1/audio/speech",
                                              headers={"Authorization": "Bearer " + token},
                                              json={"input": chunk, "response_format": "wav"},
                                              timeout=(2, 12 if index == 0 else 16), allow_redirects=False) as reply:
                                reply.raise_for_status()
                                audio = audible_pocket_audio(reply.content)
                        except (requests.RequestException, ValueError, wave.Error, EOFError):
                            source = None
                    if ident != self._speaking_id or self._stop.is_set():
                        return
                    if audio is None:
                        audio = self._windows_speech(chunk)
                    if ident == self._speaking_id:
                        self.voice_event.emit("audio", ident, audio)
                except (OSError, ValueError, subprocess.SubprocessError):
                    self.voice_event.emit("error:audio", ident, "")
                    return

    @staticmethod
    def _windows_speech_command(script):
        powershell = os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        command = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        return [powershell, "-NoProfile", "-NonInteractive", "-Sta", "-EncodedCommand", command]

    @staticmethod
    def _windows_speech(text):
        result = subprocess.run(
            VoiceController._windows_speech_command(WINDOWS_SPEECH_SCRIPT),
            input=text.encode("utf-8"), capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode or not result.stdout.startswith(b"RIFF"):
            raise ValueError("Windows speech did not return audio.")
        return result.stdout

    def _play_next(self, state=None):
        if state is not None and state != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        self._play_start_timer.stop()
        old, self._buffer = self._buffer, None
        self.player.setSource(QUrl())
        if old:
            old.deleteLater()
        if self._audio_queue:
            self._buffer = QBuffer(self)
            self._buffer.setData(QByteArray(self._audio_queue.pop(0)))
            self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            self._play_start_timer.start(6000)
            self.player.setSourceDevice(self._buffer, QUrl("voice.wav"))
            self.player.play()

    def _playback_state(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_start_timer.stop()

    def _play_error(self, _error, detail):
        if not self._buffer:
            return
        text = self._pending_audio_text
        self.stop_speaking()
        if text:
            self._speak_on_windows_default(text)
            return
        self.status.emit("Could not play this spoken reply.")
        if self._state == "idle":
            self._notice("Speaker playback failed", detail or "Check the output device in Voice settings.", transient=True)

    def stop_speaking(self):
        self._speaking_id += 1
        self._pending_audio_text = ""
        self._play_start_timer.stop()
        with self._native_lock:
            process, self._native_voice = self._native_voice, None
            if process and process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
        self._audio_queue.clear()
        self.player.stop()
        self.player.setSource(QUrl())
        if self._buffer:
            self._buffer.deleteLater()
        self._buffer = None

    def shutdown(self):
        self._closed = True
        self._stop.set()
        self.cancel()
        self._hotkey_jobs.put(None)
        self._jobs.put((self._unload_stt, "", 0))
        self._jobs.put(None)
        self._tts_jobs.put(None)
        self._stop_tts_server()
