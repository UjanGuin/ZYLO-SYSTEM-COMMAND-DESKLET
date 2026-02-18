import json
import os
import re
import shlex
import subprocess
from typing import Optional, Dict, List, Any, Callable, Tuple, Set
from PyQt6.QtCore import QObject, pyqtSignal
from openai import OpenAI

SYSTEM_PROMPT = """You are the ZYLO-SYSTEM-COMMAND-DESKLET Laptop Intelligence.
You have full, unrestricted access to this Linux machine. You are the ultimate guardian of this workstation. You are highly responsible, efficient, and professional.
You can access the full filesystem, including system folders, by using absolute paths and `sudo` when needed.
Your shell working directory persists between commands.

Your goal is to fulfill user requests by executing shell commands. You act with the authority of a lead system administrator but with the care of a personal assistant. Every command you run impacts a real-world machine; you are responsible for its health, security, and performance.

Response protocol (STRICT):
- If a shell command is needed, respond with JSON only:
  {"mode":"run","command":"<shell command>","reason":"<short reason>"}
- If the task is complete or no command is needed, respond with JSON only:
  {"mode":"final","message":"<answer to user>"}

Rules:
- One command at a time.
- For opening GUI apps/files, prefer detached launch commands so the shell returns quickly.
- For opening files, prefer `xdg-open <path>` and avoid long filesystem scans.
- For "open last N modified files in/of <folder>", do not filter by extension unless the user asked for a type.
- When opening multiple files, open each file path individually in a loop with background `xdg-open`.
- You are autonomous and decisive.
- Never include markdown fences. Output raw JSON only.
"""

DETACHED_LAUNCH_COMMANDS = {
    "xdg-open",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "firefox",
    "brave-browser",
    "code",
    "code-insiders",
    "nautilus",
    "nemo",
    "thunar",
    "dolphin",
    "evince",
    "okular",
    "zathura",
    "atril",
    "libreoffice",
    "soffice",
    "gedit",
    "xed",
    "kate",
    "mousepad",
    "pluma",
    "leafpad",
    "vlc",
    "mpv",
}

INTERACTIVE_FILE_VIEWERS = {"nano", "vim", "vi", "nvim", "less", "more", "cat"}
OPENABLE_FILE_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".md",
    ".rtf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".csv",
    ".log",
    ".json",
    ".yaml",
    ".yml",
}

MAX_HISTORY_MESSAGES = 24
MAX_FEEDBACK_CHARS = 2500
OPEN_INTENT_MAX_STEPS = 6

OPEN_FILE_INTENT_RE = re.compile(r"^\s*(?:please\s+)?open\s+(.+?)\s*$", re.IGNORECASE)
OPEN_RECENT_FILES_INTENT_RE = re.compile(
    r"^\s*(?:please\s+)?open\s+(?:the\s+)?last\s+(?P<count>\d+)\s+modified\s+files?\s+(?:in|of|from)\s+(?P<folder>.+?)\s*$",
    re.IGNORECASE,
)
EXT_WORD_TO_SUFFIX = {
    "pdf": ".pdf",
    "txt": ".txt",
    "text": ".txt",
    "md": ".md",
    "doc": ".doc",
    "docx": ".docx",
    "csv": ".csv",
    "json": ".json",
    "yaml": ".yaml",
    "yml": ".yml",
}
USER_FOLDER_ALIASES = {
    "desktop": "Desktop",
    "documents": "Documents",
    "document": "Documents",
    "downloads": "Downloads",
    "download": "Downloads",
    "music": "Music",
    "songs": "Music",
    "audio": "Music",
    "videos": "Videos",
    "video": "Videos",
    "pictures": "Pictures",
    "photos": "Pictures",
    "images": "Pictures",
}

class WorkerSignals(QObject):
    response_ready = pyqtSignal(str)
    status_update = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    clearing_response = pyqtSignal()

class StepAIWorker(QObject):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        sudo_password: str = "././././",
        initial_working_dir: str = "/",
    ):
        super().__init__()
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.sudo_password = (sudo_password or "././././").rstrip("\n")
        self.current_working_dir = self._resolve_directory(initial_working_dir or "/", "/")
        if not os.path.isdir(self.current_working_dir):
            self.current_working_dir = os.path.expanduser("~")
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.signals = WorkerSignals()

    def process_request(self, user_text: str):
        self.signals.clearing_response.emit()
        try:
            final_text = self.process_request_sync(
                user_text,
                status_callback=self.signals.status_update.emit,
                max_steps=100,
            )
            self.signals.response_ready.emit(final_text)
        except Exception as e:
            self.signals.error_occurred.emit(str(e))

    def process_request_sync(
        self,
        user_text: str,
        status_callback: Optional[Callable[[str], None]] = None,
        max_steps: int = 100,
    ) -> str:
        open_intent, normalized_target = self._detect_open_file_intent(user_text)
        recent_open_intent = self._detect_open_recent_files_intent(user_text)
        if recent_open_intent:
            prepared_text = self._augment_open_recent_files_request(
                user_text,
                recent_open_intent["count"],
                recent_open_intent["folder_path"],
            )
        else:
            prepared_text = self._augment_open_file_request(user_text, normalized_target) if open_intent else user_text

        short_context_intent = open_intent or bool(recent_open_intent)
        step_limit = min(max_steps, OPEN_INTENT_MAX_STEPS) if short_context_intent else max_steps
        response_temperature = 0.0 if short_context_intent else 0.2

        # For open-file intent, use a short-lived request context to avoid large-history latency.
        # Execution still goes through the same command engine.
        if short_context_intent:
            request_messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prepared_text},
            ]
        else:
            self._trim_history()
            self.messages.append({"role": "user", "content": prepared_text})
            request_messages = self.messages

        for _ in range(step_limit):
            if status_callback:
                status_callback("Thinking...")

            if not short_context_intent:
                self._trim_history()
                request_messages = self.messages

            response_kwargs = dict(
                model=self.model,
                messages=request_messages,
                temperature=response_temperature,
                stream=False,
            )
            if short_context_intent:
                response_kwargs["max_tokens"] = 320

            response = self.client.chat.completions.create(**response_kwargs)
            content = response.choices[0].message.content
            raw = str(content or "").strip()
            if not raw:
                request_messages.append(
                    {
                        "role": "user",
                        "content": "Your previous response was empty. Reply now with raw JSON only.",
                    }
                )
                if not short_context_intent:
                    self.messages = request_messages
                continue
            request_messages.append({"role": "assistant", "content": raw})
            if not short_context_intent:
                self.messages = request_messages

            action = self._extract_json(raw)
            if not action:
                if short_context_intent:
                    request_messages.append(
                        {
                            "role": "user",
                            "content": "Invalid format. Reply with raw JSON only, no prose.",
                        }
                    )
                    if not short_context_intent:
                        self.messages = request_messages
                    continue
                raise RuntimeError("Format Error")

            mode = action.get("mode")
            if mode == "final":
                final_message = str(action.get("message", ""))
                if short_context_intent:
                    self.messages.append({"role": "user", "content": user_text})
                    self.messages.append({"role": "assistant", "content": final_message})
                    self._trim_history()
                return final_message

            if mode == "run":
                command = str(action.get("command", "")).strip()
                reason = str(action.get("reason", "")).strip()
                if status_callback:
                    status_callback(f"Executing: {reason}")

                result = self._run_command(command)
                used_command = str(result.get("command", command))
                if (
                    short_context_intent
                    and int(result.get("exit_code", 1)) == 0
                    and self._command_looks_like_open_action(used_command)
                ):
                    final_message = self._build_open_completion_message(
                        used_command=used_command,
                        result=result,
                        normalized_target=normalized_target,
                        recent_open_intent=recent_open_intent,
                    )
                    self.messages.append({"role": "user", "content": user_text})
                    self.messages.append({"role": "assistant", "content": final_message})
                    self._trim_history()
                    return final_message

                feedback = self._build_feedback(used_command, result)
                request_messages.append({"role": "user", "content": feedback})
                if not short_context_intent:
                    self.messages = request_messages
                    self._trim_history()
                continue

            if short_context_intent:
                request_messages.append(
                    {
                        "role": "user",
                        "content": "Invalid mode. Use mode=run or mode=final with valid JSON.",
                    }
                )
                if not short_context_intent:
                    self.messages = request_messages
                continue
            raise RuntimeError(f"Invalid mode: {mode}")

        if short_context_intent:
            if status_callback:
                status_callback("Executing: fallback open handler")
            fallback_message = self._execute_open_intent_fallback(
                normalized_target=normalized_target,
                recent_open_intent=recent_open_intent,
            )
            self.messages.append({"role": "user", "content": user_text})
            self.messages.append({"role": "assistant", "content": fallback_message})
            self._trim_history()
            return fallback_message
        raise RuntimeError("Max command steps reached without final response.")

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(text):
            if ch != "{": continue
            try:
                obj, _ = decoder.raw_decode(text[idx:])
                if isinstance(obj, dict): return obj
            except: continue
        return None

    def _resolve_directory(self, path: str, base_dir: str) -> str:
        expanded = os.path.expandvars(os.path.expanduser((path or "").strip()))
        if not expanded:
            expanded = base_dir
        if not os.path.isabs(expanded):
            expanded = os.path.join(base_dir, expanded)
        return os.path.abspath(expanded)

    def _handle_cd_command(self, command: str) -> Optional[Dict[str, Any]]:
        if any(op in command for op in ("&&", "||", ";", "|", "\n")):
            return None

        try:
            parts = shlex.split(command)
        except ValueError as e:
            return {"exit_code": 1, "stdout": "", "stderr": str(e), "cwd": self.current_working_dir}

        if not parts or parts[0] != "cd":
            return None

        target = "~" if len(parts) == 1 else parts[1]
        next_dir = self._resolve_directory(target, self.current_working_dir)
        if not os.path.isdir(next_dir):
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"cd: {target}: No such file or directory",
                "cwd": self.current_working_dir,
            }

        self.current_working_dir = next_dir
        return {
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "cwd": self.current_working_dir,
        }

    def _prepare_sudo(self, command: str) -> str:
        if not self.sudo_password:
            return command
        return re.sub(r"(?<!\S)sudo(?!\s+-S)\s+", 'sudo -S -p "" ', command)

    def _parse_cwd_marker(self, stdout: str, marker: str):
        if marker not in stdout:
            return stdout, self.current_working_dir
        before, _, after = stdout.rpartition(marker)
        next_dir = after.splitlines()[0].strip() if after else ""
        cleaned_stdout = before.rstrip()
        if next_dir and os.path.isdir(next_dir):
            self.current_working_dir = next_dir
        return cleaned_stdout, self.current_working_dir

    def _looks_like_openable_file(self, target: str) -> bool:
        cleaned = (target or "").strip().strip('"').strip("'")
        if not cleaned:
            return False
        if cleaned.startswith(("-", "http://", "https://", "file://")):
            return False
        lowered = cleaned.lower()
        if any(lowered.endswith(ext) for ext in OPENABLE_FILE_EXTENSIONS):
            return True
        return "/" in cleaned or "\\" in cleaned

    def _normalize_open_target_text(self, target: str) -> str:
        cleaned = (target or "").strip().strip('"').strip("'")
        cleaned = re.sub(r"^(?:the\s+)?(?:file|document)\s+", "", cleaned, flags=re.IGNORECASE).strip()
        if not cleaned:
            return cleaned

        lowered = cleaned.lower()
        if "." not in os.path.basename(cleaned):
            for word, suffix in EXT_WORD_TO_SUFFIX.items():
                token = " " + word
                if lowered.endswith(token):
                    base = cleaned[: -len(token)].strip()
                    if base:
                        return base + suffix
        return cleaned

    def _is_file_open_intent_target(self, target: str) -> bool:
        lowered = (target or "").strip().lower()
        if self._looks_like_openable_file(target):
            return True
        return any(lowered.endswith(" " + k) for k in EXT_WORD_TO_SUFFIX.keys())

    def _detect_open_file_intent(self, user_text: str):
        match = OPEN_FILE_INTENT_RE.match(user_text or "")
        if not match:
            return False, ""
        raw_target = (match.group(1) or "").strip()
        if not raw_target:
            return False, ""
        normalized_target = self._normalize_open_target_text(raw_target)
        if not self._is_file_open_intent_target(normalized_target):
            return False, ""
        return True, normalized_target

    def _augment_open_file_request(self, user_text: str, normalized_target: str) -> str:
        return (
            f"{user_text}\n\n"
            f"[Execution hint: This is a direct file-open request. "
            f"Run one immediate command using xdg-open for target '{normalized_target}', "
            f"quote paths properly, avoid explanation, then finalize.]"
        )

    def _resolve_user_folder_hint(self, folder_text: str) -> str:
        cleaned = (folder_text or "").strip().strip('"').strip("'")
        cleaned = re.sub(r"^(?:my|the)\s+", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s+folder$", "", cleaned, flags=re.IGNORECASE).strip()

        if not cleaned:
            return os.path.expanduser("~")

        if "/" in cleaned or cleaned.startswith(("~", ".", "$")):
            expanded = os.path.expandvars(os.path.expanduser(cleaned))
            if not os.path.isabs(expanded):
                expanded = os.path.abspath(os.path.join(self.current_working_dir, expanded))
            return expanded

        home = os.path.expanduser("~")
        alias = USER_FOLDER_ALIASES.get(cleaned.lower())
        if alias:
            return os.path.join(home, alias)

        # Try direct name under home; fallback keeps user-provided directory name.
        direct_candidate = os.path.join(home, cleaned)
        if os.path.isdir(direct_candidate):
            return direct_candidate
        titled_candidate = os.path.join(home, cleaned.title())
        if os.path.isdir(titled_candidate):
            return titled_candidate
        return direct_candidate

    def _detect_open_recent_files_intent(self, user_text: str) -> Optional[Dict[str, Any]]:
        match = OPEN_RECENT_FILES_INTENT_RE.match(user_text or "")
        if not match:
            return None

        try:
            count = int(match.group("count"))
        except (TypeError, ValueError):
            return None
        if count <= 0:
            return None

        folder_raw = (match.group("folder") or "").strip()
        folder_path = self._resolve_user_folder_hint(folder_raw)
        return {"count": min(count, 20), "folder_path": folder_path}

    def _augment_open_recent_files_request(self, user_text: str, count: int, folder_path: str) -> str:
        folder_quoted = shlex.quote(folder_path)
        return (
            f"{user_text}\n\n"
            "[Execution hint: This is a multi-file open request. "
            f"Open exactly the last {count} modified regular files from {folder_quoted}. "
            "Use all file formats unless user explicitly requested an extension. "
            "Do not use a single xdg-open with many arguments. "
            "Open each file path in a loop with background xdg-open. "
            "Use one command and then finalize.]"
        )

    def _find_directory_in_home(self, target_dir_name: str) -> Optional[str]:
        hint = (target_dir_name or "").strip().strip('"').strip("'")
        if not hint:
            return None

        home = os.path.expanduser("~")
        hint_lower = os.path.basename(hint).lower()
        if not hint_lower:
            return None

        best_match: Optional[str] = None
        best_mtime = -1.0
        try:
            for current_root, dirs, _ in os.walk(home):
                rel_path = os.path.relpath(current_root, home)
                depth = 0 if rel_path == "." else rel_path.count(os.sep) + 1
                if depth > 5:
                    dirs[:] = []
                    continue
                for dir_name in dirs:
                    if dir_name.lower() != hint_lower:
                        continue
                    candidate = os.path.join(current_root, dir_name)
                    try:
                        mtime = os.path.getmtime(candidate)
                    except OSError:
                        mtime = 0.0
                    if mtime > best_mtime:
                        best_mtime = mtime
                        best_match = candidate
        except OSError:
            return None
        return best_match

    def _execute_open_single_fallback(self, normalized_target: str) -> str:
        resolved_target = self._resolve_open_target(normalized_target)

        if self._looks_like_openable_file(resolved_target):
            expanded = os.path.expandvars(os.path.expanduser(resolved_target))
            if not os.path.isabs(expanded):
                expanded = os.path.abspath(os.path.join(self.current_working_dir, expanded))
            if not os.path.exists(expanded):
                return f"Unable to open {normalized_target}. It may not exist."
            resolved_target = expanded

        result = self._run_command(f"xdg-open {shlex.quote(resolved_target)}")
        if int(result.get("exit_code", 1)) == 0:
            return f"Opened {resolved_target}"
        return f"Unable to open {normalized_target}. It may not exist."

    def _execute_open_recent_files_fallback(self, count: int, folder_path: str) -> str:
        folder = os.path.expandvars(os.path.expanduser(folder_path))
        if not os.path.isabs(folder):
            folder = os.path.abspath(os.path.join(self.current_working_dir, folder))

        if not os.path.isdir(folder):
            found_dir = self._find_directory_in_home(folder)
            if found_dir:
                folder = found_dir

        if not os.path.isdir(folder):
            return f"Unable to find folder: {folder_path}"

        folder_q = shlex.quote(folder)
        source = (
            f"find {folder_q} -type f -printf '%T@\\t%p\\n' 2>/dev/null "
            f"| sort -nr | head -n {max(1, int(count))} | cut -f2-"
        )
        command = (
            "__step_opened=0; "
            "while IFS= read -r __step_file; do "
            "[ -n \"$__step_file\" ] || continue; "
            "nohup xdg-open \"$__step_file\" >/dev/null 2>&1 & "
            "__step_opened=$((__step_opened+1)); "
            "done < <(" + source + "); "
            "echo \"__STEP_OPENED_COUNT__=$__step_opened\""
        )
        result = self._run_command(command)
        stdout = str(result.get("stdout", ""))
        match = re.search(r"__STEP_OPENED_COUNT__=(\d+)", stdout)
        opened = int(match.group(1)) if match else 0
        if opened > 0:
            return f"Opened {opened} most recently modified files from {folder}"
        return f"No files found in {folder}"

    def _execute_open_intent_fallback(
        self,
        normalized_target: str,
        recent_open_intent: Optional[Dict[str, Any]],
    ) -> str:
        if recent_open_intent:
            count = int(recent_open_intent.get("count", 3))
            folder_path = str(recent_open_intent.get("folder_path", "") or "")
            return self._execute_open_recent_files_fallback(count=count, folder_path=folder_path)

        if normalized_target:
            return self._execute_open_single_fallback(normalized_target)

        return "Unable to determine what to open."

    def _command_looks_like_open_action(self, command: str) -> bool:
        stripped = (command or "").strip()
        if not stripped:
            return False
        lowered = stripped.lower()
        if "xdg-open" in lowered:
            return True

        try:
            parts = shlex.split(stripped)
        except ValueError:
            return "gio open" in lowered

        if not parts:
            return False

        launcher = os.path.basename(parts[0]).lower()
        if launcher in DETACHED_LAUNCH_COMMANDS:
            return True
        if launcher == "gio" and len(parts) >= 2 and parts[1] == "open":
            return True
        return False

    def _build_open_completion_message(
        self,
        used_command: str,
        result: Dict[str, Any],
        normalized_target: str,
        recent_open_intent: Optional[Dict[str, Any]],
    ) -> str:
        stdout = str(result.get("stdout", "") or "")
        count_match = re.search(r"__STEP_OPENED_COUNT__=(\d+)", stdout)
        if count_match:
            opened = int(count_match.group(1))
            if opened > 0:
                return f"Opened {opened} requested files."
            return "No files found to open."

        if recent_open_intent:
            count = int(recent_open_intent.get("count", 0) or 0)
            if count > 0:
                return f"Opened requested files (up to {count})."
            return "Opened requested files."

        if normalized_target:
            return f"Opened {normalized_target}"

        try:
            parts = shlex.split(used_command)
        except ValueError:
            return "Open completed."

        if len(parts) >= 2 and os.path.basename(parts[0]) in {"xdg-open", "open"}:
            return f"Opened {parts[1]}"
        if len(parts) >= 3 and os.path.basename(parts[0]) == "gio" and parts[1] == "open":
            return f"Opened {parts[2]}"
        return "Open completed."

    def _find_file_in_common_locations(self, target_name: str) -> Optional[str]:
        if not target_name:
            return None

        home = os.path.expanduser("~")
        roots = [
            self.current_working_dir,
            os.path.join(home, "Desktop"),
            os.path.join(home, "Downloads"),
            os.path.join(home, "Documents"),
            os.path.join(home, "Music"),
            os.path.join(home, "Videos"),
            os.path.join(home, "Pictures"),
        ]

        target_lower = target_name.lower()
        target_stem, target_ext = os.path.splitext(target_lower)

        def find_best_match(search_roots: List[str], max_depth: int, allow_partial: bool) -> Optional[str]:
            best: Optional[Tuple[int, float, str]] = None
            seen: Set[str] = set()
            for root in search_roots:
                root = os.path.abspath(root)
                if root in seen or not os.path.isdir(root):
                    continue
                seen.add(root)
                try:
                    for current_root, dirs, files in os.walk(root):
                        rel_path = os.path.relpath(current_root, root)
                        depth = 0 if rel_path == "." else rel_path.count(os.sep) + 1
                        if depth > max_depth:
                            dirs[:] = []
                            continue

                        for file_name in files:
                            file_lower = file_name.lower()
                            score = 0
                            if file_name == target_name:
                                score = 6
                            elif file_lower == target_lower:
                                score = 5
                            else:
                                file_stem, file_ext = os.path.splitext(file_lower)
                                if not target_ext and target_stem and file_stem == target_stem:
                                    score = 4
                                elif (
                                    allow_partial
                                    and (target_lower in file_lower or file_lower in target_lower)
                                    and (not target_ext or file_ext == target_ext)
                                ):
                                    norm_target = re.sub(r"[^a-z0-9]+", "", target_stem or target_lower)
                                    norm_file = re.sub(r"[^a-z0-9]+", "", file_stem or file_lower)
                                    if min(len(norm_target), len(norm_file)) >= 4:
                                        score = 2

                            if score == 0:
                                continue

                            candidate = os.path.join(current_root, file_name)
                            try:
                                mtime = os.path.getmtime(candidate)
                            except OSError:
                                mtime = 0.0

                            if best is None or (score, mtime) > (best[0], best[1]):
                                best = (score, mtime, candidate)
                except OSError:
                    continue
            return best[2] if best else None

        quick_match = find_best_match(roots, max_depth=4, allow_partial=False)
        if quick_match:
            return quick_match

        broader_match = find_best_match(roots, max_depth=5, allow_partial=True)
        if broader_match:
            return broader_match

        return find_best_match([home], max_depth=6, allow_partial=True)

    def _resolve_open_target(self, target: str) -> str:
        if not self._looks_like_openable_file(target):
            return target

        expanded = os.path.expandvars(os.path.expanduser(target))
        if os.path.isabs(expanded):
            if os.path.exists(expanded):
                return expanded
            found = self._find_file_in_common_locations(os.path.basename(expanded))
            return found if found else target

        local_candidate = os.path.abspath(os.path.join(self.current_working_dir, expanded))
        if os.path.exists(local_candidate):
            return local_candidate

        found = self._find_file_in_common_locations(os.path.basename(expanded))
        if found:
            return found
        return target

    def _rewrite_bulk_open_command(self, command: str) -> str:
        stripped = (command or "").strip()
        if not stripped:
            return stripped

        # Convert "... | xargs ... xdg-open" into per-file background opens.
        xargs_match = re.match(r"^(?P<src>.+?)\|\s*xargs(?:\s+.+?)?\s+xdg-open(?:\s+.+?)?\s*$", stripped)
        if xargs_match:
            source_cmd = xargs_match.group("src").strip()
            if source_cmd:
                return (
                    "while IFS= read -r __step_file; do "
                    "nohup xdg-open \"$__step_file\" >/dev/null 2>&1 & "
                    "done < <(" + source_cmd + ")"
                )

        # Convert "xdg-open $(...)" style to per-file opens.
        subshell_match = re.match(r"^xdg-open\s+\$\((?P<src>.+)\)\s*$", stripped)
        if subshell_match:
            source_cmd = subshell_match.group("src").strip()
            if source_cmd:
                return (
                    "while IFS= read -r __step_file; do "
                    "nohup xdg-open \"$__step_file\" >/dev/null 2>&1 & "
                    "done < <(" + source_cmd + ")"
                )

        return stripped

    def _normalize_open_command(self, command: str) -> str:
        stripped = (command or "").strip()
        if not stripped:
            return stripped

        rewritten = self._rewrite_bulk_open_command(stripped)
        if rewritten != stripped:
            return rewritten

        # Never normalize multi-part shell expressions; keep pipes/redirects semantics intact.
        if any(op in stripped for op in ("&&", "||", "|", ";", "\n", ">", "<", "$(", "`")):
            return stripped
        try:
            parts = shlex.split(stripped)
        except ValueError:
            return stripped
        if not parts:
            return stripped

        launcher = os.path.basename(parts[0])

        # Translate macOS-style "open file.pdf" to Linux xdg-open.
        if launcher == "open" and len(parts) >= 2:
            parts = ["xdg-open", *parts[1:]]
            launcher = "xdg-open"

        # If model uses terminal viewers for "open" tasks, switch to xdg-open.
        if launcher in INTERACTIVE_FILE_VIEWERS and len(parts) == 2 and self._looks_like_openable_file(parts[1]):
            parts = ["xdg-open", parts[1]]
            launcher = "xdg-open"

        if launcher == "gio" and len(parts) >= 3 and parts[1] == "open":
            parts[2] = self._resolve_open_target(parts[2])
            return shlex.join(parts)

        if launcher in DETACHED_LAUNCH_COMMANDS and len(parts) >= 2:
            parts[1] = self._resolve_open_target(parts[1])
            return shlex.join(parts)

        return shlex.join(parts)

    def _should_auto_detach(self, command: str) -> bool:
        stripped = (command or "").strip()
        if not stripped:
            return False
        if stripped.endswith("&"):
            return False
        if "sudo" in stripped:
            return False
        if any(op in stripped for op in ("&&", "||", "|", ";", "\n")):
            return False
        try:
            parts = shlex.split(stripped)
        except ValueError:
            return False
        if not parts:
            return False
        launcher = os.path.basename(parts[0])
        if launcher in DETACHED_LAUNCH_COMMANDS:
            return True
        if launcher == "gio" and len(parts) > 1 and parts[1] == "open":
            return True
        return False

    def _run_detached_command(self, command: str) -> Dict[str, Any]:
        marker = "__STEP_CWD_MARKER__"
        detached_cmd = f'nohup {command} >/dev/null 2>&1 &'
        wrapped_cmd = f'{detached_cmd}\nprintf "\\n{marker}%s\\n" "$PWD"'

        try:
            proc = subprocess.run(
                wrapped_cmd,
                shell=True,
                executable="/bin/bash",
                cwd=self.current_working_dir,
                text=True,
                capture_output=True,
                timeout=20,
            )
            stdout, _ = self._parse_cwd_marker(proc.stdout, marker)
            return {
                "command": command,
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": proc.stderr,
                "cwd": self.current_working_dir,
            }
        except Exception as e:
            return {
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "cwd": self.current_working_dir,
            }

    def _run_command(self, command: str) -> Dict[str, Any]:
        normalized_command = self._normalize_open_command(command)

        cd_result = self._handle_cd_command(normalized_command.strip())
        if cd_result is not None:
            cd_result["command"] = normalized_command
            return cd_result

        if self._should_auto_detach(normalized_command):
            return self._run_detached_command(normalized_command)

        cmd = self._prepare_sudo(normalized_command)
        marker = "__STEP_CWD_MARKER__"
        wrapped_cmd = f'{cmd}\nprintf "\\n{marker}%s\\n" "$PWD"'

        try:
            proc = subprocess.run(
                wrapped_cmd,
                shell=True,
                executable="/bin/bash",
                cwd=self.current_working_dir,
                text=True,
                capture_output=True,
                timeout=120,
                input=(self.sudo_password + "\n") if ("sudo -S" in cmd and self.sudo_password) else None,
            )

            stdout, _ = self._parse_cwd_marker(proc.stdout, marker)

            return {
                "command": normalized_command,
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": proc.stderr,
                "cwd": self.current_working_dir,
            }
        except Exception as e:
            return {
                "command": normalized_command,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "cwd": self.current_working_dir,
            }

    def _trim_history(self):
        if not self.messages:
            self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            return

        system_message = self.messages[0]
        tail = self.messages[1:]
        if len(tail) > MAX_HISTORY_MESSAGES:
            tail = tail[-MAX_HISTORY_MESSAGES:]
        self.messages = [system_message, *tail]

    def _truncate_feedback(self, text: Any) -> str:
        value = str(text or "")
        if len(value) <= MAX_FEEDBACK_CHARS:
            return value
        return value[:MAX_FEEDBACK_CHARS].rstrip() + "\n...[truncated]"

    def _build_feedback(self, command: str, result: Dict[str, Any]) -> str:
        return (
            f"cwd: {result.get('cwd', self.current_working_dir)}\n"
            f"cmd: {command}\n"
            f"exit: {result['exit_code']}\n"
            f"out: {self._truncate_feedback(result.get('stdout', ''))}\n"
            f"err: {self._truncate_feedback(result.get('stderr', ''))}"
        )

