"""Isolate one desklet request so Stop can interrupt blocked network calls and commands."""

import ctypes
import json
import os
from pathlib import Path
import sys
from ctypes import wintypes

from PyQt6.QtCore import QProcess, QProcessEnvironment, QTimer, pyqtSignal

from step_desklet.win11.shell_open import LaunchOutcome, open_windows_item


def _start_helper(process: QProcess, mode: str) -> None:
    executable = Path(sys.executable)
    arguments = [mode]
    if not getattr(sys, "frozen", False):
        windowless = executable.with_name("pythonw.exe")
        if executable.name.lower() == "python.exe" and windowless.is_file():
            executable = windowless
        arguments.insert(0, str(Path(__file__).resolve().parents[2] / "zylo_win11.py"))
    process.start(str(executable), arguments)


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
        self._launcher = None
        self._cancelled = False
        self._result = ("error", "The request process ended without a result.")
        self.file_matches: tuple[str, ...] = ()
        self.readyReadStandardOutput.connect(self._receive_output)
        self.errorOccurred.connect(self._on_error)
        self.finished.connect(self._on_finished)

    def start_request(self, payload: dict) -> None:
        self._payload = (json.dumps(payload) + "\n").encode("ascii")
        _start_helper(self, "--request-worker")

    def cancel(self) -> None:
        self._cancelled = True
        self._payload = b""
        self.closeWriteChannel()
        if self._launcher is not None:
            self._launcher.kill()
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
            elif event == "launch" and isinstance(value, dict):
                self._start_launch(value)
            elif event == "status":
                self.status.emit(value)
            elif event == "file_matches":
                self.file_matches = tuple(value)
            elif event in ("response", "error", "cancelled", "admin_tool"):
                self._result = (event, value)

    def _start_launch(self, request: dict) -> None:
        if self._launcher is not None:
            return
        # A sibling process launches the desktop item outside the cancellable job.
        # Stop can still end the request without terminating the app the user opened.
        launcher = QProcess(self)
        self._launcher = launcher
        environment = QProcessEnvironment.systemEnvironment()
        for name in (
            "GROQ_API_KEY", "GROQ_BACKUP_API_KEY", "NVIDIA_API_KEY", "NVIDIA_BACKUP_API_KEY",
            "NIM_API_KEY", "LMSTUDIO_API_KEY", "TAVILY_API_KEY", "FREELLMAPI_API_KEY",
        ):
            environment.remove(name)
        launcher.setProcessEnvironment(environment)
        timer = QTimer(launcher)
        timer.setSingleShot(True)
        unknown = [LaunchOutcome.UNCONFIRMED.value, ""]

        def finish(result: list) -> None:
            if self._launcher is not launcher:
                return
            self._launcher = None
            timer.stop()
            if not self._cancelled and self.state() == QProcess.ProcessState.Running:
                self.write((json.dumps(result) + "\n").encode("ascii"))
            launcher.deleteLater()

        def send_request() -> None:
            launcher.write((json.dumps(request) + "\n").encode("ascii"))
            launcher.closeWriteChannel()

        def receive_result(_code, _status) -> None:
            try:
                result = json.loads(bytes(launcher.readAllStandardOutput()))
                if not isinstance(result, list) or len(result) != 2:
                    raise ValueError("Invalid launcher reply")
            except ValueError:
                result = unknown
            finish(result)

        launcher.started.connect(send_request)
        launcher.finished.connect(receive_result)
        def launch_error(error) -> None:
            if error == QProcess.ProcessError.FailedToStart:
                finish([None, "The Windows launcher could not start."])

        launcher.errorOccurred.connect(launch_error)
        timer.timeout.connect(launcher.kill)
        timer.start(9000)
        _start_helper(launcher, "--launch-item")

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._result = ("error", self.errorString())
            self._on_finished()

    def _on_finished(self, *_args) -> None:
        self._receive_output()
        if self._launcher is not None:
            self._launcher.kill()
        if self._job is not None:
            kernel, job = self._job
            kernel.CloseHandle(job)
            self._job = None
        if not self._cancelled:
            self.outcome.emit(*self._result)
        self.deleteLater()


def run_launch_worker() -> int:
    try:
        request = json.loads(sys.stdin.buffer.readline())
        outcome = open_windows_item(**request)
        result = [outcome.value, ""]
    except (OSError, TypeError, ValueError) as error:
        result = [None, str(error)]
    print(json.dumps(result), flush=True)
    return 0


def check_provider_key(provider: str, key: str, base_url: str = "", model: str = "") -> None:
    import requests
    from step_desklet.core.config import FREELLMAPI_BASE_URL, normalize_freellmapi_route
    from step_desklet.core.worker import (
        DEFAULT_NIM_API_URL, DEFAULT_NIM_MODEL, GROQ_API_URL, GROQ_MODEL, PREVIOUS_NIM_DEFAULT,
        normalize_lmstudio_base_url,
    )

    options = {
        "headers": {"Authorization": f"Bearer {key}"},
        "timeout": (4, 12),
        "allow_redirects": False,
    }
    try:
        if provider == "tavily":
            request = requests.get("https://api.tavily.com/usage", **options)
        elif provider == "lmstudio":
            request = requests.get(normalize_lmstudio_base_url(base_url) + "/models", **options)
        elif provider == "freellmapi":
            with requests.Session() as session:
                session.trust_env = False
                options["timeout"] = (4, 30)
                request = session.post(
                    f"{FREELLMAPI_BASE_URL}/chat/completions",
                    json={
                        "model": normalize_freellmapi_route(model),
                        "messages": [{"role": "user", "content": "Reply with OK."}],
                    },
                    **options,
                )
        elif provider in {"nim", "groq"}:
            nim_model = os.getenv("NIM_MODEL", "").strip()
            model = nim_model if nim_model and nim_model != PREVIOUS_NIM_DEFAULT else DEFAULT_NIM_MODEL
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
            }
            if provider == "groq":
                url = GROQ_API_URL
                body.update(model=GROQ_MODEL, max_completion_tokens=64, reasoning_effort="low")
            else:
                url = os.getenv("NIM_API_URL") or DEFAULT_NIM_API_URL
                body.update(max_tokens=32)
                if model == DEFAULT_NIM_MODEL:
                    body["chat_template_kwargs"] = {"enable_thinking": False}
            request = requests.post(url, json=body, **options)
        else:
            raise RuntimeError("Unknown provider.")

        with request as response:
            if response.status_code in (401, 403):
                raise RuntimeError("Key rejected")
            if response.status_code == 429:
                raise RuntimeError("Rate limited")
            if response.status_code != 200:
                raise RuntimeError(f"Check failed (HTTP {response.status_code})")
            data = response.json()
            if not isinstance(data, dict) or "error" in data:
                raise RuntimeError("Unexpected provider response")
            if provider in {"nim", "groq", "freellmapi"} and not data.get("choices"):
                raise RuntimeError("No model response")
            if provider == "lmstudio" and not isinstance(data.get("data"), list):
                raise RuntimeError("Unexpected provider response")
    except (requests.RequestException, ValueError):
        # Provider errors can contain the request URL or credentials.
        raise RuntimeError("Could not reach provider") from None


def run_request_worker() -> int:
    """Read one request from the application's private pipe and send its events back."""
    from step_desklet.core.config import enabled_providers, load_settings_config, normalize_freellmapi_route, read_env_keys
    from step_desklet.core.worker import (
        DEFAULT_NIM_API_URL,
        DEFAULT_NIM_MODEL,
        GROQ_MODEL,
        PREVIOUS_NIM_DEFAULT,
        FreeLLMAPIChatClient,
        GroqChatClient,
        LMStudioChatClient,
        NIMChatClient,
        StepAIWorker,
    )

    def send(event: str, value) -> None:
        print(json.dumps((event, value)), flush=True)

    def launch_item(target: str, **options) -> LaunchOutcome:
        send("launch", {"target": target, **options})
        line = sys.stdin.buffer.readline()
        if not line:
            raise OSError("The Windows launcher disconnected.")
        outcome, error = json.loads(line)
        if not outcome:
            raise OSError(error)
        return LaunchOutcome(outcome)

    send("ready", os.getpid())
    line = sys.stdin.buffer.readline()
    if not line:
        return 0
    try:
        request = json.loads(line)
        if request.get("operation") == "check_provider_key":
            check_provider_key(request["provider"], request["key"], request.get("base_url", ""), request.get("model", ""))
            send("response", "Verified")
            return 0
        if request.get("operation") == "rebuild_app_catalog":
            key = request["groq_key"]
            if key and request.get("check_key"):
                send("status", "Checking the Groq key.")
                try:
                    check_provider_key("groq", key)
                except RuntimeError:
                    send("status", "Groq is unavailable. Building the app list locally.")
                    key = ""
            if key:
                client = GroqChatClient(key, backup_api_key=request["groq_backup_key"])
            else:
                client = NIMChatClient("")
            worker = StepAIWorker(api_key="", client=client, initial_working_dir=str(Path.home()))
            worker.rebuild_app_catalog(use_groq=bool(key), status_callback=lambda text: send("status", text))
            send("response", "App list rebuilt.")
            return 0
        options = request["options"]
        saved_keys = read_env_keys()
        settings = load_settings_config()
        if request["provider"] not in enabled_providers(settings):
            send("error", "This provider is disabled. Select an enabled provider or turn it on in Setup.")
            return 0
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
        elif request["provider"] == "freellmapi":
            model = normalize_freellmapi_route(settings["freeLlmApiModel"])
            client = FreeLLMAPIChatClient(saved_keys["FREELLMAPI_API_KEY"], model=model)
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
        worker.launch_item = launch_item
        worker.pending_file_matches = tuple(request.get("file_matches", ()))

        def respond(text: str) -> None:
            send("file_matches", worker.pending_file_matches)
            send("response", text)

        worker.signals.status_update.connect(lambda text: send("status", text))
        worker.signals.response_ready.connect(respond)
        worker.signals.admin_tool_requested.connect(lambda tool_id: send("admin_tool", tool_id))
        worker.signals.error_occurred.connect(lambda text: send("error", text))
        worker.signals.cancelled.connect(lambda: send("cancelled", ""))
        worker.process_request(request["prompt"])
    except Exception as error:
        send("error", str(error))
    return 0
