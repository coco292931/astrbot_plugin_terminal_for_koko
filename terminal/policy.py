from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "enable", "enabled"}:
            return True
        if lowered in {"0", "false", "no", "off", "disable", "disabled"}:
            return False
    return default


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _safe_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


@dataclass
class TerminalPolicyConfig:
    enabled: bool = False
    admin_only: bool = True
    admin_user_ids: list[str] = field(default_factory=list)
    allow_group: bool = False
    max_sessions: int = 2
    idle_ttl_seconds: int = 600
    max_output_chars: int = 8000
    max_recent_chars: int = 4000
    max_input_chars: int = 4000
    default_command: str = ""
    allowed_commands: list[str] = field(default_factory=list)
    backend_mode: str = "auto"
    auto_start_tmux: bool = True
    sshpass_pipe_fallback: bool = True
    default_cwd: str = ""
    cwd_allowlist: list[str] = field(default_factory=list)
    command_permission_mode: str = "blacklist"
    command_blacklist: list[str] = field(default_factory=list)
    audit_enabled: bool = True
    quiet_ms: int = 200
    max_wait_ms: int = 3000
    input_chunk_chars: int = 128
    input_chunk_delay_ms: int = 10

    @classmethod
    def from_config(cls, raw_config: dict[str, Any]) -> "TerminalPolicyConfig":
        raw = raw_config.get("terminal", raw_config)
        if not isinstance(raw, dict):
            raw = {}
        return cls(
            enabled=_safe_bool(raw.get("enabled"), False),
            admin_only=_safe_bool(raw.get("admin_only"), True),
            admin_user_ids=_safe_list(raw.get("admin_user_ids")),
            allow_group=_safe_bool(raw.get("allow_group"), False),
            max_sessions=_safe_int(raw.get("max_sessions"), 2, 1, 10),
            idle_ttl_seconds=_safe_int(raw.get("idle_ttl_seconds"), 600, 30, 86400),
            max_output_chars=_safe_int(raw.get("max_output_chars"), 8000, 500, 50000),
            max_recent_chars=_safe_int(raw.get("max_recent_chars"), 4000, 500, 50000),
            max_input_chars=_safe_int(raw.get("max_input_chars"), 4000, 100, 20000),
            default_command=str(raw.get("default_command") or "").strip(),
            allowed_commands=_safe_list(raw.get("allowed_commands", _default_allowed_commands())),
            backend_mode=_normalize_backend_mode(raw.get("backend_mode")),
            auto_start_tmux=_safe_bool(raw.get("auto_start_tmux"), True),
            sshpass_pipe_fallback=_safe_bool(raw.get("sshpass_pipe_fallback"), True),
            default_cwd=str(raw.get("default_cwd") or "").strip(),
            cwd_allowlist=_safe_list(raw.get("cwd_allowlist", [])),
            command_permission_mode=_normalize_permission_mode(raw.get("command_permission_mode")),
            command_blacklist=_safe_list(raw.get("command_blacklist", [])),
            audit_enabled=_safe_bool(raw.get("audit_enabled"), True),
            quiet_ms=_safe_int(
                raw.get("quiet_ms", raw.get("settle_delay_ms", raw.get("send_read_delay_ms"))),
                200,
                0,
                5000,
            ),
            max_wait_ms=_safe_int(raw.get("max_wait_ms"), 3000, 0, 60000),
            input_chunk_chars=_safe_int(raw.get("input_chunk_chars"), 128, 8, 4096),
            input_chunk_delay_ms=_safe_int(raw.get("input_chunk_delay_ms"), 10, 0, 1000),
        )

    def authorize_event(self, event: Any) -> tuple[bool, str]:
        if not self.enabled:
            return False, "terminal 工具未启用，请先在插件配置中打开 terminal.enabled"

        if not self.allow_group and _looks_like_group_event(event):
            return False, "terminal 工具默认禁止群聊调用，请在私聊中使用或显式开启 allow_group"

        if self.admin_only and not _looks_like_admin(event, self.admin_user_ids):
            return (
                False,
                "terminal 工具需要管理员权限；无法确认当前调用者为管理员，请配置 admin_user_ids 或关闭 admin_only",
            )

        return True, ""

    def normalize_command(self, command: str) -> tuple[bool, str, str]:
        command = (command or self.default_command or _default_command()).strip()
        if not command:
            return False, "", "缺少 command，且 default_command 为空"

        # allow_all means no command-level allowlist checks; keep event/cwd
        # authorization separate from command text authorization.
        if self.command_permission_mode == "allow_all":
            return True, command, ""

        # sshpass fallback must be decided before allowed_commands, otherwise a
        # non-empty start-command allowlist prevents the pipe backend workaround.
        if self.should_use_pipe_for_sshpass(command):
            return True, command, ""

        executable = _first_executable_name(command)
        allowed = {_normalize_executable_name(item) for item in self.allowed_commands}
        if allowed and executable not in allowed:
            return False, "", f"命令 {executable!r} 不在 allowed_commands 白名单内"
        return True, command, ""

    def resolve_backend_mode(self, command: str, backend: str = "") -> str:
        selected = _normalize_backend_mode(backend) if backend else self.backend_mode
        if selected != "pipe" and self.should_use_pipe_for_sshpass(command):
            return "pipe"
        return selected

    def should_use_pipe_for_sshpass(self, text: str) -> bool:
        return self.sshpass_pipe_fallback and _looks_like_sshpass(text)

    def authorize_command_text(self, event: Any, text: str) -> tuple[bool, str]:
        text = (text or "").strip()
        if not text:
            return True, ""

        if self.command_permission_mode == "allow_all":
            return True, ""
        if self.command_permission_mode == "admin_only":
            if _looks_like_admin(event, self.admin_user_ids):
                return True, ""
            return False, "当前命令权限模式为 admin_only，仅管理员命令放行"
        if self.command_permission_mode == "blacklist" and _contains_blacklisted_command(
            text, self.command_blacklist
        ):
            return False, "命令命中 command_blacklist，已拒绝执行"
        return True, ""

    def normalize_cwd(self, cwd: str) -> tuple[bool, str, str]:
        cwd = (cwd or self.default_cwd or "").strip()
        if not cwd:
            return True, "", ""

        try:
            target = Path(cwd).expanduser().resolve(strict=False)
        except Exception as exc:
            return False, "", f"cwd 无法解析: {exc}"

        if not self.cwd_allowlist:
            return True, str(target), ""

        for root_text in self.cwd_allowlist:
            try:
                root = Path(root_text).expanduser().resolve(strict=False)
            except Exception:
                continue
            if _path_is_relative_to(target, root):
                return True, str(target), ""

        return False, "", f"cwd {target} 不在 cwd_allowlist 内"


def _default_command() -> str:
    if sys.platform.startswith("win"):
        return "powershell"
    shell = os.environ.get("SHELL", "").strip()
    if shell:
        return shell
    if shutil.which("bash"):
        return "bash"
    return "sh"


def _default_allowed_commands() -> list[str]:
    return []


def _normalize_permission_mode(value: Any) -> str:
    mode = str(value or "blacklist").strip().lower()
    if mode in {"allow_all", "admin_only", "blacklist"}:
        return mode
    return "blacklist"


def _normalize_backend_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    if mode in {"auto", "pty", "tmux", "pipe"}:
        return mode
    return "auto"


def _contains_blacklisted_command(text: str, blacklist: list[str]) -> bool:
    lowered = text.lower()
    return any(item.lower() in lowered for item in blacklist if item)


def _looks_like_sshpass(command: str) -> bool:
    lowered = (command or "").lower()
    return "sshpass" in lowered


def _first_executable_name(command: str) -> str:
    try:
        parts = shlex.split(command, posix=False)
    except Exception:
        parts = command.split()
    first = parts[0] if parts else command
    return _normalize_executable_name(first)


def _normalize_executable_name(value: str) -> str:
    name = Path(str(value).strip().strip('"')).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def _path_is_relative_to(target: Path, root: Path) -> bool:
    try:
        target_norm = os.path.normcase(str(target))
        root_norm = os.path.normcase(str(root))
        common = os.path.commonpath([target_norm, root_norm])
        return common == root_norm
    except Exception:
        return False


def _looks_like_group_event(event: Any) -> bool:
    for attr in ("get_message_type", "message_type", "type"):
        try:
            value = getattr(event, attr)
            value = value() if callable(value) else value
        except Exception:
            continue
        text = str(value).lower()
        if "group" in text or "群" in text:
            return True

    origin = str(getattr(event, "unified_msg_origin", "") or "").lower()
    return "group" in origin or "群" in origin


def _looks_like_admin(event: Any, admin_user_ids: list[str]) -> bool:
    sender_id = _extract_sender_id(event)
    if sender_id and sender_id in {str(item) for item in admin_user_ids}:
        return True

    for attr in ("is_admin", "is_super_admin", "is_at_admin"):
        try:
            value = getattr(event, attr)
            value = value() if callable(value) else value
        except Exception:
            continue
        if value is True:
            return True

    try:
        sender = event.get_sender() if hasattr(event, "get_sender") else None
    except Exception:
        sender = None
    if sender is not None:
        for attr in ("is_admin", "admin", "role", "permission"):
            value = getattr(sender, attr, None)
            value = value() if callable(value) else value
            if value is True or str(value).lower() in {"admin", "owner", "superuser"}:
                return True

    return False


def _extract_sender_id(event: Any) -> str:
    for attr in ("get_sender_id", "get_user_id", "sender_id", "user_id"):
        try:
            value = getattr(event, attr)
            value = value() if callable(value) else value
        except Exception:
            continue
        if value:
            return str(value)
    return ""
