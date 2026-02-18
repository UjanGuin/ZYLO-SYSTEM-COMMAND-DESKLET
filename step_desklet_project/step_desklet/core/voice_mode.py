import os
import re
import signal
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Set

from PyQt6.QtCore import QObject, pyqtSignal
from stt import transcribe_wav_file
from .worker import StepAIWorker

try:
    from pynput import keyboard
except Exception:
    keyboard = None

STT_SAMPLE_RATE = 16000

TTS_SERVER = "grpc.nvcf.nvidia.com:443"
TTS_FUNCTION_ID = "877104f7-e885-42b9-8de8-f6e4c6303969"
TTS_API_KEY_FALLBACK = "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"
TTS_LANGUAGE = "en-US"
TTS_ENERGETIC_VOICE = "Magpie-Multilingual.EN-US.Aria.Happy"

class GlobalPushToTalk:
    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.RLock()
        self._subscribers: Set[Callable[[bool], None]] = set()
        self._ctrl_held = False
        self._m_held = False
        self._combo_active = False
        self.available = False
        self.error: Optional[str] = None
        self.backend: Optional[str] = None
        self._listener = None
        self._xinput_proc: Optional[subprocess.Popen] = None
        self._xinput_thread: Optional[threading.Thread] = None
        self._ctrl_keycodes = set()
        self._m_keycodes = set()

        if keyboard is not None:
            try:
                self._listener = keyboard.Listener(
                    on_press=self._on_press,
                    on_release=self._on_release,
                )
                self._listener.daemon = True
                self._listener.start()
                self.available = True
                self.backend = "pynput"
            except Exception as exc:
                self.error = str(exc)

        if not self.available:
            self._start_xinput_backend()

    @classmethod
    def instance(cls) -> "GlobalPushToTalk":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def subscribe(self, callback: Callable[[bool], None]):
        with self._lock:
            self._subscribers.add(callback)

    def unsubscribe(self, callback: Callable[[bool], None]):
        with self._lock:
            self._subscribers.discard(callback)

    def _emit(self, is_active: bool):
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(is_active)
            except Exception:
                continue

    def _start_xinput_backend(self):
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            if not self.error:
                self.error = "Wayland session detected (xinput backend requires X11)."
            return
        if not shutil.which("xinput") or not shutil.which("xmodmap"):
            if not self.error:
                self.error = "xinput/xmodmap not available."
            return

        self._ctrl_keycodes, self._m_keycodes = self._load_x11_keycodes()
        if not self._ctrl_keycodes or not self._m_keycodes:
            if not self.error:
                self.error = "Could not resolve keycodes for Ctrl/M."
            return

        try:
            self._xinput_proc = subprocess.Popen(
                ["xinput", "test-xi2", "--root"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            if not self.error:
                self.error = str(exc)
            return

        time.sleep(0.1)
        if self._xinput_proc.poll() is not None:
            if not self.error:
                self.error = "xinput backend failed to start."
            self._xinput_proc = None
            return

        self._xinput_thread = threading.Thread(target=self._xinput_loop, daemon=True)
        self._xinput_thread.start()
        self.available = True
        self.backend = "xinput"

    def _load_x11_keycodes(self):
        ctrl_codes = set()
        m_codes = set()
        result = subprocess.run(
            ["xmodmap", "-pke"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        pattern = re.compile(r"^keycode\s+(\d+)\s*=\s*(.*)$")
        for raw in result.stdout.splitlines():
            match = pattern.match(raw.strip())
            if not match:
                continue
            keycode = int(match.group(1))
            symbols = [tok for tok in match.group(2).split() if tok != "NoSymbol"]
            if not symbols:
                continue
            if any(sym in {"Control_L", "Control_R"} for sym in symbols):
                ctrl_codes.add(keycode)
            if any(sym.lower() == "m" for sym in symbols):
                m_codes.add(keycode)
        if not ctrl_codes:
            ctrl_codes = {37, 105}
        if not m_codes:
            m_codes = {58}
        return ctrl_codes, m_codes

    def _xinput_loop(self):
        if self._xinput_proc is None or self._xinput_proc.stdout is None:
            return

        current_event = None
        for raw in self._xinput_proc.stdout:
            line = raw.strip()
            if "EVENT type 2 (KeyPress)" in line:
                current_event = "press"
                continue
            if "EVENT type 3 (KeyRelease)" in line:
                current_event = "release"
                continue
            if current_event and line.startswith("detail:"):
                parts = line.split(":", 1)
                if len(parts) != 2:
                    current_event = None
                    continue
                detail = parts[1].strip().split()[0]
                try:
                    keycode = int(detail)
                except ValueError:
                    current_event = None
                    continue
                self._handle_xinput_key_event(current_event == "press", keycode)
                current_event = None

    def _handle_xinput_key_event(self, is_press: bool, keycode: int):
        combo_change = None
        with self._lock:
            if keycode in self._ctrl_keycodes:
                self._ctrl_held = is_press
            elif keycode in self._m_keycodes:
                self._m_held = is_press
            else:
                return
            combo_change = self._take_combo_change_locked()
        if combo_change is not None:
            self._emit(combo_change)

    @staticmethod
    def _is_ctrl(key) -> bool:
        if keyboard is None:
            return False
        return key in {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}

    @staticmethod
    def _is_m(key) -> bool:
        if keyboard is None:
            return False
        if not isinstance(key, keyboard.KeyCode):
            return False
        try:
            return (key.char or "").lower() == "m"
        except Exception:
            return False

    def _take_combo_change_locked(self) -> Optional[bool]:
        combo_now = self._ctrl_held and self._m_held
        if combo_now == self._combo_active:
            return None
        self._combo_active = combo_now
        return combo_now

    def _on_press(self, key):
        combo_change = None
        with self._lock:
            if self._is_ctrl(key):
                self._ctrl_held = True
            elif self._is_m(key):
                self._m_held = True
            else:
                return
            combo_change = self._take_combo_change_locked()
        if combo_change is not None:
            self._emit(combo_change)

    def _on_release(self, key):
        combo_change = None
        with self._lock:
            if self._is_ctrl(key):
                self._ctrl_held = False
            elif self._is_m(key):
                self._m_held = False
            else:
                return
            combo_change = self._take_combo_change_locked()
        if combo_change is not None:
            self._emit(combo_change)


class VoiceModeSignals(QObject):
    status_update = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    recording_state_changed = pyqtSignal(bool)
    speaking_state_changed = pyqtSignal(bool)
    transcript_ready = pyqtSignal(str)
    reply_ready = pyqtSignal(str)


class VoiceModeController(QObject):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        sudo_password: str = "././././",
        initial_working_dir: str = "/",
    ):
        super().__init__()
        self.signals = VoiceModeSignals()
        self.command_worker = StepAIWorker(
            api_key=api_key,
            base_url=base_url,
            model=model,
            sudo_password=sudo_password,
            initial_working_dir=initial_working_dir,
        )

        self._hotkey = GlobalPushToTalk.instance()
        self._lock = threading.Lock()
        self._enabled = False
        self._cancel_requested = False
        self._processing = False
        self._record_proc: Optional[subprocess.Popen] = None
        self._record_file: Optional[str] = None
        self._playback_proc: Optional[subprocess.Popen] = None

        self._tts_server = os.getenv("NVIDIA_TTS_SERVER", TTS_SERVER)
        self._tts_function_id = os.getenv("NVIDIA_TTS_FUNCTION_ID", TTS_FUNCTION_ID)
        self._tts_api_key = (
            os.getenv("NVIDIA_TTS_API_KEY")
            or os.getenv("NVIDIA_MAGPIE_API_KEY")
            or os.getenv("NVIDIA_API_KEY")
            or TTS_API_KEY_FALLBACK
        )
        self._tts_talk_script = self._resolve_talk_script()

    def set_enabled(self, enabled: bool) -> bool:
        if enabled:
            if not self._hotkey.available:
                reason = self._hotkey.error or "Global hotkey backend unavailable."
                self.signals.error_occurred.emit(
                    f"Voice mode unavailable: {reason}. Install `pynput` or use X11 with xinput/xmodmap."
                )
                return False

            with self._lock:
                self._enabled = True
                self._cancel_requested = False
            self._hotkey.subscribe(self._handle_hotkey)
            self.signals.status_update.emit("Voice mode active. Hold Ctrl+M to talk.")
            return True

        self._hotkey.unsubscribe(self._handle_hotkey)
        with self._lock:
            self._enabled = False
            self._cancel_requested = True
            record_proc = self._record_proc
            record_file = self._record_file
            self._record_proc = None
            self._record_file = None
            playback_proc = self._playback_proc
            self._playback_proc = None

        self._terminate_process(record_proc)
        self._terminate_process(playback_proc)

        if record_file and os.path.exists(record_file):
            try:
                os.remove(record_file)
            except OSError:
                pass

        self.signals.recording_state_changed.emit(False)
        self.signals.speaking_state_changed.emit(False)
        self.signals.status_update.emit("")
        return True

    def shutdown(self):
        self.set_enabled(False)

    def _should_continue(self) -> bool:
        with self._lock:
            return self._enabled and not self._cancel_requested

    def _handle_hotkey(self, combo_active: bool):
        if not self._should_continue():
            return
        if combo_active:
            self._start_recording()
        else:
            self._stop_recording_and_process()

    def _start_recording(self):
        with self._lock:
            if not self._enabled or self._cancel_requested:
                return
            if self._processing or self._record_proc is not None:
                return

            record_cmd, record_file = self._build_record_command()
            if not record_cmd:
                self.signals.error_occurred.emit(
                    "No recording backend found. Install `arecord` or `ffmpeg`."
                )
                return

            try:
                proc = subprocess.Popen(
                    record_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                self.signals.error_occurred.emit(f"Microphone start failed: {exc}")
                return

            self._record_proc = proc
            self._record_file = record_file

        self.signals.recording_state_changed.emit(True)
        self.signals.status_update.emit("Listening...")

    def _stop_recording_and_process(self):
        with self._lock:
            proc = self._record_proc
            record_file = self._record_file
            self._record_proc = None
            self._record_file = None

        if proc is None:
            return

        self._terminate_process(proc)
        self.signals.recording_state_changed.emit(False)

        if not record_file or not os.path.exists(record_file):
            self.signals.status_update.emit("")
            return

        if os.path.getsize(record_file) < 4096:
            try:
                os.remove(record_file)
            except OSError:
                pass
            self.signals.status_update.emit("")
            return

        with self._lock:
            if not self._enabled or self._cancel_requested or self._processing:
                try:
                    os.remove(record_file)
                except OSError:
                    pass
                self.signals.status_update.emit("")
                return
            self._processing = True

        threading.Thread(
            target=self._process_pipeline,
            args=(record_file,),
            daemon=True,
        ).start()

    def _process_pipeline(self, wav_file: str):
        try:
            transcript = self._transcribe(wav_file)
            if not transcript:
                self.signals.status_update.emit("")
                return
            self.signals.transcript_ready.emit(transcript)

            if not self._should_continue():
                return

            self.signals.status_update.emit("Sending transcript to step-3.5-flash...")
            response_text = self._generate_reply(transcript)
            if not response_text:
                return
            spoken_text = self._sanitize_spoken_text(response_text)
            if not spoken_text:
                spoken_text = response_text
            self.signals.reply_ready.emit(spoken_text)

            if not self._should_continue():
                return

            self._speak(spoken_text)
        except Exception as exc:
            self.signals.error_occurred.emit(str(exc))
        finally:
            with self._lock:
                self._processing = False
            try:
                os.remove(wav_file)
            except OSError:
                pass
            if self._should_continue():
                self.signals.status_update.emit("Voice mode active. Hold Ctrl+M to talk.")
            else:
                self.signals.status_update.emit("")

    def _transcribe(self, wav_file: str) -> str:
        self.signals.status_update.emit("Transcribing...")
        result = {"text": "", "error": None}
        done = threading.Event()

        def run_transcription():
            try:
                result["text"] = transcribe_wav_file(wav_file, timeout_sec=25.0)
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        threading.Thread(target=run_transcription, daemon=True).start()
        if not done.wait(timeout=40.0):
            raise RuntimeError("STT timeout: no response received in time.")

        if result["error"] is not None:
            raise RuntimeError(f"STT failed: {result['error']}") from result["error"]
        return str(result["text"] or "").strip()

    def _generate_reply(self, transcript: str) -> str:
        self.signals.status_update.emit("step-3.5-flash command engine running...")
        try:
            return self.command_worker.process_request_sync(
                transcript,
                status_callback=self._relay_engine_status,
                max_steps=100,
            )
        except Exception as exc:
            raise RuntimeError(f"Command engine failed: {exc}") from exc

    def _relay_engine_status(self, status: str):
        status = (status or "").strip()
        if not status:
            return
        if status == "Thinking...":
            self.signals.status_update.emit("step-3.5-flash reasoning...")
            return
        self.signals.status_update.emit(status)

    def _sanitize_spoken_text(self, text: str) -> str:
        cleaned = (text or "").replace("*", "")
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r" *\n *", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _speak(self, text: str):
        if not text:
            return

        self.signals.status_update.emit("Speaking...")
        output_file = tempfile.NamedTemporaryFile(
            prefix="step_desklet_tts_",
            suffix=".wav",
            delete=False,
        ).name
        try:
            self._generate_tts_file(text, output_file)
            if not self._should_continue():
                return

            play_cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", output_file]
            with self._lock:
                if not self._enabled or self._cancel_requested:
                    return
                self._playback_proc = subprocess.Popen(
                    play_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc = self._playback_proc

            self.signals.speaking_state_changed.emit(True)
            proc.wait()
        finally:
            with self._lock:
                self._playback_proc = None
            self.signals.speaking_state_changed.emit(False)
            try:
                os.remove(output_file)
            except OSError:
                pass

    def _generate_tts_file(self, text: str, output_file: str):
        if not os.path.isfile(self._tts_talk_script):
            raise RuntimeError(
                "NVIDIA talk.py not found. Set `NVIDIA_TTS_TALK_SCRIPT` to python-clients/scripts/tts/talk.py."
            )
        if not self._tts_api_key:
            raise RuntimeError("TTS API key missing. Set `NVIDIA_TTS_API_KEY`.")

        cmd = [
            "python3",
            self._tts_talk_script,
            "--server",
            self._tts_server,
            "--use-ssl",
            "--metadata",
            "function-id",
            self._tts_function_id,
            "--metadata",
            "authorization",
            f"Bearer {self._tts_api_key}",
            "--language-code",
            TTS_LANGUAGE,
            "--voice",
            TTS_ENERGETIC_VOICE,
            "--text",
            text,
            "--output",
            output_file,
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"TTS generation failed: {(result.stderr or result.stdout).strip()[:300]}"
            )

    def _build_record_command(self):
        out_file = tempfile.NamedTemporaryFile(
            prefix="step_desklet_rec_",
            suffix=".wav",
            delete=False,
        ).name

        if shutil.which("arecord"):
            return (
                [
                    "arecord",
                    "-q",
                    "-f",
                    "S16_LE",
                    "-r",
                    str(STT_SAMPLE_RATE),
                    "-c",
                    "1",
                    out_file,
                ],
                out_file,
            )

        if shutil.which("ffmpeg"):
            return (
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "alsa",
                    "-i",
                    "default",
                    "-ac",
                    "1",
                    "-ar",
                    str(STT_SAMPLE_RATE),
                    "-y",
                    out_file,
                ],
                out_file,
            )

        try:
            os.remove(out_file)
        except OSError:
            pass
        return None, None

    @staticmethod
    def _terminate_process(proc: Optional[subprocess.Popen], timeout: float = 2.0):
        if proc is None:
            return
        if proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=timeout)
            return
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=1.5)
            return
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass

    def _resolve_talk_script(self) -> str:
        env_path = os.getenv("NVIDIA_TTS_TALK_SCRIPT")
        root = Path(__file__).resolve().parents[2]
        candidates = [
            env_path,
            str(root / "python-clients/scripts/tts/talk.py"),
            "/home/ujan/python-clients/scripts/tts/talk.py",
            "python-clients/scripts/tts/talk.py",
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                return candidate
        return candidates[-1]
