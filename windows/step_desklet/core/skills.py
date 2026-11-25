"""Dynamic PowerShell plugin discovery, trust verification, disable policy, and audit history for zylo_skills/."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from step_desklet.core.config import (
    ensure_config_dir,
    get_config_dir,
    get_skill_history_file,
    get_skills_policy_file,
    read_json_file,
    write_json_file,
)

SKILL_ID_RE = re.compile(r"[^a-z0-9_-]+")
PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_SKILL_HISTORY_ENTRIES = 200
_SUPERSEDED_BUNDLED_SKILLS = {
    "find_file.ps1": {
        "c3cecb29eaeb5a6941c45d1e43398f87d9c62d431f81c5ce6b381912b923ff5e",
        "e4209a85bc5eee35780bc0d10594209a533a452afd9b159f1968be45ab9bf134",
        "93ba5405af5e4de17f1f824964df1a2f4503069c20b15fc95094b4beb4a7728c",
        "b68786568f5a32a042a9b2f653297e0f8ee6f8ed9745ba63b4227c8270cb0671",
        "ca846edc36271045636fa7459dd9ee4c248a884e5f99fa035481653a6fb202a8",
        "4c7700eb7595cbd29b91127cc6d455d84b843bde01f7ee02233f8c707e49da70",
        "22280a8ccf7344e315354267ca7b2dc4b178d9e1129cd21f27e0331b856ab336",
    },
    "media_controls.ps1": {"7df7a93b2bb26382fc2cdd83a7f1262ae52278282b36697889775a347f980c31"},
    "installed_apps.ps1": {
        "8b4ee1f812b9de924e1565d7e1ac2dcdcfd79b89d6a0da6d4c12de338ab199ff",
        "b1e4dd2a79ee83043addbbdfb1bee303e578f059edb4f9cc4a52ac223b1d4386",
    },
}


@dataclass
class ZyloSkill:
    skill_id: str
    description: str
    params_hint: str
    script_path: Path
    source: str  # "user" or "bundled"
    sha256: str = ""
    enabled: bool = True
    trusted: bool = False
    trust_reason: str = ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compute_script_sha256(script_path: Path) -> str:
    """Compute SHA-256 digest of a PowerShell skill file for tamper/trust verification."""
    try:
        return hashlib.sha256(script_path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _bundled_skills_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        for candidate in (exe_dir / "zylo_skills", exe_dir / "_internal" / "zylo_skills"):
            if candidate.exists():
                return candidate
        return exe_dir / "zylo_skills"
    return Path(__file__).resolve().parents[2] / "zylo_skills"


def _bundled_skill_hashes() -> Dict[str, str]:
    """Return {script_filename.lower(): sha256} for built-in starter scripts shipped with ZYLO."""
    hashes: Dict[str, str] = {}
    bundled = _bundled_skills_dir()
    if bundled.is_dir():
        for script in bundled.glob("*.ps1"):
            digest = compute_script_sha256(script)
            if digest:
                hashes[script.name.lower()] = digest
    return hashes


def get_user_skills_dir() -> Path:
    return get_config_dir() / "zylo_skills"


def ensure_skills_directory() -> Path:
    """Add newly shipped skills on upgrades without replacing user scripts."""
    user_dir = get_user_skills_dir()
    user_dir.mkdir(parents=True, exist_ok=True)
    bundled = _bundled_skills_dir()
    if bundled.is_dir():
        user_ids = {_parse_ps1_metadata(path, "user").skill_id for path in user_dir.glob("*.ps1")}
        for script in bundled.glob("*.ps1"):
            target = user_dir / script.name
            if target.exists():
                old_digests = _SUPERSEDED_BUNDLED_SKILLS.get(script.name.lower(), ())
                should_copy = bool(old_digests) and compute_script_sha256(target) in old_digests
            else:
                should_copy = _parse_ps1_metadata(script, "bundled").skill_id not in user_ids
            if should_copy:
                try:
                    shutil.copy2(script, target)
                except OSError:
                    pass
    return user_dir


def _sanitize_skill_id(raw: str) -> str:
    cleaned = SKILL_ID_RE.sub("-", (raw or "").strip().lower()).strip("-")
    return cleaned or "custom-skill"


def _parse_ps1_metadata(script_path: Path, source: str) -> ZyloSkill:
    skill_id = _sanitize_skill_id(script_path.stem.replace("_", "-"))
    description = ""
    params_hint = "none"
    first_comment = ""

    try:
        lines = script_path.read_text(encoding="utf-8", errors="replace").splitlines()[:30]
    except OSError:
        lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        comment_body = stripped.lstrip("#").strip()
        lower = comment_body.lower()
        if lower.startswith("@skill-name:"):
            skill_id = _sanitize_skill_id(comment_body.split(":", 1)[1])
        elif lower.startswith("@skill-description:"):
            description = comment_body.split(":", 1)[1].strip()
        elif lower.startswith("@skill-params:"):
            params_hint = comment_body.split(":", 1)[1].strip() or "none"
        elif not first_comment and comment_body and not comment_body.startswith("@"):
            first_comment = comment_body

    if not description:
        description = first_comment or f"Custom PowerShell skill ({script_path.name})"

    return ZyloSkill(
        skill_id=skill_id,
        description=description,
        params_hint=params_hint,
        script_path=script_path.resolve(),
        source=source,
        sha256=compute_script_sha256(script_path),
    )


def load_skills_policy() -> Dict[str, Any]:
    """Load persisted disabled-skills list, trusted-skill SHA-256 allowlist, and revoked list."""
    raw = read_json_file(get_skills_policy_file(), default={})
    disabled = sorted(
        {
            _sanitize_skill_id(str(item))
            for item in (raw.get("disabled") if isinstance(raw.get("disabled"), list) else [])
            if str(item).strip()
        }
    )
    revoked = sorted(
        {
            _sanitize_skill_id(str(item))
            for item in (raw.get("revoked") if isinstance(raw.get("revoked"), list) else [])
            if str(item).strip()
        }
    )
    trusted_raw = raw.get("trusted") if isinstance(raw.get("trusted"), dict) else {}
    trusted: Dict[str, Dict[str, Any]] = {}
    for k, v in trusted_raw.items():
        sid = _sanitize_skill_id(str(k))
        if isinstance(v, dict) and str(v.get("sha256") or "").strip():
            trusted[sid] = {
                "sha256": str(v["sha256"]).strip().lower(),
                "trustedAt": str(v.get("trustedAt") or ""),
                "source": str(v.get("source") or "user"),
                "scriptName": str(v.get("scriptName") or ""),
            }
    return {
        "disabled": disabled,
        "revoked": revoked,
        "trusted": trusted,
    }


def save_skills_policy(policy: Dict[str, Any]) -> None:
    payload = {
        "disabled": sorted(set(policy.get("disabled") or [])),
        "revoked": sorted(set(policy.get("revoked") or [])),
        "trusted": policy.get("trusted") or {},
    }
    write_json_file(get_skills_policy_file(), payload)


def evaluate_skill_status(
    skill: ZyloSkill,
    policy: Optional[Dict[str, Any]] = None,
    bundled_hashes: Optional[Dict[str, str]] = None,
) -> Tuple[bool, bool, str]:
    """Return (enabled, trusted, reason) for a discovered skill.

    Bundled starter scripts that are byte-identical to ZYLO's shipped copy are
    trusted unless explicitly revoked by the user. User-dropped scripts or
    any edited script must match an explicit SHA-256 entry in trusted-skills.
    """
    pol = policy if policy is not None else load_skills_policy()
    enabled = skill.skill_id not in set(pol.get("disabled", []))
    revoked = skill.skill_id in set(pol.get("revoked", []))
    current_hash = (skill.sha256 or compute_script_sha256(skill.script_path)).lower()

    if not current_hash:
        return enabled, False, "Unable to read skill file to verify SHA-256 integrity."

    if revoked:
        return enabled, False, f"Skill '{skill.skill_id}' was explicitly untrusted by the user."

    filename = skill.script_path.name.lower()
    bundled_map = bundled_hashes if bundled_hashes is not None else _bundled_skill_hashes()
    bundled_digest = bundled_map.get(filename, "").lower()
    trusted_entry = (pol.get("trusted") or {}).get(skill.skill_id)
    if trusted_entry:
        expected_hash = str(trusted_entry.get("sha256") or "").lower()
        if expected_hash and expected_hash == current_hash:
            return enabled, True, "Trusted via verified SHA-256 allowlist."
        if current_hash == bundled_digest and expected_hash in _SUPERSEDED_BUNDLED_SKILLS.get(filename, ()):
            return enabled, True, "Verified upgrade of the previously trusted built-in skill."
        return (
            enabled,
            False,
            f"Script contents changed since trust approval (expected {expected_hash[:12]}, got {current_hash[:12]}).",
        )

    if bundled_digest == current_hash:
        return enabled, True, "Verified built-in starter skill (matches bundled SHA-256)."

    return (
        enabled,
        False,
        f"Untrusted custom script (sha256 {current_hash[:12]}... is not in trusted-skill allowlist).",
    )


def trust_skill(skill: ZyloSkill) -> Dict[str, Any]:
    """Add or update a skill's current SHA-256 digest in the trusted-skill allowlist."""
    policy = load_skills_policy()
    digest = (skill.sha256 or compute_script_sha256(skill.script_path)).lower()
    if not digest:
        raise RuntimeError(f"Cannot compute SHA-256 for {skill.script_path}")
    policy["revoked"] = [sid for sid in policy.get("revoked", []) if sid != skill.skill_id]
    policy.setdefault("trusted", {})[skill.skill_id] = {
        "sha256": digest,
        "trustedAt": _utc_now_iso(),
        "source": skill.source,
        "scriptName": skill.script_path.name,
    }
    save_skills_policy(policy)
    return policy


def untrust_skill(skill_id: str) -> Dict[str, Any]:
    """Remove a skill from the trusted allowlist and record explicit revocation."""
    sid = _sanitize_skill_id(skill_id)
    policy = load_skills_policy()
    policy.setdefault("trusted", {}).pop(sid, None)
    revoked = set(policy.get("revoked", []))
    revoked.add(sid)
    policy["revoked"] = sorted(revoked)
    save_skills_policy(policy)
    return policy


def set_skill_enabled(skill_id: str, enabled: bool) -> Dict[str, Any]:
    """Enable or disable an individual skill by ID."""
    sid = _sanitize_skill_id(skill_id)
    policy = load_skills_policy()
    disabled = set(policy.get("disabled", []))
    if enabled:
        disabled.discard(sid)
    else:
        disabled.add(sid)
    policy["disabled"] = sorted(disabled)
    save_skills_policy(policy)
    return policy


def authorize_skill_execution(
    skill: ZyloSkill,
    policy: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Verify both enabled state and SHA-256 trust before executing any PowerShell skill."""
    enabled, trusted, reason = evaluate_skill_status(skill, policy=policy)
    if not enabled:
        return False, f"Skill '{skill.skill_id}' is disabled by user policy."
    if not trusted:
        return False, (
            f"Skill '{skill.skill_id}' is not trusted for automatic execution: {reason} "
            f"Approve it in System Tray -> Manage Skills & Trust."
        )
    return True, "Authorized"


def discover_skills(include_disabled: bool = True) -> Dict[str, ZyloSkill]:
    """Scan bundled and user zylo_skills/ directories and annotate each with policy/trust status."""
    registry: Dict[str, ZyloSkill] = {}

    bundled = _bundled_skills_dir()
    if bundled.is_dir():
        for script in sorted(bundled.glob("*.ps1")):
            skill = _parse_ps1_metadata(script, source="bundled")
            registry[skill.skill_id] = skill

    user_dir = ensure_skills_directory()
    if user_dir.is_dir():
        for script in sorted(user_dir.glob("*.ps1")):
            skill = _parse_ps1_metadata(script, source="user")
            registry[skill.skill_id] = skill

    policy = load_skills_policy()
    bundled_hashes = _bundled_skill_hashes()
    filtered: Dict[str, ZyloSkill] = {}
    for sid, skill in registry.items():
        enabled, trusted, reason = evaluate_skill_status(
            skill, policy=policy, bundled_hashes=bundled_hashes
        )
        skill.enabled = enabled
        skill.trusted = trusted
        skill.trust_reason = reason
        if include_disabled or enabled:
            filtered[sid] = skill

    return filtered


def format_skills_prompt_block(skills: Dict[str, ZyloSkill]) -> str:
    enabled_skills = [s for s in skills.values() if s.enabled]
    if not enabled_skills:
        return "Custom Skills Directory (zylo_skills/): No enabled .ps1 skill scripts currently active."

    lines = [
        "Installed Custom PowerShell Skills (zylo_skills/):",
        "Prefer invoking a matching trusted skill via {\"mode\":\"skill\",\"skill\":\"<id>\",\"args\":{...},\"reason\":\"...\"} whenever relevant.",
        "Only skills marked [TRUSTED] may run automatically; [UNTRUSTED] skills require user trust approval first:",
    ]
    for skill in enabled_skills:
        trust_tag = "TRUSTED" if skill.trusted else "UNTRUSTED - requires user approval"
        lines.append(
            f"- {skill.skill_id} [{trust_tag}]: {skill.description} | params: {skill.params_hint}"
        )
    return "\n".join(lines)


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_skill_command(skill: ZyloSkill, args: Dict[str, Any] | None = None) -> str:
    """Build a safely-quoted PowerShell invocation string for a discovered .ps1 skill.

    Note: Safe argument quoting prevents shell injection in parameters, but only the
    SHA-256 trust policy authorizes executing the target script itself.
    """
    parts = [f"& {_ps_quote(str(skill.script_path))}"]
    if isinstance(args, dict):
        for raw_key, raw_val in args.items():
            key = str(raw_key or "").strip().lstrip("-")
            if not PARAM_NAME_RE.match(key):
                continue
            if raw_val is None or raw_val == "":
                continue
            if isinstance(raw_val, bool):
                if raw_val:
                    parts.append(f"-{key}")
            else:
                parts.append(f"-{key} {_ps_quote(str(raw_val))}")
    return " ".join(parts)


def read_skill_history(limit: int = 100) -> List[Dict[str, Any]]:
    """Return recent skill execution audit records (newest first)."""
    history_path = get_skill_history_file()
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else data.get("entries", [])
    except (OSError, ValueError, AttributeError):
        return []
    valid = [item for item in entries if isinstance(item, dict)]
    valid.reverse()
    return valid[: max(1, int(limit))]


def record_skill_execution(
    skill_id: str,
    *,
    script_path: str = "",
    sha256: str = "",
    args: Optional[Dict[str, Any]] = None,
    status: str = "completed",
    exit_code: int = 0,
    reason: str = "",
    output_preview: str = "",
    error: str = "",
) -> Dict[str, Any]:
    """Append a bounded audit record to skill-history.json."""
    ensure_config_dir()
    history_path = get_skill_history_file()
    try:
        existing_raw = json.loads(history_path.read_text(encoding="utf-8"))
        entries = existing_raw if isinstance(existing_raw, list) else existing_raw.get("entries", [])
        if not isinstance(entries, list):
            entries = []
    except (OSError, ValueError, AttributeError):
        entries = []

    entry = {
        "timestamp": _utc_now_iso(),
        "skill": _sanitize_skill_id(skill_id),
        "scriptPath": str(script_path or ""),
        "sha256": str(sha256 or ""),
        "args": args if isinstance(args, dict) else {},
        "status": str(status or "completed"),
        "exitCode": int(exit_code),
        "reason": str(reason or "")[:300],
        "outputPreview": str(output_preview or "")[:400],
        "error": str(error or "")[:400],
    }
    entries.append(entry)
    entries = entries[-MAX_SKILL_HISTORY_ENTRIES:]

    tmp = history_path.with_suffix(history_path.suffix + ".tmp")
    tmp.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    if sys.platform != "win32":
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
    os.replace(tmp, history_path)
    return entry


def clear_skill_history() -> None:
    history_path = get_skill_history_file()
    try:
        if history_path.exists():
            history_path.unlink()
    except OSError:
        pass
