"""Unit tests for 5-state command outcomes, cancellation, timeout cleanup, and pinned dependencies."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_support import PROJECT_DIR, ensure_runtime_stubs_if_missing

ensure_runtime_stubs_if_missing()

from step_desklet.core import worker as worker_mod  # noqa: E402
from step_desklet.core import assistant_context as assistant_context_mod  # noqa: E402


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


class ProviderClientTests(unittest.TestCase):
    @staticmethod
    def _response(status: int, data: dict):
        response = Mock(status_code=status, ok=status < 400, headers={})
        response.json.return_value = data
        return response

    def test_nvidia_retries_an_upstream_502_on_the_current_key(self):
        client = worker_mod.NIMChatClient("primary", backup_api_key="backup")
        failed = self._response(502, {"error": {"message": "Upstream request failed."}})
        answer = self._response(200, {"choices": [{"message": {"content": "Recovered."}}]})
        answer.headers["Content-Type"] = "application/json"
        with (
            patch.object(client._session, "post", side_effect=[failed, answer]) as post,
            patch.object(client._cancel_event, "wait", return_value=False) as wait,
        ):
            self.assertEqual(client.complete(model=worker_mod.DEFAULT_NIM_MODEL, messages=[]), "Recovered.")
        self.assertEqual(
            [call.kwargs["headers"]["Authorization"] for call in post.call_args_list],
            ["Bearer primary", "Bearer primary"],
        )
        self.assertEqual(post.call_args_list[0].kwargs["json"], post.call_args_list[1].kwargs["json"])
        wait.assert_called_once_with(1.0)
        failed.close.assert_called_once()
        answer.close.assert_called_once()

    def test_nvidia_502_retry_is_bounded_and_can_be_cancelled(self):
        for cancel, expected_calls, error in (
            (False, 2, worker_mod.NIMBadGatewayError),
            (True, 1, worker_mod.OperationCancelledError),
        ):
            with self.subTest(cancel=cancel):
                client = worker_mod.NIMChatClient("primary", backup_api_key="backup")
                failed = self._response(502, {"error": {"message": "Upstream request failed."}})
                with (
                    patch.object(client._session, "post", return_value=failed) as post,
                    patch.object(client._cancel_event, "wait", return_value=cancel),
                ):
                    with self.assertRaises(error):
                        client.complete(model=worker_mod.DEFAULT_NIM_MODEL, messages=[])
                self.assertEqual(post.call_count, expected_calls)

    def test_nvidia_switches_on_rate_limits_and_timeouts(self):
        for failure in (
            self._response(429, {"error": {"message": "Rate limit"}}),
            self._response(504, {"error": {"message": "Gateway timeout"}}),
            worker_mod.requests.Timeout(),
        ):
            with self.subTest(failure=failure):
                client = worker_mod.NIMChatClient("primary", backup_api_key="backup")
                answer = self._response(200, {"choices": [{"message": {"content": "Recovered."}}]})
                answer.headers["Content-Type"] = "application/json"
                with patch.object(client._session, "post", side_effect=[failure, answer, answer]) as post:
                    self.assertEqual(client.complete(model=worker_mod.DEFAULT_NIM_MODEL, messages=[]), "Recovered.")
                    client.complete(model=worker_mod.DEFAULT_NIM_MODEL, messages=[])
                self.assertEqual(
                    [call.kwargs["headers"]["Authorization"] for call in post.call_args_list],
                    ["Bearer primary", "Bearer backup", "Bearer backup"],
                )

    def test_nvidia_does_not_switch_for_authentication_or_reuse_the_primary_after_failover(self):
        for status, attempts in ((401, 1), (429, 2)):
            with self.subTest(status=status):
                client = worker_mod.NIMChatClient("primary", backup_api_key="backup")
                response = self._response(status, {"error": {"message": "Rejected."}})
                with patch.object(client._session, "post", return_value=response) as post:
                    with self.assertRaisesRegex(RuntimeError, str(status)):
                        client.complete(model=worker_mod.DEFAULT_NIM_MODEL, messages=[])
                    self.assertEqual(post.call_count, attempts)

    def test_groq_uses_the_reported_wait_to_choose_the_key(self):
        for retry_after, message, expected in (
            ("3.9", "Rate limit", "primary"),
            ("4", "Rate limit", "backup"),
            ("", "Please try again in 11.535s.", "backup"),
            ("", "Please try again in 1m7.4s.", "backup"),
        ):
            with self.subTest(retry_after=retry_after, message=message):
                client = worker_mod.GroqChatClient("primary", backup_api_key="backup")
                limited = self._response(429, {"error": {"message": message}})
                limited.headers["Retry-After"] = retry_after
                answer = self._response(200, {"choices": [{"message": {"content": "Recovered."}}]})
                with (
                    patch.object(client._session, "post", side_effect=[limited, answer]) as post,
                    patch.object(client._cancel_event, "wait", return_value=False) as wait,
                ):
                    self.assertEqual(client.complete(model=worker_mod.GROQ_MODEL, messages=[]), "Recovered.")
                self.assertEqual(
                    [call.kwargs["headers"]["Authorization"] for call in post.call_args_list],
                    ["Bearer primary", f"Bearer {expected}"],
                )
                self.assertEqual(wait.call_count, int(expected == "primary"))

    def test_groq_retries_short_tpm_limit_with_less_output_on_same_key(self):
        client = worker_mod.GroqChatClient("primary", backup_api_key="backup")
        limited = self._response(429, {"error": {
            "message": ("Rate limit reached on tokens per minute (TPM): Limit 8000, Used 3209, "
                        "Requested 6329. Please try again in 3.535s.")
        }})
        answer = self._response(200, {"choices": [{"message": {"content": "Recovered."}}]})
        sent = []

        def post(_url, **kwargs):
            sent.append((kwargs["headers"]["Authorization"], deepcopy(kwargs["json"])))
            return limited if len(sent) == 1 else answer

        with (
            patch.object(client._session, "post", side_effect=post),
            patch.object(client._cancel_event, "wait", return_value=False) as wait,
        ):
            self.assertEqual(client.complete(model=worker_mod.GROQ_MODEL, messages=[]), "Recovered.")
        wait.assert_called_once_with(4.535)
        self.assertEqual([key for key, _ in sent], ["Bearer primary", "Bearer primary"])
        self.assertLess(sent[1][1]["max_completion_tokens"], sent[0][1]["max_completion_tokens"])

    def test_groq_uses_the_elapsed_time_for_a_network_timeout(self):
        for elapsed, expected in ((3, "primary"), (4, "backup")):
            with self.subTest(elapsed=elapsed):
                client = worker_mod.GroqChatClient("primary", backup_api_key="backup")
                answer = self._response(200, {"choices": [{"message": {"content": "Recovered."}}]})
                with (
                    patch.object(client._session, "post", side_effect=[worker_mod.requests.Timeout(), answer]) as post,
                    patch.object(worker_mod.time, "monotonic", side_effect=[0, elapsed, elapsed]),
                ):
                    client.complete(model=worker_mod.GROQ_MODEL, messages=[])
                self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], f"Bearer {expected}")

    def test_groq_retries_rejected_json_and_returns_a_valid_answer(self):
        for error in (
            {"message": "Failed to generate JSON. Please adjust your prompt. See 'failed_generation' for more details."},
            {"code": "json_validate_failed", "message": "The generated document was rejected."},
            {"message": "Failed to validate JSON."},
        ):
            with self.subTest(error=error):
                client = worker_mod.GroqChatClient("test")
                rejected = self._response(400, {"error": {**error, "failed_generation": "discard this text"}})
                accepted = self._response(200, {
                    "choices": [{"message": {"content": '{"mode":"final","message":"Recovered."}'}}],
                })
                replies = iter((rejected, accepted))
                requests = []

                def post(_url, *, json, **_options):
                    requests.append(deepcopy(json))
                    return next(replies)

                worker = worker_mod.StepAIWorker(api_key="", client=client, model=worker_mod.GROQ_MODEL)
                with patch.object(client._session, "post", side_effect=post):
                    self.assertEqual(worker.process_request_sync("Say hello."), "Recovered.")
                self.assertEqual(len(requests), 2)
                original, retry = requests
                self.assertEqual(original["response_format"], {"type": "json_object"})
                self.assertNotIn("response_format", retry)
                self.assertEqual(retry["tool_choice"], "none")
                self.assertGreater(retry["max_completion_tokens"], original["max_completion_tokens"])
                self.assertEqual(retry["messages"][:-1], original["messages"])
                self.assertNotIn("discard this text", str(retry))
                rejected.close.assert_called_once()
                accepted.close.assert_called_once()

    def test_groq_does_not_retry_unrelated_errors_or_repeat_json_recovery(self):
        for error, expected_attempts in (
            ({"message": "Unsupported request parameter", "code": "invalid_request_error"}, 1),
            ({"message": "Failed to generate JSON", "code": "json_validate_failed"}, 2),
        ):
            with self.subTest(error=error):
                client = worker_mod.GroqChatClient("test")
                response = self._response(400, {"error": error})
                with patch.object(client._session, "post", return_value=response) as post:
                    with self.assertRaisesRegex(RuntimeError, "HTTP 400"):
                        client.complete(model=worker_mod.GROQ_MODEL, messages=[])
                    self.assertEqual(post.call_count, expected_attempts)

    def test_nemotron_uses_streaming_and_only_collects_answer_content(self):
        client = worker_mod.NIMChatClient("test")
        response = self._response(200, {})
        response.headers["Content-Type"] = "text/event-stream"
        frames = [
            {"choices": [{"delta": {"reasoning_content": "Hidden reasoning", "content": None}}]},
            {"choices": []},
            {"choices": [{"delta": {"content": '{"mode":"final",'}}]},
            {"choices": [{"delta": {"content": [{"type": "text", "text": '"message":"Done."}'}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        response.iter_lines.return_value = [
            *(f"data: {json.dumps(frame)}".encode() for frame in frames),
            b"data: [DONE]",
        ]
        with patch.object(client._session, "post", return_value=response) as post:
            worker = worker_mod.StepAIWorker(api_key="", client=client)
            self.assertEqual(worker.process_request_sync("Say hello."), "Done.")
        self.assertEqual(post.call_args.args[0], "https://integrate.api.nvidia.com/v1/chat/completions")
        self.assertTrue(post.call_args.kwargs["stream"])
        request = post.call_args.kwargs["json"]
        self.assertEqual(request["model"], "nvidia/nemotron-3.5-super-vl-120b-a12b")
        self.assertEqual(request["temperature"], 1)
        self.assertEqual(request["top_p"], 0.95)
        self.assertEqual(request["max_tokens"], 16384)
        self.assertTrue(request["parallel_tool_calls"])
        self.assertEqual(request["seed"], 42)
        self.assertEqual(request["chat_template_kwargs"], {"enable_thinking": True})
        response.close.assert_called_once()

    def test_nemotron_rejects_an_incomplete_stream(self):
        client = worker_mod.NIMChatClient("test")
        response = self._response(200, {})
        response.headers["Content-Type"] = "text/event-stream"
        response.iter_lines.return_value = [b'data: {"choices":[{"delta":{"content":"Partial answer"}}]}']
        with patch.object(client._session, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "stream ended before the answer was complete"):
                client.complete(model=worker_mod.DEFAULT_NIM_MODEL, messages=[])
        response.close.assert_called_once()

    def test_chats_streams_final_message_tokens_on_each_provider(self):
        clients = [
            (worker_mod.NIMChatClient("test"), "custom-nim"),
            (worker_mod.GroqChatClient("test"), worker_mod.GROQ_MODEL),
            (worker_mod.LMStudioChatClient(), "local-model"),
            (worker_mod.FreeLLMAPIChatClient("test"), "auto"),
        ]
        for client, model in clients:
            with self.subTest(model=model):
                response = self._response(200, {})
                response.headers["Content-Type"] = "text/event-stream"
                chunks = [
                    '{"mode":"final","message":"Hello',
                    r' \ud83d',
                    r'\ude00',
                    r'\nthere!"}',
                ]
                events = []
                response.iter_lines.return_value = [
                    *(f"data: {json.dumps({'choices': [{'delta': {'content': chunk, 'reasoning_content': 'private'}}]})}"
                      for chunk in chunks),
                    "data: [DONE]",
                ]
                client.preview_callback = lambda event, text: events.append((event, text))
                with patch.object(client._session, "post", return_value=response) as post:
                    result = client.complete(model=model, messages=[])
                self.assertTrue(post.call_args.kwargs["json"]["stream"])
                self.assertEqual(result, "".join(chunks))
                self.assertEqual(events, [
                    ("stream_reset", ""), ("token", "Hello"), ("token", " \U0001F600"), ("token", "\nthere!"),
                ])
                response.close.assert_called_once()

    def test_stream_preview_does_not_display_internal_action_fields(self):
        response = self._response(200, {})
        command = '{"mode":"run","reason":"Read something","command":"fixture"}'
        response.iter_lines.return_value = [
            "data: " + json.dumps({"choices": [{"delta": {"content": command}}]}),
            "data: [DONE]",
        ]
        preview = Mock()
        result = worker_mod._read_chat_stream(response, threading.Event(), "Fixture", preview)
        self.assertEqual(result, command)
        preview.assert_called_once_with("stream_reset", "")

class WorkerOutcomeAndShutdownTests(unittest.TestCase):
    def test_exact_windows_volume_runs_trusted_skill_without_a_provider(self):
        from step_desklet.core.skills import ZyloSkill

        skill = ZyloSkill("media-controls", "Volume", "", Path("media_controls.ps1"), "bundled")
        client = Mock()
        worker = worker_mod.StepAIWorker(api_key="", client=client)
        result = {"status": worker_mod.CMD_STATUS_COMPLETED, "stdout": "Volume set to 70%."}
        with (
            patch.object(worker_mod, "IS_WINDOWS", True),
            patch.object(worker_mod, "discover_skills", return_value={"media-controls": skill}),
            patch.object(worker, "_execute_skill", return_value=("command", result)) as execute,
        ):
            self.assertIn("0 to 100", worker.process_request_sync("Set volume to 101%"))
            self.assertEqual(worker.process_request_sync("Set volume to 70%"), "Volume set to 70%.")
            execute.assert_called_once_with(skill, {"Action": "setvolume", "Level": 70}, "Set Windows volume", None)
            client.complete.assert_not_called()

    def test_four_completed_turns_roll_forward_and_remain_available_during_tools(self):
        replies = [json.dumps({"mode": "final", "message": f"Reply {number}"}) for number in range(1, 6)]
        prior_turns = [
            message
            for number in range(1, 5)
            for message in (
                {"role": "user", "content": f"Message {number}"},
                {"role": "assistant", "content": f"Reply {number}"},
            )
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(worker_mod, "discover_skills", return_value={}):
            conversation = Path(directory) / "conversation.json"
            options = {"api_key": "", "conversation_path": str(conversation), "max_history_messages": 8}
            worker = worker_mod.StepAIWorker(client=_FakeClient(replies[:4]), **options)
            for number in range(1, 5):
                worker.process_request_sync(f"Message {number}")

            action = json.dumps({"mode": "run", "command": "Get-Date", "reason": "Read the date"})
            client = _FakeClient([action] * 5 + replies[4:])
            worker = worker_mod.StepAIWorker(client=client, **options)
            result = {"status": worker_mod.CMD_STATUS_COMPLETED, "exit_code": 0, "stdout": "Date", "stderr": ""}
            with patch.object(worker, "_run_command", return_value=result):
                self.assertEqual(worker.process_request_sync("Message 5"), "Reply 5")

            current = {"role": "user", "content": "Message 5"}
            for request in client.requests:
                self.assertEqual(request[1:10], [*prior_turns, current])
                self.assertLessEqual(len(request), 10 + worker_mod.MAX_TURN_FEEDBACK_MESSAGES)
            saved = json.loads(conversation.read_text(encoding="utf-8"))
            self.assertEqual(saved, [*prior_turns[2:], current, {"role": "assistant", "content": "Reply 5"}])

    def _make_worker(self, client):
        return worker_mod.StepAIWorker(
            api_key="test",
            client=client,
            initial_working_dir=tempfile.gettempdir(),
        )

    @unittest.skipUnless(sys.platform == "win32", "Windows Features approval")
    def test_windows_features_request_goes_to_the_approval_flow(self):
        answer = json.dumps({"mode": "admin_tool", "tool": "windows-features"})
        client = _FakeClient([answer])
        worker = worker_mod.StepAIWorker(api_key="", client=client, allow_admin_tool_requests=True)
        with patch.object(worker, "_run_command") as command:
            result = worker.process_request_sync("Open Windows Features to disable WSL.")
        self.assertEqual(result, worker_mod.AdminToolRequest("windows-features"))
        self.assertIn("review and apply the change in Windows Features", client.requests[0][0]["content"])
        command.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows system date")
    def test_worker_includes_the_current_pc_date_on_each_turn(self):
        client = _FakeClient([])
        worker = self._make_worker(client)
        with patch.object(assistant_context_mod, "datetime") as clock:
            clock.now.return_value.astimezone.return_value = datetime(2025, 12, 31, tzinfo=timezone.utc)
            worker.process_request_sync("What month is it?")
            clock.now.return_value.astimezone.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
            worker.process_request_sync("And what year is it now?")

        self.assertIn("2025-12-31", client.requests[0][0]["content"])
        self.assertIn("2026-01-01", client.requests[1][0]["content"])
        self.assertNotIn("2025-12-31", client.requests[1][0]["content"])
        self.assertIn("Mention it only when relevant.", client.requests[1][0]["content"])

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
