from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .policy import TerminalPolicyConfig
from .session import TerminalSession


class TerminalManager:
    def __init__(self, policy: TerminalPolicyConfig, audit_path: Path):
        self.policy = policy
        self.audit_path = audit_path
        self.sessions: dict[str, TerminalSession] = {}

    async def handle(
        self,
        event: Any,
        action: str,
        session_id: str = "",
        text: str = "",
        key: str = "",
        command: str = "",
        cwd: str = "",
        rows: int = 24,
        cols: int = 100,
        wait: bool = True,
        enter: bool = True,
        clear_line: bool = False,
    ) -> dict[str, Any]:
        action = (action or "").strip().lower()
        await self._cleanup_expired()

        ok, reason = self.policy.authorize_event(event)
        if not ok:
            return self._result(False, action, session_id, message=reason)

        if action == "start":
            result = await self._start(
                event, command, cwd, rows, cols, text, wait, enter
            )
        elif action == "list":
            result = self._list(action)
        elif action in {"read", "send", "key", "resize", "stop"}:
            result = await self._with_session(
                event,
                action,
                session_id,
                text,
                key,
                rows,
                cols,
                wait,
                enter,
                clear_line,
            )
        else:
            result = self._result(
                False,
                action,
                session_id,
                message="未知 action，支持 start/read/send/key/resize/stop/list",
            )

        self._audit(event, action, session_id, text, result)
        return result

    async def stop_all(self) -> None:
        for session in list(self.sessions.values()):
            try:
                session.close()
            except Exception:
                pass
        self.sessions.clear()

    async def _start(
        self,
        event: Any,
        command: str,
        cwd: str,
        rows: int,
        cols: int,
        text: str,
        wait: bool,
        enter: bool,
    ) -> dict[str, Any]:
        if len(self.sessions) >= self.policy.max_sessions:
            return self._result(
                False,
                "start",
                "",
                message=f"已达到最大会话数 {self.policy.max_sessions}",
            )

        ok, normalized_command, message = self.policy.normalize_command(command)
        if not ok:
            return self._result(False, "start", "", message=message)
        ok, message = self.policy.authorize_command_text(event, normalized_command)
        if not ok:
            return self._result(False, "start", "", message=message)

        ok, normalized_cwd, message = self.policy.normalize_cwd(cwd)
        if not ok:
            return self._result(False, "start", "", message=message)

        rows = max(1, min(int(rows or 24), 80))
        cols = max(20, min(int(cols or 100), 240))
        session_id = self._new_session_id()
        try:
            session = TerminalSession(
                session_id=session_id,
                command=normalized_command,
                cwd=normalized_cwd,
                rows=rows,
                cols=cols,
                max_history_chars=max(self.policy.max_output_chars * 3, 20000),
                backend_mode=self.policy.backend_mode,
            )
        except Exception as exc:
            logger.warning(f"[terminal_for_koko] start failed: {exc}")
            return self._result(False, "start", "", message=f"启动终端失败: {exc}")

        self.sessions[session_id] = session
        if text:
            prepared_text = self._prepare_text(text, enter)
            ok, message = self.policy.authorize_command_text(event, prepared_text)
            if not ok:
                session.close()
                self.sessions.pop(session_id, None)
                return self._result(False, "start", session_id, message=message)
            if len(prepared_text) > self.policy.max_input_chars:
                session.close()
                self.sessions.pop(session_id, None)
                return self._result(
                    False,
                    "start",
                    session_id,
                    message=f"text 超过 max_input_chars={self.policy.max_input_chars}",
                )
            await self._write_text(session, prepared_text)
        if wait:
            await self._wait_after_input(session)
        return self._snapshot_result("start", session_id, session, "terminal started")

    async def _with_session(
        self,
        event: Any,
        action: str,
        session_id: str,
        text: str,
        key: str,
        rows: int,
        cols: int,
        wait: bool,
        enter: bool,
        clear_line: bool,
    ) -> dict[str, Any]:
        session_id = self._resolve_session_id((session_id or "").strip())
        session = self.sessions.get(session_id)
        if not session:
            return self._result(False, action, session_id, message="会话不存在")
        if not session.alive and action != "stop":
            self.sessions.pop(session_id, None)
            return self._result(False, action, session_id, message="会话已结束")

        try:
            if action == "read":
                return self._snapshot_result(action, session_id, session)
            if action == "send":
                prepared_text = self._prepare_text(text or "", enter, clear_line)
                ok, message = self.policy.authorize_command_text(event, prepared_text)
                if not ok:
                    return self._result(False, action, session_id, message=message)
                if len(prepared_text) > self.policy.max_input_chars:
                    return self._result(
                        False,
                        action,
                        session_id,
                        message=f"text 超过 max_input_chars={self.policy.max_input_chars}",
                    )
                await self._write_text(session, prepared_text)
                if wait:
                    await self._wait_after_input(session)
                return self._snapshot_result(action, session_id, session)
            if action == "key":
                session.send_key(key)
                if wait:
                    await self._wait_after_input(session)
                return self._snapshot_result(action, session_id, session)
            if action == "resize":
                session.resize(
                    max(1, min(int(rows or 24), 80)),
                    max(20, min(int(cols or 100), 240)),
                )
                return self._snapshot_result(
                    action, session_id, session, "terminal resized"
                )
            if action == "stop":
                session.close()
                self.sessions.pop(session_id, None)
                return self._result(
                    True, action, session_id, alive=False, message="terminal stopped"
                )
        except Exception as exc:
            logger.warning(f"[terminal_for_koko] action {action} failed: {exc}")
            return self._result(False, action, session_id, message=str(exc))

        return self._result(False, action, session_id, message="未处理的 action")

    def _list(self, action: str) -> dict[str, Any]:
        items = []
        now = time.time()
        for session_id, session in self.sessions.items():
            items.append(
                {
                    "session_id": session_id,
                    "alive": session.alive,
                    "command": session.command,
                    "backend": session.backend_name,
                    "cwd": session.cwd,
                    "idle_seconds": int(now - session.updated_at),
                    "rows": session.rows,
                    "cols": session.cols,
                }
            )
        return {
            "ok": True,
            "action": action,
            "session_id": "",
            "alive": True,
            "seq": 0,
            "screen": "",
            "recent_output": "",
            "view": self._make_view("", True, 0, json.dumps(items, ensure_ascii=False)),
            "truncated": False,
            "sessions": items,
            "message": "",
        }

    async def _wait_after_input(self, session: TerminalSession) -> None:
        max_wait = max(0, self.policy.max_wait_ms) / 1000
        quiet = max(0, self.policy.quiet_ms) / 1000
        if max_wait <= 0:
            return

        started = time.monotonic()
        last_change = started
        last_seq = session.output_seq
        seen_output = False
        while time.monotonic() - started < max_wait:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            seq = session.output_seq
            if seq != last_seq:
                seen_output = True
                last_seq = seq
                last_change = now
            if seen_output and now - last_change >= quiet:
                return

    async def _write_text(self, session: TerminalSession, text: str) -> None:
        chunk_size = max(1, self.policy.input_chunk_chars)
        delay = max(0, self.policy.input_chunk_delay_ms) / 1000
        for start in range(0, len(text), chunk_size):
            session.write(text[start : start + chunk_size])
            if delay > 0 and start + chunk_size < len(text):
                await asyncio.sleep(delay)

    async def _cleanup_expired(self) -> None:
        for session_id, session in list(self.sessions.items()):
            if not session.alive or session.is_idle_expired(self.policy.idle_ttl_seconds):
                try:
                    session.close()
                except Exception:
                    pass
                self.sessions.pop(session_id, None)

    def _snapshot_result(
        self,
        action: str,
        session_id: str,
        session: TerminalSession,
        message: str = "",
    ) -> dict[str, Any]:
        snapshot = session.snapshot(
            screen_limit=self.policy.max_output_chars,
            recent_limit=self.policy.max_recent_chars,
        )
        return {
            "ok": True,
            "action": action,
            "session_id": session_id,
            "alive": session.alive,
            "seq": snapshot.seq,
            "screen": snapshot.screen,
            "recent_output": snapshot.recent_output,
            "view": self._make_view(
                session_id,
                session.alive,
                snapshot.seq,
                snapshot.recent_output or snapshot.screen,
            ),
            "truncated": snapshot.truncated,
            "message": message,
        }

    def _result(
        self,
        ok: bool,
        action: str,
        session_id: str,
        alive: bool = False,
        message: str = "",
    ) -> dict[str, Any]:
        return {
            "ok": ok,
            "action": action,
            "session_id": session_id,
            "alive": alive,
            "seq": 0,
            "screen": "",
            "recent_output": "",
            "view": f"[terminal {action or 'unknown'}] {message}".strip(),
            "truncated": False,
            "message": message,
        }

    def _prepare_text(self, text: str, enter: bool, clear_line: bool = False) -> str:
        prepared = text or ""
        if clear_line:
            prepared = "\x15" + prepared
        if enter and prepared and not prepared.endswith(("\n", "\r")):
            prepared += "\n"
        return prepared

    def _resolve_session_id(self, session_id: str) -> str:
        if session_id:
            return session_id
        alive = [sid for sid, session in self.sessions.items() if session.alive]
        if len(alive) == 1:
            return alive[0]
        return session_id

    def _make_view(self, session_id: str, alive: bool, seq: int, text: str) -> str:
        state = "alive" if alive else "closed"
        label = session_id or "terminal"
        return f"[{label} {state} seq={seq}]\n{text or ''}"

    def _new_session_id(self) -> str:
        while True:
            session_id = f"term_{secrets.token_hex(4)}"
            if session_id not in self.sessions:
                return session_id

    def _audit(
        self,
        event: Any,
        action: str,
        session_id: str,
        text: str,
        result: dict[str, Any],
    ) -> None:
        if not self.policy.audit_enabled:
            return
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "action": action,
                "session_id": session_id or result.get("session_id", ""),
                "ok": result.get("ok", False),
                "alive": result.get("alive", False),
                "sender_id": _extract_event_value(
                    event,
                    ("get_sender_id", "get_user_id", "sender_id", "user_id"),
                ),
                "origin": str(getattr(event, "unified_msg_origin", "") or ""),
                "text_len": len(text or ""),
                "text_preview": _preview(text),
                "screen_len": len(str(result.get("screen", "") or "")),
                "recent_len": len(str(result.get("recent_output", "") or "")),
                "message": str(result.get("message", "") or ""),
            }
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug(f"[terminal_for_koko] audit failed: {exc}")


def _preview(text: str, limit: int = 120) -> str:
    text = (text or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _extract_event_value(event: Any, names: tuple[str, ...]) -> str:
    for name in names:
        try:
            value = getattr(event, name)
            value = value() if callable(value) else value
        except Exception:
            continue
        if value:
            return str(value)
    return ""
