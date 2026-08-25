<div align="center">

# ⚡ CodeYX

**终端原生 AI 编程智能体 —— 从零开始用 Python 构建。**

通过结构化的工具调用循环自主读码、写码、执行代码，
五层权限模型确保每个动作可审计、可拦截。

[![CI](https://github.com/EthyleneC2H4/CodeYX/actions/workflows/test.yml/badge.svg)](https://github.com/EthyleneC2H4/CodeYX/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-647%20passing-brightgreen.svg)](#运行测试)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](#-license)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](pyproject.toml)

[English](README.md) · **简体中文**

</div>

---

## 为什么是 CodeYX？

大多数 AI 编程助手是托管服务，或是对专有技术栈的薄封装。
CodeYX 是一个**从零实现、完全本地的 Agent 运行时**：ReAct 循环、工具调度器、
权限引擎、上下文压缩器、记忆系统与多智能体编排全部直接构建在 `asyncio` 之上——
没有框架锁定，每一层都可检视。

```text
❯ /plan 给 API 加上 JWT 认证
  ✻ 规划中… 阅读 routes.py、auth.py、tests/
  ── 计划已写入 .codeyx/plans/bold-spark-0825-1432.md
  ── [1] YOLO  [2] 手动执行  [3] 反馈
❯ 1
  ✻ 写入 src/auth/jwt.py … ✓
  ✻ 编辑 src/api/routes.py … ✓
  ✻ Bash: pytest tests/auth -q … 24 passed
```

## ✨ 特性

| | |
|---|---|
| 🌐 **多协议 LLM 支持** | Anthropic Messages、OpenAI Responses、OpenAI 兼容 Chat Completions（vLLM、Ollama 等）与 DeepSeek——统一收敛到一个 `stream()` 接口。 |
| 🧰 **15 个内置工具** | ReadFile / WriteFile / EditFile / Bash / Glob / Grep / Agent（子代理）/ Team* / Enter- & ExitWorktree 等，可通过 MCP 服务器与技能扩展。 |
| 🛡️ **五层权限模型** | Plan 模式特例 → 安全命令白名单 → 危险命令黑名单 → 路径沙箱 → 规则引擎 → 模式矩阵 → 人工确认。任何一层都可以拒绝；首个拒绝即短路。 |
| 🗜️ **两层上下文压缩** | 先按轮次对工具结果做预算控制并落盘；接近窗口上限再触发 LLM 摘要——压缩后通过恢复快照重新挂载文件读取。预算决策跨轮冻结，保证 prompt-cache 前缀逐字节稳定。 |
| 🧠 **跨会话记忆** | LLM 抽取器每 5 轮运行一次，把记忆分类落盘（偏好 / 反馈 / 项目知识 / 引用）；新会话自动继承。 |
| 🔌 **MCP 集成** | 通过 Model Context Protocol 接入 stdio + Streamable HTTP 服务器。工具延迟加载——只通告名称，schema 按需发现（多服务器场景初始 token 节省约 85%）。显式的连接/调用超时，挂死的服务器不会拖住循环。 |
| 🧩 **技能系统** | 可复用的提示词模板，Markdown + YAML frontmatter 打包，经 `/skill` 调用。fork 型技能在隔离代理中运行，权限从父级继承。 |
| 👥 **子代理与团队** | 带过滤工具集的隔离代理、自定义模型、可选 git worktree 隔离。团队通过原子文件系统邮箱协作，由 Coordinator 编排——进程内、tmux 或 iTerm2 窗格中运行真实的 `codeyx` 进程。 |
| 🪝 **Hooks 引擎** | 在 15 个生命周期事件注入 shell / HTTP / 提示词动作，支持条件匹配（`==`、`!=`、正则、fnmatch）、shell 转义插值、强制超时与 pre-tool 拒绝。 |

## 🚀 快速开始

### 环境要求

- Python **3.11+**
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip
- 至少一家提供商的 API key

### 安装与启动

```bash
git clone https://github.com/EthyleneC2H4/CodeYX.git
cd CodeYX
uv sync

cp config.example.yaml .codeyx/config.yaml   # 填入你的 key
uv run codeyx
```

无界面单次执行（也是窗格 teammate 的启动方式）：

```bash
uv run codeyx -p "总结这个仓库" --work-dir /path/to/project

# 以指定身份加入团队
uv run codeyx --team my-team --agent-name worker-1 --prompt "开始任务"
```

<details>
<summary><b>配置</b>（点击展开）</summary>

配置按 user → project → local 三级合并；所有字段支持 `${VAR}` 环境变量插值。

```yaml
providers:
  - name: anthropic
    protocol: anthropic          # anthropic | openai | openai-compat | deepseek
    base_url: https://api.anthropic.com
    model: claude-sonnet-4-5
    api_key: ${ANTHROPIC_API_KEY}
    thinking: false              # 扩展思考 + 更大输出预算
    context_window: 200000

# permission_mode: default       # default | acceptEdits | plan | bypassPermissions | dontAsk

mcp_servers: []
  # - name: filesystem
  #   command: npx
  #   args: ["-y", "@modelcontextprotocol/server-filesystem", "."]

worktree:
  symlink_directories: ["node_modules", ".venv", "vendor"]
  stale_cleanup_interval: 3600
```

完整 schema 见 [`config.example.yaml`](config.example.yaml)

</details>

### 运行测试

```bash
uv run pytest                    # 全量 —— 647 个测试
uv run ruff check codeyx tests   # lint 门禁
```

## 🏗️ 架构

五个层级，各自独立可检视：

```text
┌─────────────────────────────────────────────────────┐
│  展示层          Textual TUI · 斜杠命令              │
├─────────────────────────────────────────────────────┤
│  引擎层          ReAct 循环 · 会话管理               │
├─────────────────────────────────────────────────────┤
│  工具层          文件 I/O · Bash · 搜索 · MCP        │
│                 子代理 · 团队                        │
├─────────────────────────────────────────────────────┤
│  记忆层          自动记忆 · 会话持久化               │
├─────────────────────────────────────────────────────┤
│  安全层          五层权限 · 沙箱 · 规则 · 钩子       │
└─────────────────────────────────────────────────────┘
```

```text
codeyx/
├── agent.py          # 核心 ReAct 循环（异步生成器，类型化 AgentEvent）
├── app.py            # Textual TUI 应用
├── client.py         # LLM 抽象（4 种协议）
├── conversation.py   # 消息模型 + 多协议序列化
├── prompts.py        # 按优先级组装系统提示词
├── tools/            # 内置工具（base + 15 个核心实现）
├── runtime/          # 串行 + 有界并发工具调度器
├── permissions/      # 权限检查器 · 危险命令检测 · 沙箱 · 规则引擎
├── context/          # 两层压缩 + ContentReplacementState
├── memory/           # 自动记忆抽取 + 会话持久化
├── agents/           # 子代理定义、加载、工具过滤、任务管理
├── teams/            # 邮箱 · coordinator · tmux/iterm2/进程内后端
├── hooks/            # 生命周期钩子（15 事件、4 动作类型）
├── mcp/              # MCP 客户端 · 管理器 · 延迟加载包装
├── skills/           # 技能加载器 · 解析器 · 执行器
├── commands/         # 斜杠命令注册表 + 处理器
└── worktree/         # git worktree 隔离 + 生命周期清理
```

## 🔒 安全模型详解

每一次工具调用——无论串行还是并行——都经过同一条管线：

```text
工具调用 ─► pre_tool_use 钩子 ─► 权限管线 ─► 执行 ─► post_tool_use 钩子
                                      │
 ① plan 模式特例                      │  任何一层都可以 DENY；
 ② 安全命令白名单                     │  首个拒绝即短路。
 ③ 危险命令黑名单                     │
 ④ 路径沙箱                           │  "ask" 让循环挂起在一个
 ⑤ 项目/本地规则引擎                  │  asyncio.Future 上，由 TUI 解析；
 ⑥ 模式矩阵                           │  取消操作同样会解析所有未决对话框，
 ⑦ 人工确认                           │  输入框永远不会被卡死。
```

要点：

- **并行批次无法绕过检查。** 并发执行共享同一条授权路径；需要确认的操作会被明确拒绝并给出指引，而不是被静默放行。
- **派生检查器，而非新建。** 子代理/fork/teammate 经 `PermissionChecker.derive()` 继承检测器、规则与沙箱根目录——fork 自动批准*提问*，但 deny 规则与钩子照常生效。
- **token 级危险命令检测。** `rm -rf /` 的任意旗标顺序都会被拦截（`--force --recursive`、长短混合、`&&` 之后链接），外加 mkfs/dd/fork bomb/管道远程脚本/sudo/系统路径重定向。
- **Hook 注入双重防御：** 插值经 `shlex.quote` 转义后才进入 shell 命令；保留上下文键（`$MESSAGE`、`$TOOL_NAME` 等）不可被工具参数遮蔽。
- **"总是允许"不会被放大。** 持久化的前缀规则拒绝 shell 元字符，批准 `echo hi` 绝不会连带放行 `echo hi; rm -rf ~`。

## 🤖 多智能体与团队

```text
                 ┌────────────┐
                 │ Coordinator │  过滤工具集：只保留 spawn/delegate
                 └─────┬──────┘
        ┌──────────────┼──────────────┐
   ┌────▼────┐    ┌────▼────┐    ┌────▼────┐
   │ worker-1 │    │ worker-2 │    │ worker-3 │   worktree 隔离
   └────┬────┘    └────┬────┘    └────┬────┘
        └──────────────┴──────────────┘
             原子文件系统邮箱（.json claim/崩溃恢复）
             结构化 TaskSpec → WorkerState 状态机
```

- **隔离级别：** 进程内过滤代理 → 临时 git worktree → 运行真实 `codeyx` 进程的完整窗格（tmux/iTerm2，带启动参数）。
- **邮箱具备崩溃安全性：** 原子发布、claim-恢复语义、损坏消息进隔离区而非销毁。
- **worktree 全生命周期管理：** 按命名模式清扫过期项；关机时脏 worktree 保留并告警，干净的移除。

## ⌨️ 斜杠命令

| 命令 | 用途 |
|---|---|
| `/plan` `/do` | 进入规划模式 / 返回执行模式 |
| `/review` | 结构化代码评审 |
| `/compact [focus]` | 压缩会话历史 |
| `/session list/resume` | 会话持久化 |
| `/memory list/catalog/search/clear` | 检视记忆库 |
| `/skill list/search` | 发现与调用技能 |
| `/permission` | 切换权限模式 |
| `/status` | 运行状态总览 |
| `/help` | 全部命令 + 别名 |

## 📖 关键设计决策

- **AsyncIterator 事件流。** `Agent.run()` 产出类型化 `AgentEvent` dataclass；核心循环与 UI 完全解耦——同一循环驱动 TUI、无界面提示与 teammate 窗格。
- **ContentReplacementState。** 预算决策只记录一次并原样重放，让 Anthropic prompt-cache 前缀在请求间保持逐字节一致。
- **延迟加载 MCP 工具。** 初始只通告名称；完整 schema 经 `ToolSearch` 按需获取。
- **人机回环即数据流。** 权限询问让循环挂起在 `Future` 上，TUI 负责解析；取消操作确定性地解析每一个未决对话框。

## 🗺️ 路线图

- [ ] 流式工具调用渲染打磨
- [ ] 记忆聚合预算控制
- [ ] 沙箱 TOCTOU 加固说明与审计链路
- [ ] Trajectory 评测 harness 正式化

实时问题台账见 [docs/known-issues.md](docs/known-issues.md)。

## 🤝 参与贡献

欢迎 Issue 与 PR。提交 PR 前：

```bash
uv run pytest                     # 全部通过
uv run ruff check codeyx tests    # lint 干净
```

commit message 请使用英文。

## 📄 许可证

[MIT](LICENSE) © c2h4
