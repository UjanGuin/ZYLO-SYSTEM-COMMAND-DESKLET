"""Unit tests for 5-state command outcomes, cancellation, timeout cleanup, and pinned dependencies."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.test_support import PROJECT_DIR, ensure_runtime_stubs_if_missing

ensure_runtime_stubs_if_missing()

from step_desklet.core import worker as worker_mod  # noqa: E402


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def complete(self, **kwargs):
        self.requests.append(kwargs["messages"])
        if self._responses:
            return self._responses.pop(0)
        return json.dumps({"mode": "final", "message": "done"})


class _BlockingClient:
    def __init__(self):
        self._event = threading.Event()
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        self._event.set()

    def complete(self, **_kwargs):
        self._event.wait(timeout=5.0)
        if self.cancelled:
            raise worker_mod.OperationCancelledError("Operation cancelled.")
        return json.dumps({"mode": "final", "message": "should not reach"})


class WorkerOutcomeAndShutdownTests(unittest.TestCase):
    def _make_worker(self, client):
        return worker_mod.StepAIWorker(
            api_key="test",
            client=client,
            initial_working_dir=tempfile.gettempdir(),
        )

    def test_classify_command_outcomes_all_five_states(self):
        self.assertEqual(
            worker_mod.classify_command_outcome(0),
            worker_mod.CMD_STATUS_COMPLETED,
        )
        self.assertEqual(
            worker_mod.classify_command_outcome(2, stderr="file not found"),
            worker_mod.CMD_STATUS_FAILED,
        )
        self.assertEqual(
            worker_mod.classify_command_outcome(-1, timed_out=True),
            worker_mod.CMD_STATUS_TIMED_OUT,
        )
        self.assertEqual(
            worker_mod.classify_command_outcome(-1, cancelled=True),
            worker_mod.CMD_STATUS_CANCELLED,
        )
        self.assertEqual(
            worker_mod.classify_command_outcome(1, stderr="UnauthorizedAccessException: Access is denied."),
            worker_mod.CMD_STATUS_REQUIRES_PERMISSION,
        )
        self.assertEqual(
            worker_mod.classify_command_outcome(1, permission_denied=True),
            worker_mod.CMD_STATUS_REQUIRES_PERMISSION,
        )

    def test_build_feedback_includes_distinct_status_summary_headers(self):
        ai_worker = self._make_worker(_FakeClient([]))
        expected = [
            (worker_mod.CMD_STATUS_COMPLETED, "Command completed."),
            (worker_mod.CMD_STATUS_FAILED, "Command failed."),
            (worker_mod.CMD_STATUS_TIMED_OUT, "Command timed out."),
            (worker_mod.CMD_STATUS_CANCELLED, "Command was cancelled."),
            (worker_mod.CMD_STATUS_REQUIRES_PERMISSION, "Command requires permission."),
        ]
        for status, header in expected:
            feedback = ai_worker._build_feedback(
                "echo test",
                {"status": status, "exit_code": 0 if status == "completed" else 1, "stdout": "", "stderr": ""},
            )
            self.assertTrue(feedback.startswith(header + "\n"), msg=feedback)
            self.assertIn(f"status: {status}", feedback)

    def test_subprocess_timeout_returns_timed_out_status(self):
        ai_worker = self._make_worker(_FakeClient([]))
        result = ai_worker._run_popen_cancellable(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            command_label="sleep 5",
            timeout=0.2,
        )
        self.assertEqual(result["status"], worker_mod.CMD_STATUS_TIMED_OUT)
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("timed out", result["stderr"].lower())

    def test_subprocess_cancellation_terminates_quickly(self):
        ai_worker = self._make_worker(_FakeClient([]))

        def _cancel_soon():
            time.sleep(0.15)
            ai_worker.request_cancel()

        t = threading.Thread(target=_cancel_soon, daemon=True)
        t.start()
        start = time.monotonic()
        result = ai_worker._run_popen_cancellable(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            command_label="sleep 10",
            timeout=10.0,
        )
        elapsed = time.monotonic() - start
        t.join(timeout=2.0)

        self.assertEqual(result["status"], worker_mod.CMD_STATUS_CANCELLED)
        self.assertLess(elapsed, 3.0)

    def test_process_request_cancels_blocking_client_cleanly(self):
        client = _BlockingClient()
        ai_worker = self._make_worker(client)
        cancelled_flag = threading.Event()
        errors = []

        ai_worker.signals.cancelled.connect(cancelled_flag.set)
        ai_worker.signals.error_occurred.connect(errors.append)

        def _trigger_cancel():
            time.sleep(0.1)
            ai_worker.request_cancel()

        threading.Thread(target=_trigger_cancel, daemon=True).start()
        ai_worker.process_request("hello")

        self.assertTrue(client.cancelled)
        self.assertTrue(cancelled_flag.is_set())
        self.assertEqual(errors, [])

    def test_dependencies_are_strictly_pinned(self):
        for req_name in ("requirements.txt", "requirements-lock.txt"):
            path = PROJECT_DIR / req_name
            self.assertTrue(path.exists(), msg=f"Missing {req_name}")
            lines = [
                ln.strip()
                for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith("-r ")
            ]
            self.assertGreaterEqual(len(lines), 4)
            for ln in lines:
                self.assertIn("==", ln, msg=f"Unpinned dependency in {req_name}: {ln}")

    def test_powershell_wrapper_enforces_stop_errors_and_propagates_exit_codes(self):
        script = worker_mod.StepAIWorker._build_powershell_wrapper_script("Get-Item 'C:\\missing'")
        self.assertIn("$ErrorActionPreference = 'Stop'", script)
        self.assertIn("$global:LASTEXITCODE = 0", script)
        self.assertIn("catch {", script)
        self.assertIn("[Console]::Error.WriteLine", script)
        self.assertIn("finally {", script)
        self.assertIn("exit $__zyloExitCode", script)
        self.assertEqual(
            worker_mod.classify_command_outcome(0, stderr="Access is denied."),
            worker_mod.CMD_STATUS_REQUIRES_PERMISSION,
        )

    def test_invalid_layout_and_settings_config_never_crash_and_clamp_bounds(self):
        from step_desklet.core import config as cfg_mod

        invalid_layout = cfg_mod.validate_layout_config(
            {
                "width": "invalid",
                "height": "invalid",
                "x": None,
                "y": True,
                "locked": "yes",
            }
        )
        self.assertEqual(invalid_layout["width"], cfg_mod.DEFAULT_DESKLET_WIDTH)
        self.assertIsNone(invalid_layout["height"])
        self.assertEqual(invalid_layout["x"], cfg_mod.DEFAULT_DESKLET_X)
        self.assertEqual(invalid_layout["y"], cfg_mod.DEFAULT_DESKLET_Y)
        self.assertTrue(invalid_layout["locked"])

        clamped_layout = cfg_mod.validate_layout_config(
            {"width": 99999, "height": 12, "x": -999999, "y": "420.6"}
        )
        self.assertEqual(clamped_layout["width"], cfg_mod.MAX_DESKLET_WIDTH)
        self.assertEqual(clamped_layout["height"], cfg_mod.MIN_DESKLET_HEIGHT)
        self.assertEqual(clamped_layout["x"], cfg_mod.MIN_DESKLET_COORD)
        self.assertEqual(clamped_layout["y"], 421)

        validated_settings = cfg_mod.validate_settings_config(
            {"memoryTurns": "invalid", "accent": "not-a-hex", "defaultProvider": "unknown"}
        )
        self.assertEqual(validated_settings["memoryTurns"], cfg_mod.DEFAULT_MEMORY_TURNS)
        self.assertEqual(validated_settings["accent"], cfg_mod.ACCENTS["Dark Violet"])
        self.assertEqual(validated_settings["defaultProvider"], "")


if __name__ == "__main__":
    unittest.main()
