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
    ) -> dict[str, Any]:
        action = (action or "").strip().lower()
        await self._cleanup_expired()

        ok, reason = self.policy.authorize_event(event)
        if not ok:
            return self._result(False, action, session_id, message=reason)

        if action == "start":
            result = await self._start(command, cwd, rows, cols, text, wait)
        elif action == "list":
            result = self._list(action)
        elif action in {"read", "send", "key", "resize", "stop"}:
            result = await self._with_session(
                action, session_id, text, key, rows, cols, wait
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
        self, command: str, cwd: str, rows: int, cols: int, text: str, wait: bool
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
            )
        except Exception as exc:
            logger.warning(f"[terminal_for_koko] start failed: {exc}")
            return self._result(False, "start", "", message=f"启动终端失败: {exc}")

        self.sessions[session_id] = session
        if text:
            if len(text) > self.policy.max_input_chars:
                session.close()
                self.sessions.pop(session_id, None)
                return self._result(
                    False,
                    "start",
                    session_id,
                    message=f"text 超过 max_input_chars={self.policy.max_input_chars}",
                )
            session.write(text)
        if wait:
            await self._wait_after_input()
        return self._snapshot_result("start", session_id, session, "terminal started")

    async def _with_session(
        self,
        action: str,
        session_id: str,
        text: str,
        key: str,
        rows: int,
        cols: int,
        wait: bool,
    ) -> dict[str, Any]:
        session_id = (session_id or "").strip()
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
                if len(text or "") > self.policy.max_input_chars:
                    return self._result(
                        False,
                        action,
                        session_id,
                        message=f"text 超过 max_input_chars={self.policy.max_input_chars}",
                    )
                session.write(text or "")
                if wait:
                    await self._wait_after_input()
                return self._snapshot_result(action, session_id, session)
            if action == "key":
                session.send_key(key)
                if wait:
                    await self._wait_after_input()
                return self._snapshot_result(action, session_id, session)
            if action == "resize":
                session.resize(max(1, min(int(rows or 24), 80)), max(20, min(int(cols or 100), 240)))
                return self._snapshot_result(action, session_id, session, "terminal resized")
            if action == "stop":
                session.close()
                self.sessions.pop(session_id, None)
                return self._result(True, action, session_id, alive=False, message="terminal stopped")
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
            "truncated": False,
            "sessions": items,
            "message": "",
        }

    async def _wait_after_input(self) -> None:
        delay = max(0, self.policy.settle_delay_ms) / 1000
        if delay > 0:
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
            "truncated": False,
            "message": message,
        }

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
                "sender_id": _extract_event_value(event, ("get_sender_id", "get_user_id", "sender_id", "user_id")),
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
