"""Verified, on-demand installation of the two local voice models."""

import hashlib
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path

import requests
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication


STT = "whisper-small-Q8_0.gguf"
LEGACY_STT = (
    "stt", "moonshine-streaming-small-Q8_0.gguf",
    "https://huggingface.co/handy-computer/moonshine-streaming-small-gguf/resolve/"
    "41444173ed8210852a883e046fadcfba3e7bfbae/moonshine-streaming-small-Q8_0.gguf",
    "d03670f69629b649085d0f44a63d97668b4119117cc9611a4e4ad94341713dfc", 199_000_000,
)
TTS = "pocket-tts-english-novc-q8_0.gguf"
ENGINE_VERSION = "v0.8.55"
ENGINE = "crispasr-windows-x86_64-cpu.zip"
VOICE_PRESET = (
    "tts", "alba.safetensors",
    "https://huggingface.co/kyutai/pocket-tts-without-voice-cloning/resolve/"
    "075c0abfe7e41450521b0200b5168cfbc16bc77b/embeddings_v3/alba.safetensors",
    "76fcc99b552439f1d2a96d593285113f6afaf628281195a2959b40f5c3610f3f", 6_150_000,
)
# Release metadata and model pointers pin the exact artifacts, independently of CDN redirects.
ASSETS = (
    ("stt", STT, "https://huggingface.co/memoravox/whisper-small-gguf/resolve/main/" + STT,
     "9b9c8811bbcc82a7766f0fb0925614bdacb0923b2cc630daeac17108b655b860", 269_751_136),
    ("tts", TTS, "https://huggingface.co/cstr/pocket-tts-GGUF/resolve/"
     "d10681033bba2211a03e65a741a7951462b04252/" + TTS,
     "5e76fe89ec300a1e8dcd94eafbe0e44e5c94c029b153d7f59948a5c43cea208f", 110_000_000),
    ("tts", ENGINE, f"https://github.com/CrispStrobe/CrispASR/releases/download/{ENGINE_VERSION}/{ENGINE}",
     "f3b9643b7874e66cd366cda81d37d101677f18ab6e3a3255ff65dae19a439740", 9_702_256),
    VOICE_PRESET,
)


def models_root():
    local = os.getenv("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / "AppData" / "Local") / "ZYLO" / "models"


def engine_location(root):
    directory = root / "tts" / ("engine-" + ENGINE_VERSION)
    for name in ("crispasr.exe", "crispasr-cli.exe"):
        found = next(directory.rglob(name), None)
        if found:
            return found
    return None


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_models(root, progress, cancel, *, assets=None):
    for folder, name, url, sha, size in ASSETS if assets is None else assets:
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
        if name == ENGINE and engine_location(root) is None:
            directory = target.parent / ("engine-" + ENGINE_VERSION)
            with zipfile.ZipFile(target) as archive:
                for info in archive.infolist():
                    relative = Path(info.filename)
                    if info.is_dir():
                        continue
                    if relative.is_absolute() or ".." in relative.parts or ":" in info.filename:
                        raise ValueError("Invalid voice runtime archive path.")
                    output_path = directory / relative
                    if not output_path.resolve().is_relative_to(directory.resolve()):
                        raise ValueError("Invalid voice runtime archive path.")
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, output_path.open("wb") as output:
                        shutil.copyfileobj(source, output)
            if engine_location(root) is None:
                raise ValueError("The voice runtime executable was missing from its archive.")
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

    def start(self, *, include_pocket=False):
        if self.running:
            return
        self.running = True
        self.cancelled.clear()
        assets = ASSETS if include_pocket else ASSETS[:1]
        threading.Thread(target=self._download, args=(assets,), name="ZYLO models", daemon=True).start()

    def _download(self, assets):
        try:
            ok = install_models(models_root(), self.progress.emit, self.cancelled, assets=assets)
            status = "Voice models are ready." if ok else "Download cancelled."
        except (OSError, ValueError, zipfile.BadZipFile, requests.RequestException) as error:
            ok = False
            status = str(error) if isinstance(error, ValueError) else "Could not install the voice models. Try again."
        self.finished.emit(ok, status)

    def _done(self, *_):
        self.running = False
