"""Isolate one desklet request so Stop can interrupt blocked network calls and commands."""

import ctypes
import json
import os
from pathlib import Path
import sys
from ctypes import wintypes

from PyQt6.QtCore import QProcess, pyqtSignal


def _open_request_job(pid: int):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    # AssignProcessToJobObject requires PROCESS_SET_QUOTA | PROCESS_TERMINATE.
    process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
    try:
        if not process or not kernel.AssignProcessToJobObject(job, process):
            raise ctypes.WinError(ctypes.get_last_error())
    except OSError:
        kernel.CloseHandle(job)
        raise
    finally:
        if process:
            kernel.CloseHandle(process)
    return kernel, job


class DeskletRequest(QProcess):
    status = pyqtSignal(str)
    outcome = pyqtSignal(str, str)

    def __init__(self, parent):
        super().__init__(parent)
        self._payload = b""
        self._job = None
        self._cancelled = False
        self._result = ("error", "The request process ended without a result.")
        self.readyReadStandardOutput.connect(self._receive_output)
        self.errorOccurred.connect(self._on_error)
        self.finished.connect(self._on_finished)

    def start_request(self, payload: dict) -> None:
        self._payload = (json.dumps(payload) + "\n").encode("ascii")
        executable = Path(sys.executable)
        arguments = ["--request-worker"]
        if not getattr(sys, "frozen", False):
            windowless = executable.with_name("pythonw.exe")
            if executable.name.lower() == "python.exe" and windowless.is_file():
                executable = windowless
            arguments.insert(0, str(Path(__file__).resolve().parents[2] / "zylo_win11.py"))
        self.start(str(executable), arguments)

    def cancel(self) -> None:
        self._cancelled = True
        self._payload = b""
        self.closeWriteChannel()
        if self._job is not None:
            kernel, job = self._job
            kernel.TerminateJobObject(job, 1)
        self.kill()

    def _receive_output(self) -> None:
        while self.canReadLine():
            line = bytes(self.readLine())
            if self._cancelled:
                continue
            try:
                event, value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if event == "ready" and self._payload:
                # The venv launcher and the Python worker can have different PIDs.
                # Send the prompt only after the worker joins the cancellable job.
                try:
                    self._job = _open_request_job(int(value))
                except OSError as error:
                    self._result = ("error", f"Could not start a cancellable request: {error}")
                    self.closeWriteChannel()
                    self.kill()
                    return
                self.write(self._payload)
                self._payload = b""
                self.closeWriteChannel()
            elif event == "status":
                self.status.emit(value)
            elif event in ("response", "error", "cancelled", "admin_tool"):
                self._result = (event, value)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._result = ("error", self.errorString())
            self._on_finished()

    def _on_finished(self, *_args) -> None:
        self._receive_output()
        if self._job is not None:
            kernel, job = self._job
            kernel.CloseHandle(job)
            self._job = None
        if not self._cancelled:
            self.outcome.emit(*self._result)
        self.deleteLater()


def run_request_worker() -> int:
    """Read one request from the application's private pipe and send its events back."""
    from step_desklet.core.config import read_env_keys
    from step_desklet.core.worker import (
        DEFAULT_NIM_API_URL,
        DEFAULT_NIM_MODEL,
        GROQ_MODEL,
        PREVIOUS_NIM_DEFAULT,
        GroqChatClient,
        LMStudioChatClient,
        NIMChatClient,
        StepAIWorker,
    )

    def send(event: str, value) -> None:
        print(json.dumps((event, value)), flush=True)

    send("ready", os.getpid())
    line = sys.stdin.buffer.readline()
    if not line:
        return 0
    try:
        request = json.loads(line)
        options = request["options"]
        saved_keys = read_env_keys()
        if request["provider"] == "groq":
            client = GroqChatClient(
                os.getenv("GROQ_API_KEY") or "",
                backup_api_key=saved_keys["GROQ_BACKUP_API_KEY"],
            )
            worker = StepAIWorker(api_key="", model=GROQ_MODEL, client=client, **options)
        elif request["provider"] == "lmstudio":
            model = request["model"]
            client = LMStudioChatClient(
                base_url=request["base_url"], model=model, api_key=os.getenv("LMSTUDIO_API_KEY") or ""
            )
            worker = StepAIWorker(api_key="", model=model, client=client, **options)
        else:
            model = os.getenv("NIM_MODEL", "").strip()
            client = NIMChatClient(
                api_key=os.getenv("NVIDIA_API_KEY") or os.getenv("NIM_API_KEY") or "",
                api_url=os.getenv("NIM_API_URL", DEFAULT_NIM_API_URL),
                backup_api_key=saved_keys["NVIDIA_BACKUP_API_KEY"],
            )
            worker = StepAIWorker(
                api_key="",
                client=client,
                model=model if model and model != PREVIOUS_NIM_DEFAULT else DEFAULT_NIM_MODEL,
                **options,
            )
        worker.signals.status_update.connect(lambda text: send("status", text))
        worker.signals.response_ready.connect(lambda text: send("response", text))
        worker.signals.admin_tool_requested.connect(lambda tool_id: send("admin_tool", tool_id))
        worker.signals.error_occurred.connect(lambda text: send("error", text))
        worker.signals.cancelled.connect(lambda: send("cancelled", ""))
        worker.process_request(request["prompt"])
    except Exception as error:
        send("error", str(error))
    return 0
