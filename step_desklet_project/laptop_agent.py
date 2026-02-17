#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openai import OpenAI
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "stepfun-ai/step-3.5-flash"
MAX_OUTPUT_CHARS = 12000
HARDCODED_TEST_API_KEY = "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"

SYSTEM_PROMPT = """You are the Step-3.5 Laptop Intelligence.
You have full, unrestricted access to this Linux machine. You are the ultimate guardian of this workstation. You are highly responsible, efficient, and professional. 

Your goal is to fulfill user requests by executing shell commands. You act with the authority of a lead system administrator but with the care of a personal assistant. Every command you run impacts a real-world machine; you are responsible for its health, security, and performance.

Response protocol (STRICT):
- If a shell command is needed, respond with JSON only:
  {"mode":"run","command":"<shell command>","reason":"<short reason>"}
- If the task is complete or no command is needed, respond with JSON only:
  {"mode":"final","message":"<answer to user>"}

Rules:
- One command at a time.
- You are autonomous and decisive. If you need information, find it. If you need to install something, do it.
- Your actions are final and impactful. You are the guardian of this laptop. Optimize for the user's best interest.
- Never include markdown fences. Output raw JSON only.
"""


@dataclass
class AgentConfig:
    model: str
    base_url: str
    api_key: str
    temperature: float
    top_p: float
    max_tokens: int
    command_timeout: int
    max_steps: int
    auto_approve: bool
    unsafe: bool
    show_reasoning: bool


@dataclass
class AgentState:
    messages: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class CommandResult:
    exit_code: int
    elapsed_sec: float
    stdout: str
    stderr: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.error


class PremiumUI:
    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg
        self.console = Console()

    def show_boot(self) -> None:
        title = Text("Step-3.5 Intelligence", style="bold #00d4ff")
        subtitle = Text("Laptop Guardian Active", style="#a8dadc")

        body = Group(
            Align.center(title),
            Align.center(subtitle),
        )
        self.console.print(
            Panel(
                body,
                title="[bold #f1faee]SYSTEM[/]",
                border_style="#00d4ff",
                box=box.ROUNDED,
                padding=(0, 2),
            )
        )

    def prompt_user(self) -> str:
        return Prompt.ask("[bold #00d4ff]Step-3.5[/] > ").strip()

    def show_step(self, step: int, max_steps: int) -> None:
        pass  # Hidden for widget feel

    def show_user_message(self, text: str) -> None:
        self.console.print(
            Panel(
                Text(text, style="#f1faee"),
                title="[bold #8ecae6]You[/]",
                border_style="#219ebc",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def stream_completion(self, client: OpenAI, cfg: AgentConfig, messages: List[Dict[str, str]]) -> str:
        with Live(
            Panel(Text("Thinking...", style="italic #f4a261"), title="[bold #00d4ff]Step-3.5[/]", border_style="#00d4ff", box=box.ROUNDED),
            console=self.console,
            refresh_per_second=4,
        ) as live:
            response = client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                temperature=cfg.temperature,
                top_p=cfg.top_p,
                max_tokens=cfg.max_tokens,
                stream=False,  # Disable streaming for cleaner UI
            )
            content = response.choices[0].message.content or ""
            live.update(Panel(Text("Executing...", style="italic #00d4ff"), title="[bold #00d4ff]Step-3.5[/]", border_style="#00d4ff", box=box.ROUNDED))
            return content.strip()

    def show_action(self, reason: str, command: str) -> None:
        # Subtle action display
        self.console.print(f"[dim]⚡ {reason}[/]")
        self.console.print(Syntax(f"  $ {command}", "bash", theme="monokai", word_wrap=True))

    def ask_approval(self, command: str) -> bool:
        return Confirm.ask(f"[bold #fcbf49]Confirm execution: {command}?[/]", default=True, console=self.console)

    def show_result(self, command: str, result: CommandResult) -> None:
        if not result.ok:
            self.show_error(f"Command failed: {result.stderr or result.error}")
        # Otherwise stay quiet unless requested

    def show_assistant_message(self, message: str) -> None:
        md = Markdown(message or "(empty)")
        self.console.print(
            Panel(
                md,
                title="[bold #00d4ff]Step-3.5[/]",
                border_style="#00d4ff",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def show_warning(self, message: str) -> None:
        self.console.print(
            Panel(
                Text(message, style="#ffd60a"),
                title="[bold #ffd60a]Step-3.5 Warning[/]",
                border_style="#e85d04",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def show_error(self, message: str) -> None:
        self.console.print(
            Panel(
                Text(message, style="#ff4d6d"),
                title="[bold #ff4d6d]Step-3.5 Error[/]",
                border_style="#c1121f",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def show_exit(self) -> None:
        self.console.print(Rule(style="#264653"))
        self.console.print(Panel(Text("Session closed.", style="#f1faee"), border_style="#1d3557", box=box.ROUNDED))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Step-3.5-flash CLI agent")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--api-key",
        default=os.getenv("NVIDIA_API_KEY") or os.getenv("STEP_API_KEY") or HARDCODED_TEST_API_KEY,
    )
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--command-timeout", type=int, default=120)
    parser.add_argument("--max-steps", type=int, default=1000000)
    parser.add_argument("--no-auto-approve", action="store_false", dest="auto_approve", help="Disable automatic command execution")
    parser.add_argument("--safe", action="store_false", dest="unsafe", help="Block potentially destructive commands")
    parser.set_defaults(auto_approve=True, unsafe=True)
    parser.add_argument("--show-reasoning", action="store_true", help="Render reasoning stream when available")
    return parser.parse_args()


def build_client(api_key: str, base_url: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url)


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def truncate(s: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(s) <= limit:
        return s
    clipped = len(s) - limit
    return f"{s[:limit]}\n\n[...truncated {clipped} chars...]"


def looks_dangerous(command: str) -> bool:
    risky_patterns = [
        r"(^|\s)rm\s+-rf\s+/",
        r"(^|\s)mkfs(\.|\s)",
        r"(^|\s)dd\s+if=",
        r"(:\(\)\{\s*:|fork bomb)",
        r"(^|\s)shutdown(\s|$)",
        r"(^|\s)reboot(\s|$)",
        r"(^|\s)poweroff(\s|$)",
        r"(^|\s)chmod\s+-R\s+777\s+/",
    ]
    return any(re.search(pattern, command) for pattern in risky_patterns)


def run_command(command: str, timeout: int) -> CommandResult:
    started = time.time()
    sudo_password = "././././"
    input_data = None
    cmd_to_run = command
    
    if "sudo " in command:
        # Inject -S to read password from stdin
        cmd_to_run = command.replace("sudo ", "sudo -S ", 1)
        input_data = sudo_password + "\n"

    try:
        completed = subprocess.run(
            cmd_to_run,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            input=input_data
        )
        return CommandResult(
            exit_code=completed.returncode,
            elapsed_sec=time.time() - started,
            stdout=truncate(completed.stdout),
            stderr=truncate(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        so = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        se = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return CommandResult(
            exit_code=-1,
            elapsed_sec=time.time() - started,
            stdout=truncate(so),
            stderr=truncate(se),
            error=f"timeout after {timeout}s",
        )


def build_feedback(command: str, result: CommandResult) -> str:
    error_line = f"\nerror: {result.error}" if result.error else ""
    return (
        "Command execution result below. Continue solving the same user request.\n"
        f"command: {command}\n"
        f"exit_code: {result.exit_code}\n"
        f"elapsed_sec: {result.elapsed_sec:.2f}\n"
        f"stdout:\n{result.stdout if result.stdout else '(empty)'}\n"
        f"stderr:\n{result.stderr if result.stderr else '(empty)'}"
        f"{error_line}"
    )


def handle_user_request(client: OpenAI, cfg: AgentConfig, state: AgentState, ui: PremiumUI, user_text: str) -> None:
    state.messages.append({"role": "user", "content": user_text})
    ui.show_user_message(user_text)

    for step in range(1, cfg.max_steps + 1):
        raw = ui.stream_completion(client, cfg, state.messages)
        state.messages.append({"role": "assistant", "content": raw})

        action = extract_json_object(raw)
        if not action:
            ui.show_error("Internal error: Invalid response from Step-3.5")
            return

        mode = action.get("mode")
        if mode == "final":
            ui.show_assistant_message(str(action.get("message", "")))
            return

        if mode != "run":
            return

        command = str(action.get("command", "")).strip()
        reason = str(action.get("reason", "")).strip()
        if not command:
            return

        ui.show_action(reason, command)

        # Safety check (even if unsafe is true, we log it)
        if looks_dangerous(command) and not cfg.unsafe:
            ui.show_warning("Restricted command blocked.")
            return

        approved = cfg.auto_approve or ui.ask_approval(command)
        if not approved:
            return

        result = run_command(command, cfg.command_timeout)
        ui.show_result(command, result)
        feedback = build_feedback(command, result)
        state.messages.append({"role": "user", "content": feedback})


def main() -> int:
    args = parse_args()
    if not args.api_key:
        print(
            "Missing API key. Set NVIDIA_API_KEY or STEP_API_KEY, or pass --api-key.",
            file=sys.stderr,
        )
        return 1

    cfg = AgentConfig(
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
        command_timeout=args.command_timeout,
        max_steps=args.max_steps,
        auto_approve=args.auto_approve,
        unsafe=args.unsafe,
        show_reasoning=args.show_reasoning,
    )
    ui = PremiumUI(cfg)

    client = build_client(cfg.api_key, cfg.base_url)
    state = AgentState(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    ui.show_boot()

    while True:
        try:
            user_text = ui.prompt_user()
        except (EOFError, KeyboardInterrupt):
            ui.show_exit()
            break

        if not user_text:
            continue
        if user_text in {"/exit", "exit", "quit"}:
            ui.show_exit()
            break

        handle_user_request(client, cfg, state, ui, user_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
