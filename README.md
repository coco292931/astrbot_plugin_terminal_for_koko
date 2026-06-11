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
    backend: str = "",
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
  "backend": "tmux",
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

临时覆盖后端：

```json
{
  "action": "start",
  "backend": "pipe",
  "command": "sshpass -p 'password' ssh -tt user@example.com"
}
```

`backend` 支持 `auto`、`pty`、`tmux`、`pipe`。`pipe` 不分配终端 PTY，适合先验证 `sshpass`、一次性 SSH 命令或其它自己管理 PTY 的命令。

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
  "key": "ctrl+c"
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

组合键直接写在 `key` 字符串里：

```text
ctrl+c
ctrl_c
shift+tab
alt+enter
ctrl+shift+left
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
  "backend": "TmuxSession",
  "backend_mode": "tmux",
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

终端后端：

```text
auto  Linux/macOS 优先使用 tmux，找不到 tmux 时回退到 pty；Windows 使用 pywinpty
pty   强制使用插件自建 PTY
tmux  强制使用 tmux 后端
pipe  普通管道执行命令，不分配 PTY
```

可以通过全局配置 `backend_mode` 指定默认后端，也可以在单次 `start` 调用里传 `backend` 临时覆盖。插件启用后默认会尝试自动创建一个 tmux 会话；如果不想加载插件时预开终端，把 `auto_start_tmux` 设为 `false`。

如果主要使用 `ssh`、`sudo` 或其它依赖真实 TTY 的交互式程序，建议安装 `tmux` 并使用 `backend_mode=tmux` 或 `backend=tmux`。tmux 后端会创建一个真实的 detached tmux session，通过 `send-keys` / `paste-buffer` 写入输入，通过 `capture-pane` 读取屏幕，比自建 PTY 更接近手动打开终端的行为。

`sshpass` 是一个例外：它自己会处理密码提示，并可能创建/依赖自己的 PTY。为了避免 tmux/PTY 再套一层导致登录被拦截，默认开启 `sshpass_pipe_fallback`。当 `start.command` 包含 `sshpass` 时，插件会自动改用 `pipe` 后端；如果在已有 tmux/pty 会话里 `send` 了 `sshpass ...`，插件也会改为新建一个 `pipe` 会话执行这条命令。对于可解析的 `sshpass -p/-f/-e ... ssh ...` 写法，插件会移除 `sshpass` 包裹，直接启动内层 SSH 命令，并在单层 PTY 中自动回答密码提示。若你确认某个环境里必须强制 tmux/pty，可以关闭这个开关后再显式传 `backend`。

命令权限模式：

```text
allow_all   放行所有命令，不审核命令内容
admin_only  只放行管理员命令
blacklist   不放行命中 command_blacklist 的命令
```

`allowed_commands` 是可选的额外启动命令白名单，默认留空。`command_permission_mode=allow_all` 时不会再套用这个白名单；`sshpass_pipe_fallback=true` 时，`sshpass` 也会先放行到 pipe fallback，避免在白名单阶段被挡住。其它模式下，非空 `allowed_commands` 仍会限制 `start.command` 的第一个可执行文件名。

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

- Linux/macOS 默认优先使用 `tmux`，缺失时回退到 `ptyprocess`。
- Windows 后端使用 `pywinpty` / ConPTY。
- `pipe` 后端默认使用普通 stdin/stdout 管道，不额外分配 PTY；可解析的 `sshpass` 命令会改用单层 PTY prompt 后端自动输入密码。
- Linux 后端会尽量设置 `LANG/LC_ALL=C.UTF-8`。
- Windows 后端会尽量设置 Python UTF-8 环境变量。

tmux 后端可以改善 `ssh`、`sudo` 密码输入、全屏 TUI 等复杂交互。`sshpass` 默认改走 `pipe` fallback；可解析的 `sshpass` 命令会被转换为内层 SSH 命令并由插件回答密码提示，以规避双重 PTY。tmux 仍然依赖宿主机安装 `tmux`，并且当前实现是创建插件托管的 tmux session；后续还可以继续扩展为接管已有 tmux socket/session。
