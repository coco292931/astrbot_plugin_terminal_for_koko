from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .terminal.manager import TerminalManager
from .terminal.policy import TerminalPolicyConfig


@register(
    "astrbot_plugin_terminal_for_koko",
    "coco & gpt",
    "koko 交互终端",
    "0.3.0",
    "https://github.com/coco292931/astrbot_plugin_terminal_for_koko",
)
class TerminalForKokoPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config if isinstance(config, dict) else {}
        policy = TerminalPolicyConfig.from_config(self.config)
        audit_path = Path(__file__).with_name("data") / "audit.jsonl"
        self.terminal_manager = TerminalManager(policy=policy, audit_path=audit_path)
        logger.info(
            "[terminal_for_koko] loaded: "
            f"enabled={policy.enabled}, admin_only={policy.admin_only}, "
            f"allow_group={policy.allow_group}, max_sessions={policy.max_sessions}, "
            f"backend_mode={policy.backend_mode}, "
            f"auto_start_tmux={policy.auto_start_tmux}, "
            f"sshpass_pipe_fallback={policy.sshpass_pipe_fallback}, "
            f"command_permission_mode={policy.command_permission_mode}"
        )
        auto_start_result = self.terminal_manager.auto_start_tmux()
        if auto_start_result:
            if auto_start_result.get("ok"):
                logger.info(
                    "[terminal_for_koko] auto-started tmux session: "
                    f"{auto_start_result.get('session_id', '')}"
                )
            else:
                logger.warning(
                    "[terminal_for_koko] tmux auto-start skipped: "
                    f"{auto_start_result.get('message', '')}"
                )

    @filter.llm_tool(name="terminal")
    async def terminal(
        self,
        event: AstrMessageEvent,
        action: str,
        session_id: str = "",
        text: str = "",
        key: str = "",
        command: str = "",
        cwd: str = "",
        backend: str = "",
        rows: int = 24,
        cols: int = 100,
        wait: bool = True,
        enter: bool = True,
        clear_line: bool = False,
    ) -> dict[str, Any]:
        """交互式终端统一入口。

        这是一个持久终端会话工具，内部通过 action 分发动作。默认配置下该工具关闭，
        启用后也建议只允许管理员私聊使用。普通命令执行请优先使用更窄权限的工具。

        Args:
            action(string): 动作，支持 start/read/send/key/resize/stop/list
            session_id(string): 会话 ID；只有一个活跃会话时，send/read/key/stop 可省略
            text(string): start/send 动作用的输入文本；enter=true 时会自动补换行执行
            key(string): key 动作用的特殊按键，如 enter、ctrl+c、shift+tab、alt+enter
            command(string): start 动作用的终端命令，留空使用配置默认值
            cwd(string): start 动作用的工作目录；若配置了 cwd_allowlist，则必须位于其中
            backend(string): start 动作可选后端覆盖，支持 auto/pty/tmux/pipe
            rows(int): start/resize 动作用的终端行数
            cols(int): start/resize 动作用的终端列数
            wait(bool): start/send/key 后是否等待输出安静后再读屏
            enter(bool): start/send 写入 text 后是否自动补换行执行
            clear_line(bool): send 前是否先发送 Ctrl-U 清空当前输入行
        """
        try:
            return await self.terminal_manager.handle(
                event=event,
                action=action,
                session_id=session_id,
                text=text,
                key=key,
                command=command,
                cwd=cwd,
                backend=backend,
                rows=rows,
                cols=cols,
                wait=wait,
                enter=enter,
                clear_line=clear_line,
            )
        except Exception as exc:
            logger.warning(f"[terminal_for_koko] terminal tool failed: {exc}")
            return {
                "ok": False,
                "action": action,
                "session_id": session_id,
                "alive": False,
                "seq": 0,
                "screen": "",
                "recent_output": "",
                "view": f"[terminal error] {exc}",
                "truncated": False,
                "message": f"terminal tool failed: {exc}",
            }

    async def terminate(self):
        await self.terminal_manager.stop_all()
