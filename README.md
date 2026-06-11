# astrbot_plugin_terminal_for_koko

给 AstrBot LLM 使用的交互式终端插件。核心设计是只暴露一个 LLM 工具 `terminal`，内部通过 `action` 参数分发启动、读取、输入、按键、缩放、关闭等动作，避免工具列表被一排终端工具占满。

## 统一入口

```python
async def terminal(
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
) -> dict:
    ...
```

支持的 `action`：

```text
start
read
send
key
resize
stop
list
```

## 使用方式

启动会话：

```json
{
  "action": "start",
  "rows": 24,
  "cols": 100
}
```

启动会话、进入指定目录并立即执行第一条命令：

```json
{
  "action": "start",
  "command": "bash",
  "cwd": "/home/koko/project",
  "text": "python --version"
}
```

`enter=true` 是默认值，所以 `text` 末尾没有 `\n` 也会自动补换行执行。

发送文本：

```json
{
  "action": "send",
  "text": "python --version"
}
```

如果当前只有一个活跃终端，可以省略 `session_id`。`send` 默认会自动补换行执行；`wait=true` 时插件会等待终端输出安静下来，或者达到 `max_wait_ms` 后返回。

长命令输入前清空当前行：

```json
{
  "action": "send",
  "text": "bash ./very_long_script_name_with_args.sh --flag value",
  "clear_line": true
}
```

后台长任务不等待：

```json
{
  "action": "send",
  "text": "npm run dev",
  "wait": false
}
```

后续读取屏幕：

```json
{
  "action": "read"
}
```

发送特殊按键：

```json
{
  "action": "key",
  "key": "ctrl_c"
}
```

常用按键包括：

```text
enter
tab
escape
backspace
ctrl_c
ctrl_d
ctrl_u
ctrl_l
up
down
left
right
```

关闭会话：

```json
{
  "action": "stop"
}
```

## 返回格式

所有动作返回统一结构：

```json
{
  "ok": true,
  "action": "send",
  "session_id": "term_xxx",
  "alive": true,
  "seq": 12,
  "screen": "$ python --version\nPython 3.12.3",
  "recent_output": "Python 3.12.3",
  "view": "[term_xxx alive seq=12]\nPython 3.12.3",
  "truncated": false,
  "message": ""
}
```

模型主要看：

- `view`: 最适合直接阅读的终端视图。
- `session_id`: 多会话时继续操作这个终端。
- `alive`: 会话是否还活着。

## 配置重点

安全默认值：

- 默认关闭。
- 仅允许管理员私聊使用。
- 群聊默认禁用。
- 限制最大会话数。
- 限制空闲 TTL。
- 限制输出最大长度。
- 可选限制工作目录 allowlist。
- 对 action、输入摘要、输出长度写审计日志。

命令权限模式：

```text
allow_all   放行所有命令，不审核命令内容
admin_only  只放行管理员命令
blacklist   不放行命中 command_blacklist 的命令
```

等待策略：

```text
quiet_ms     输出停止变化多久后返回
max_wait_ms  最多等待多久，避免一直卡住
```

输入策略：

```text
input_chunk_chars      长输入分块大小
input_chunk_delay_ms   分块写入间隔
clear_line             单次 send 前先发 Ctrl-U 清空当前行
```

## 后端说明

当前实现路线：

- Linux/macOS 后端使用 `ptyprocess`。
- Windows 后端使用 `pywinpty` / ConPTY。
- Linux 后端会尽量设置 `LANG/LC_ALL=C.UTF-8`。
- Windows 后端会尽量设置 Python UTF-8 环境变量。

如果遇到 `sshpass`、`sudo` 密码输入、全屏 TUI 等复杂交互，直接接管 `screen/tmux` socket 会比自造 PTY 更稳；这可以作为下一阶段后端。
