# astrbot_plugin_terminal_for_koko

一个给 AstrBot LLM 使用的交互式终端插件原型。

核心设计：对外只暴露一个 LLM 工具 `terminal`，内部通过 `action` 参数分发启动、读取、输入、按键、缩放、关闭等动作。这样不会在 AstrBot 工具列表里塞一排终端相关工具，也更方便统一做权限、审计和限流。

## 统一入口

建议对外入口保持为：

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
) -> str:
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

启动会话并立即发送第一条输入：

```json
{
  "action": "start",
  "command": "bash",
  "text": "python --version\n"
}
```

发送文本：

```json
{
  "action": "send",
  "session_id": "term_xxx",
  "text": "python --version\n"
}
```

`send` 会自动按插件配置等待一小段时间，并直接返回当前屏幕。LLM 不需要自己决定等待多久。

如果只是把文本送进去，不想等待输出：

```json
{
  "action": "send",
  "session_id": "term_xxx",
  "text": "npm run dev\n",
  "wait": false
}
```

后续再读取屏幕：

```json
{
  "action": "read",
  "session_id": "term_xxx"
}
```

发送特殊按键：

```json
{
  "action": "key",
  "session_id": "term_xxx",
  "key": "ctrl_c"
}
```

`key` 也会在 `wait=true` 时自动返回一次当前屏幕。

关闭会话：

```json
{
  "action": "stop",
  "session_id": "term_xxx"
}
```

## 返回格式

所有动作尽量返回统一 JSON 字符串：

```json
{
  "ok": true,
  "action": "read",
  "session_id": "term_xxx",
  "alive": true,
  "seq": 12,
  "screen": "PS C:\\Users\\admin> ",
  "recent_output": "...",
  "truncated": false,
  "message": ""
}
```

实际使用时，模型主要看三项即可：

- `session_id`: 后续继续操作这个终端。
- `screen`: 当前可见终端内容。
- `alive`: 会话是否还活着。

## 安全默认值

第一版默认保守：

- 默认关闭。
- 仅允许管理员私聊使用。
- 群聊默认禁用。
- 限制最大会话数。
- 限制空闲 TTL。
- 限制输出最大长度。
- 可选限制工作目录 allowlist。
- 对 action、输入摘要、输出长度写审计日志。

## 推荐实现路线

当前实现路线：

- Linux/macOS 后端使用 `ptyprocess`。
- Windows 后端使用 `pywinpty` / ConPTY。
- 第一版可以先返回最近输出；后续再接 `pyte` 维护真实屏幕缓冲。

内部结构建议：

```text
astrbot_plugin_terminal_for_koko/
  main.py
  _conf_schema.json
  metadata.yaml
  requirements.txt
  terminal/
    __init__.py
    manager.py
    session.py
    policy.py
    screen_buffer.py
    backends/
      __init__.py
      winpty_backend.py
      pty_backend.py
```
