# Changelog

所有重要变更都会记录在这里。

## [0.2.0] - 2026-06-11

### Added

- 新增 `enter` 参数，`start` / `send` 写入 `text` 时默认自动补换行执行，避免命令停在输入栏里。
- 新增 `clear_line` 参数，`send` 前可先发送 `Ctrl-U` 清空当前输入行，缓解长命令输入错位或残留问题。
- 新增长输入分块写入配置：`input_chunk_chars`、`input_chunk_delay_ms`，减少 PTY/Windows 长命令截断概率。
- 新增 quiet wait 等待策略：`quiet_ms`、`max_wait_ms`，`wait=true` 时等待输出安静或到达上限后返回。
- 新增 `view` 返回字段，给 LLM 一个更适合直接阅读的终端视图。
- 新增单会话自动选择：只有一个活跃终端时，`send` / `read` / `key` / `stop` 可省略 `session_id`。
- 新增更多常用按键：`ctrl_u`、`ctrl_l`，保留 `ctrl_c`、`ctrl_d` 等调试常用组合键。
- 新增命令权限模式配置：`allow_all`、`admin_only`、`blacklist`。
- 新增 `command_blacklist`，在 `blacklist` 模式下拦截命中的启动命令或输入文本。
- 新增 `tmux` 后端，通过真实 detached tmux session 承载终端会话。
- 新增 `backend_mode` 配置：`auto`、`pty`、`tmux`。
- tmux 后端使用 `load-buffer` / `paste-buffer` 写入长文本，使用 `send-keys` 发送 `ctrl_c`、`ctrl_d` 等真实按键。
- tmux 后端使用 `capture-pane` 读取屏幕，适配 `ssh`、`sudo`、`sshpass`、TUI 等更依赖真实 TTY 的交互场景。

### Changed

- Linux/macOS 默认 shell 选择更贴近用户环境：优先 `$SHELL`，再回退到 `bash` / `sh`。
- Linux/macOS 后端启动时尽量设置 `LANG` / `LC_ALL` 为 UTF-8。
- Windows 后端启动时尽量设置 Python UTF-8 相关环境变量，减少中文路径/输出乱码概率。
- README 更新为更贴近实际使用的示例，补充 `cwd`、自动回车、长命令、后台任务、权限模式说明。
- Linux/macOS `auto` 后端模式下优先使用 tmux，找不到 tmux 时回退到原 `ptyprocess` 后端。
- `list` 返回中新增后端类型，方便确认当前会话使用的是 `TmuxSession` 还是 PTY fallback。

### Fixed

- 修复 `send` 文本末尾没有换行时不会执行的问题。
- 改善短等待导致输出尚未出现就返回的问题。
- 改善长命令直接输入时可能被截断或头尾对不上的问题。
- 改善中文路径或中文输出在部分终端环境下显示乱码的问题。

### Notes

- 当前 tmux 后端会创建插件托管的 tmux session；后续可继续扩展为接管已有 tmux socket/session。
- 若 `backend_mode=tmux` 但系统未安装 tmux，启动会话会返回明确错误；若 `backend_mode=auto`，则自动回退到 `ptyprocess`。

## [0.1.0] - 2026-06-10

### Added

- 创建 `astrbot_plugin_terminal_for_koko` 独立插件原型。
- 注册单一 LLM 工具 `terminal`，通过 `action` 分发 `start`、`read`、`send`、`key`、`resize`、`stop`、`list`。
- 新增持久终端会话管理器，支持 `session_id` 追踪终端状态。
- 新增基础安全默认值：默认关闭、管理员限制、群聊默认禁用、最大会话数、空闲 TTL、输出长度限制、审计日志。
- 新增 `cwd` 支持，允许启动终端时进入指定工作目录。
- 新增 Linux/macOS `ptyprocess` 后端与 Windows `pywinpty` 后端。
- 新增 `_conf_schema.json`、`metadata.yaml`、`requirements.txt`、README 和基础包入口文件。

### Notes

- 第一版重点是验证“单入口交互终端工具”的 AstrBot 插件形态，功能以最小可加载、可审计、默认关闭为主。
