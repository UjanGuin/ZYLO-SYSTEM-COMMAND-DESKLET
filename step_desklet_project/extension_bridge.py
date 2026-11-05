#!/usr/bin/env python3
"""Single-request bridge used by the native GNOME Shell frontend."""

import json
import os
import sys

from dotenv import load_dotenv

from step_desklet.core.worker import (
    DEFAULT_NIM_API_URL,
    DEFAULT_NIM_MODEL,
    GROQ_MODEL,
    GroqChatClient,
    StepAIWorker,
)


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        prompt = str(request.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("Enter a command first.")

        load_dotenv(os.path.expanduser("~/.config/step-desklet.env"), override=False)
        initial_working_dir = os.getenv("STEP_DESKLET_WORKING_DIR", os.path.expanduser("~"))
        conversation_path = os.path.expanduser("~/.config/step-desklet/conversation.json")
        if request.get("provider") == "groq":
            worker = StepAIWorker(
                api_key="",
                model=GROQ_MODEL,
                client=GroqChatClient(os.getenv("GROQ_API_KEY") or ""),
                initial_working_dir=initial_working_dir,
                conversation_path=conversation_path,
            )
        else:
            worker = StepAIWorker(
                api_key=os.getenv("NVIDIA_API_KEY") or os.getenv("NIM_API_KEY") or "",
                api_url=os.getenv("NIM_API_URL", DEFAULT_NIM_API_URL),
                model=os.getenv("NIM_MODEL", DEFAULT_NIM_MODEL),
                initial_working_dir=initial_working_dir,
                conversation_path=conversation_path,
            )
        def report_status(status: str) -> None:
            print(json.dumps({"type": "status", "status": status}), flush=True)

        response = worker.process_request_sync(prompt, status_callback=report_status, max_steps=100)
        print(json.dumps({"type": "result", "ok": True, "response": response}), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"type": "result", "ok": False, "error": str(exc)}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
