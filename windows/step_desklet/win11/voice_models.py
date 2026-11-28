"""Verified, on-demand installation of the local Whisper model."""

import hashlib
import os
import tempfile
import threading
from pathlib import Path

import requests
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication


STT = "whisper-small-Q8_0.gguf"
ASSETS = (
    ("stt", STT, "https://huggingface.co/memoravox/whisper-small-gguf/resolve/main/" + STT,
     "9b9c8811bbcc82a7766f0fb0925614bdacb0923b2cc630daeac17108b655b860", 269_751_136),
)


def models_root():
    local = os.getenv("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / "AppData" / "Local") / "ZYLO" / "models"


def remove_retired_models(root):
    """Only remove the files the retired voice integrations installed."""
    retired = (
        "stt/moonshine-streaming-small-Q8_0.gguf",
        "tts/pocket-tts-english-novc-q8_0.gguf",
        "tts/alba.safetensors",
        "tts/crispasr-windows-x86_64-cpu.zip",
    )
    runtime = "tts/engine-v0.8.55/crispasr-windows-x86_64-cpu"
    retired += tuple(f"{runtime}/{name}" for name in (
        "crispasr.exe", "crispasr-quantize.exe", "openblas.dll", "LICENSE", "THIRD_PARTY_NOTICES.txt",
    ))
    for relative in retired:
        path = root / relative
        try:
            if path.resolve().is_relative_to(root.resolve()):
                path.unlink(missing_ok=True)
        except OSError:
            continue


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_models(root, progress, cancel):
    for folder, name, url, sha, size in ASSETS:
        target = root / folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if cancel.is_set():
            return False
        if not target.is_file() or file_hash(target) != sha:
            partial = None
            try:
                with requests.get(url, stream=True, timeout=(8, 12)) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("Content-Length", size))
                    count = 0
                    digest = hashlib.sha256()
                    with tempfile.NamedTemporaryFile(
                        dir=target.parent, prefix=target.name + ".", suffix=".part", delete=False,
                    ) as output:
                        partial = Path(output.name)
                        for chunk in response.iter_content(512 * 1024):
                            if cancel.is_set():
                                return False
                            count += len(chunk)
                            if count > size + 2_000_000:
                                raise ValueError(f"Unexpected download size for {name}.")
                            digest.update(chunk)
                            output.write(chunk)
                            progress(name, min(99, count * 100 // max(1, total)))
                if digest.hexdigest() != sha:
                    raise ValueError(f"Could not verify {name}. Try downloading it again.")
                if cancel.is_set():
                    return False
                partial.replace(target)
            finally:
                if partial is not None:
                    partial.unlink(missing_ok=True)
        progress(name, 100)
    return not cancel.is_set()


class VoiceDownloads(QObject):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, app):
        super().__init__(app)
        self.cancelled = threading.Event()
        self.running = False
        app.aboutToQuit.connect(self.cancelled.set)
        self.finished.connect(self._done)

    @classmethod
    def for_app(cls):
        app = QApplication.instance()
        if not hasattr(app, "_zylo_voice_downloads"):
            app._zylo_voice_downloads = cls(app)
        return app._zylo_voice_downloads

    def start(self):
        if self.running:
            return
        self.running = True
        self.cancelled.clear()
        threading.Thread(target=self._download, name="liNi voice download", daemon=True).start()

    def _download(self):
        try:
            ok = install_models(models_root(), self.progress.emit, self.cancelled)
            status = "Whisper is ready." if ok else "Download cancelled."
        except (OSError, ValueError, requests.RequestException) as error:
            ok = False
            status = str(error) if isinstance(error, ValueError) else "Could not install Whisper. Try again."
        self.finished.emit(ok, status)

    def _done(self, *_):
        self.running = False
