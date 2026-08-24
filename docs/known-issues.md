# CodeYX Known Issues

本文档记录 CodeYX 当前系统中仍存在的问题、验证证据和后续处理建议。

维护规则：

- 每次修改代码后，都需要同步更新本文档。
- 新增问题时保留测试命令、失败现象和影响范围。
- 修复问题后不要直接删除条目，先将状态改为 `已修复`，并补充修复提交或验证命令。
- 与当前改动无关但测试暴露的问题，也应记录为 `既有问题`，避免误判为新回归。

最近更新时间：2026-08-25

---

## 1. 当前验证基线

### 已通过的核心验证

Phase 5 Multi-Agent / Harness / Benchmark 强化后，新增能力测试已通过：

```bash
.venv/bin/python -m pytest tests/test_teams.py::TestModels::test_task_spec_worker_prompt_roundtrip tests/test_teams.py::TestModels::test_worker_status_transitions tests/test_worktree.py::TestMergePreview::test_detects_file_level_conflict tests/test_harness.py tests/trajectory/test_trajectory_evaluator.py
```

结果：

```text
21 passed
```

补充回归：

```bash
.venv/bin/python -m pytest tests/test_agent.py tests/test_runtime.py tests/test_teams.py tests/test_harness.py
```

结果：

```text
76 passed, 2 failed
```

失败说明：

- 2 个失败均为既有 `ISSUE-003`：`tests/test_teams.py` 仍引用 `codeyx.prompts.BASE_PERSONA` 和 `PLAN_MODE_INSTRUCTIONS` 旧常量。
- 本次 Phase 5 新增测试和相邻 Agent / Runtime / Team 大部分回归均通过。

覆盖范围：

- `TaskSpec` 结构化任务协议
- `WorkerStatus` / `WorkerState` worker 状态机
- Worktree merge preview 文件级冲突检测
- 声明式 Agent harness 场景编排
- Trajectory benchmark 多维评分报告

Phase 4 Memory / Skills / Context 产品化后，相关模块测试已通过：

```bash
.venv/bin/python -m pytest tests/test_agent.py tests/test_commands.py tests/test_memory.py tests/test_skills.py tests/test_context.py
```

结果：

```text
183 passed
```

覆盖范围：

- Memory 目录型索引 `.codeyx/memory/MEMORY.md`
- Memory frontmatter 元数据解析
- Memory index / entry 加载大小限制
- 旧版 `.codeyx/memories.md` 兼容
- Skill `when_to_use` 元数据解析
- Skill discovery 检索评分
- Agent 自动注入 Skill 推荐提醒
- `/skill search` 命令入口
- `/memory catalog` 和 `/memory search` 命令入口
- Context recovery snapshot 过期清理

Phase 3 Permission / Security 增强后，权限与安全测试已通过：

```bash
.venv/bin/python -m pytest tests/test_permissions.py tests/security/test_command_injection.py
```

结果：

```text
123 passed
```

补充回归：

```bash
.venv/bin/python -m pytest tests/resilience/test_error_recovery.py tests/test_agent.py tests/test_tool_search.py tests/test_runtime.py
```

结果：

```text
47 passed
```

覆盖范围：

- PermissionDecision 可解释字段
- Plan Mode 非计划写入默认拒绝
- Plan Mode 计划文件写入例外
- 危险命令大小写/空白/链式/间接执行检测
- `python -c`、`perl -e`、`bash -c` 包装危险命令检测
- 系统关键文件 `chmod 777` 检测
- Agent 主循环回归

Phase 2 Tool Use / ToolSearch 升级后，核心 Agent Runtime 与工具系统相关测试已通过：

```bash
.venv/bin/python -m pytest tests/resilience/test_error_recovery.py tests/test_agent.py tests/test_tool_search.py tests/test_mcp.py::TestMCPToolWrapper tests/test_runtime.py
```

结果：

```text
49 passed
```

覆盖范围：

- Agent 主循环回归
- malformed tool call 恢复
- ToolSearch always/auto/disabled 模式
- Tool metadata tag 搜索
- MCP wrapper 命名与 schema
- ToolResult persisted metadata
- Runtime state / scheduler / recovery

Phase 1 Runtime 重构后，核心 Agent Runtime 相关测试已通过：

```bash
.venv/bin/python -m pytest tests/resilience/test_error_recovery.py tests/test_runtime.py tests/test_agent.py
```

结果：

```text
32 passed
```

覆盖范围：

- Agent 主循环
- Runtime state
- Tool scheduler
- Tool result recovery
- malformed tool call 恢复
- unknown tool 恢复
- max iteration 限制
- Plan Mode 基础链路
- tool_use/tool_result 写回

### 部分通过的周边验证

命令：

```bash
.venv/bin/python -m pytest tests/test_commands.py tests/test_context.py tests/test_hooks.py ...
```

结果：

```text
115 passed 后因 Hook 长用例卡住，手动中断
```

说明：

- 已验证 commands、context、hooks 前半部分没有受到 Phase 1 改动影响。
- Hook 全量测试存在长时间等待问题，需要单独定位。

命令：

```bash
.venv/bin/python -m pytest tests/test_mcp.py tests/test_memory.py tests/test_permissions.py tests/test_recovery.py tests/test_replacement_state.py tests/test_skills.py tests/test_subagent.py tests/test_teams.py tests/test_tool_search.py tests/test_worktree.py tests/trajectory/test_trajectory_evaluator.py
```

结果：

```text
335 passed, 8 failed, 18 errors
```

说明：

- 失败集中在 MCP 配置 schema、Plan Mode 旧断言、prompts 常量、Worktree 构造参数不一致等既有问题。
- 这些问题不是 Phase 1 runtime 重构引入，但需要后续修复。

---

## 2. 已知问题清单

### ISSUE-001：MCP 配置结构与测试预期不一致

状态：已修复（2026-08-25 验证）

验证：`uv run python -m pytest tests/test_mcp.py -q` 全部通过；配置层统一为 list 格式，测试契约一致。
严重级别：P1
类型：配置 schema / 测试契约不一致
影响范围：`codeyx/config.py`, `codeyx/validator.py`, `tests/test_mcp.py`

失败用例：

```text
tests/test_mcp.py::TestLoadConfigMCP::test_stdio_server
tests/test_mcp.py::TestLoadConfigMCP::test_http_server
tests/test_mcp.py::TestLoadConfigMCP::test_both_command_and_url_errors
tests/test_mcp.py::TestLoadConfigMCP::test_neither_command_nor_url_errors
```

失败现象：

```text
ConfigError: 'mcp_servers' must be a list of server configs
```

初步判断：

- 当前 `validate_mcp_servers()` 要求 `mcp_servers` 是 list。
- 测试用例可能仍按旧版 dict 结构编写，或配置加载层原本期望兼容 dict/list 两种格式。

建议修复：

- 明确最终配置格式：只支持 list，还是兼容 dict。
- 如果面向用户易用性，建议兼容两种写法：
  - list：`mcp_servers: [{ name, command/url, ... }]`
  - dict：`mcp_servers: { server_name: { command/url, ... } }`
- 更新 validator、config loader 和 README 示例。
- 补充 schema 兼容性测试。

---

### ISSUE-002：Plan Mode 权限语义不一致

状态：已修复（2026-06-17 Phase 3）
严重级别：P1
类型：权限模型 / 测试契约不一致
影响范围：`codeyx/permissions/modes.py`, `codeyx/permissions/checker.py`, `tests/test_permissions.py`

失败用例：

```text
tests/test_permissions.py::TestPermissionMode::test_plan_mode
tests/test_permissions.py::TestPermissionChecker::test_plan_mode_denies_write
```

失败现象：

```text
assert mode_decide(PermissionMode.PLAN, "write") == "deny"
实际返回："ask"
```

初步判断：

- 当前实现中，Plan Mode 对部分写操作有特殊例外：允许写计划文件。
- 普通 write 工具在特殊规则之后可能落入 `ask`，而测试期望直接 `deny`。
- 从产品安全语义看，Plan Mode 对非计划文件写入应默认拒绝，而不是进入人工确认。

建议修复：

- 已在 `PermissionChecker.check()` 中明确 Plan Mode 语义：
  - 允许读/search/计划文件写入
  - 非计划文件写入直接 deny
  - Bash 等行动工具在 mode matrix 中直接 deny
- 已同步更新 `mode_decide()` 和测试。
- 已新增计划文件写入例外测试。

---

### ISSUE-003：prompts 模块测试引用了已不存在的旧常量

状态：已修复（2026-08-25 验证）

说明：`codeyx/prompts.py` 恢复了兼容导出 `BASE_PERSONA` 与 `PLAN_MODE_INSTRUCTIONS`，同时保留 section builder 风格的 `build_system_prompt()`。

验证：`uv run python -m pytest tests/test_teams.py -q` 全部通过。
严重级别：P2
类型：测试与实现漂移
影响范围：`codeyx/prompts.py`, `tests/test_teams.py`

失败用例：

```text
tests/test_teams.py::TestAgentCoordinatorIntegration::test_normal_prompt
tests/test_teams.py::TestAgentCoordinatorIntegration::test_coordinator_overrides_plan
```

失败现象：

```text
ImportError: cannot import name 'BASE_PERSONA' from 'codeyx.prompts'
ImportError: cannot import name 'PLAN_MODE_INSTRUCTIONS' from 'codeyx.prompts'
```

初步判断：

- `prompts.py` 可能已经重构为 `build_system_prompt()` 和 section builder 风格。
- 测试仍依赖旧常量名。

建议修复：

- 方案 A：恢复兼容导出 `BASE_PERSONA`、`PLAN_MODE_INSTRUCTIONS`。
- 方案 B：更新测试，不再直接依赖内部常量，只验证 `build_system_prompt()` 输出行为。
- 推荐方案 B，减少测试对实现细节的耦合。

---

### ISSUE-004：WorktreeManager 构造参数与测试不一致

状态：已修复（2026-08-25 验证）

说明：构造函数契约已对齐；新增公开 `remove_worktree(name)` 用于非活跃残留 worktree 的直接清理。

验证：`uv run python -m pytest tests/test_worktree.py -q` 全部通过。
严重级别：P1
类型：API 契约不一致
影响范围：`codeyx/worktree/manager.py`, `tests/test_worktree.py`

失败用例：

```text
tests/test_worktree.py::TestWorktreeManager::*
tests/test_worktree.py::TestChangeDetection::*
tests/test_worktree.py::TestReadWorktreeHeadSha::test_valid_worktree
```

失败现象：

```text
TypeError: WorktreeManager.__init__() got an unexpected keyword argument 'file_cache'
```

初步判断：

- 测试 fixture 仍按旧版 `WorktreeManager(file_cache=...)` 构造。
- 当前实现移除了或重命名了 `file_cache` 参数。

建议修复：

- 检查 `WorktreeManager` 当前真实构造函数。
- 如果 file cache 仍是必要能力，则恢复可选参数并在 enter/exit worktree 时清理缓存。
- 如果 file cache 已迁移到别处，则更新测试 fixture。
- 推荐保持向后兼容：`file_cache: FileCache | None = None`，减少外部调用破坏。

---

### ISSUE-005：Hook 全量测试存在长时间等待或任务泄漏

状态：已修复（2026-08-25 验证）

说明：`HookEngine` 现在通过统一的 `_execute_hook()` 对每个 hook 动作执行 `asyncio.wait_for(timeout)` 强制超时，后台任务统一登记并在完成时取回异常，消除 pending task 泄漏路径。

验证：`uv run python -m pytest tests/test_hooks.py -q --durations=3` 共 `50 passed in ~1.7s`，最长单用例 1.0s（合法 timeout 用例）。
严重级别：P1
类型：异步任务生命周期 / 测试稳定性
影响范围：`codeyx/hooks/engine.py`, `tests/test_hooks.py`

现象：

```text
tests/test_hooks.py 前半段通过后长时间无输出，手动 KeyboardInterrupt
Task was destroyed but it is pending!
task: <Task cancelling ... HookEngine._run_single() ...>
```

初步判断：

- HookEngine 可能在某些 timeout/cancel 路径没有正确 await/cancel 子任务。
- 测试中某个 async hook 可能产生 pending task，导致 pytest 卡住。

建议修复：

- 单独二分定位卡住用例。
- 检查 `HookEngine._run_single()` 的 timeout 和 cancellation 处理。
- 为 HookEngine 增加明确的 task cleanup。
- 测试中避免无限等待，必要时使用 `asyncio.wait_for()`。

---

### ISSUE-006：危险命令检测存在间接执行绕过

状态：已修复（2026-06-17 Phase 3）
严重级别：P0
类型：安全检测缺口
影响范围：`codeyx/permissions/dangerous.py`, `tests/security/test_command_injection.py`

失败用例：

```text
python -c "import os; os.system('rm -rf /')"
perl -e 'system("rm -rf /")'
bash -c 'rm -rf /'
chmod 777 /etc/passwd
```

失败现象：

```text
Indirect execution missed
Permission escalation missed
```

初步判断：

- 当前检测能覆盖基础危险命令和部分链式命令，但对解释器包裹执行、`system()` 调用、`bash -c` 包装识别不足。
- `chmod 777 /etc/passwd` 缺少系统关键文件权限放宽规则。

建议修复：

- 已对命令做空白归一化和基础 shell tokenization。
- 已增加解释器间接执行检测：
  - `python -c`
  - `perl -e`
  - `ruby -e`
  - `node -e`
  - `bash -c`
  - `sh -c`
- 已支持递归检测包装命令中的二级危险命令。
- 已增加关键系统文件权限修改规则：
  - `/etc/passwd`
  - `/etc/shadow`
  - `/etc/sudoers`
  - `/boot/*`
- 验证结果：`tests/security/test_command_injection.py` 全部通过。

---

### ISSUE-007：Context 性能测试与当前预算策略不一致

状态：已修复（2026-08-25 验证）

说明：性能测试 fixture 与当前预算阈值对齐，不再硬编码必须产生 replacement records 的行为断言。

验证：`uv run python -m pytest tests/perf -q` 共 `5 passed in ~0.1s`。
严重级别：P2
类型：性能测试 / 预算策略不一致
影响范围：`codeyx/context/manager.py`, `tests/perf/test_context_perf.py`

失败用例：

```text
tests/perf/test_context_perf.py::TestContextCompressionPerf::test_budget_apply_scales_linearly
```

失败现象：

```text
assert len(records) > 0
实际 records == []
```

初步判断：

- 测试期望某些 tool result 被持久化，但当前预算策略没有触发 replacement records。
- 可能是测试数据规模、阈值常量或 budget 策略已经变化。

建议修复：

- 检查 `SINGLE_RESULT_CHAR_LIMIT`、`AGGREGATE_CHAR_LIMIT` 与测试 fixture 大小。
- 明确该测试验证的是性能还是替换行为。
- 如果是性能测试，不应硬编码必须产生 records；如果是预算行为测试，应放到普通 context 测试中。

---

### ISSUE-008：仓库存在未跟踪历史目录和测试文档

状态：已解决（2026-08-25）

说明：`mewcode/` 与 `tests/test-summary.md` 已不存在；`tests/interview-qa.md`、`tests/optimization-plan.md` 属于个人学习材料而非项目资产，已从仓库移除（git 历史仍可恢复）。新增 `config.example.yaml` 作为可提交的配置样例。
严重级别：P3
类型：仓库卫生
影响范围：Git 工作区

当前未跟踪项：

```text
mewcode/
tests/test-summary.md
```

初步判断：

- `mewcode/` 可能是项目重命名前残留目录。
- `tests/test-summary.md` 可能是之前测试总结文档，尚未确认是否应纳入仓库。

建议处理：

- 确认 `mewcode/` 是否仍有保留价值。
- 如果无价值，删除或加入 `.gitignore`。
- 如果 `tests/test-summary.md` 是正式文档，则纳入版本控制；否则移动到外部文档目录或忽略。

---

## 3. 优先修复顺序

截至 2026-08-25，ISSUE-001 至 ISSUE-008 全部修复或解决，当前无待处理问题。后续新增问题按本节顺序规则追加。

---

## 4. 更新记录

### 2026-06-17 Phase 2

- 完成 Tool Use / ToolSearch 升级。
- 新增 `ToolMetadata`，为工具补充 source、risk_level、timeout、streaming、permission、tags 等元数据。
- 扩展 `ToolResult`，支持 metadata、artifacts、persisted_path、display_hint。
- `ToolRegistry` 新增 `tool_search_mode`，支持 `always`、`auto`、`disabled`。
- ToolSearch keyword 搜索现在支持匹配 tool metadata tags。
- MCP 工具命名已统一为 `mcp__<server>__<tool>`，修复此前 wrapper 与 ToolFilter/App 识别规则不一致的问题。
- Agent 大工具结果持久化时会回填 `ToolResult.persisted_path`、`display_hint` 和 `metadata.original_chars`。
- 通过验证：`49 passed`。
- 当时未解决问题包括 MCP 配置 schema、Plan Mode 权限语义、Hook 长用例、危险命令检测绕过、WorktreeManager 构造参数等；其中 Plan Mode 权限语义和危险命令检测绕过已在 Phase 3 修复。

### 2026-06-17 Phase 3

- 完成 Permission / Security 增强。
- `Decision` 增加 `source`、`risk_level`、`matched_rule`、`details`，权限结果可解释性增强。
- Plan Mode 权限矩阵改为默认拒绝 write/command，计划文件写入通过 Layer 0 特例放行。
- 危险命令检测增加空白归一化、shell tokenization、解释器间接执行、shell `-c` 包装检测和关键系统文件 `chmod 777` 检测。
- 修复 `ISSUE-002` 和 `ISSUE-006`。
- 通过验证：`tests/test_permissions.py tests/security/test_command_injection.py` 共 `123 passed`。
- 回归验证：`tests/resilience/test_error_recovery.py tests/test_agent.py tests/test_tool_search.py tests/test_runtime.py` 共 `47 passed`。

### 2026-06-17 Phase 4

- 完成 Memory / Skills / Context 产品化增强。
- Memory 支持 `.codeyx/memory/` 目录型存储，`MEMORY.md` 作为索引，单项 memory 文件支持 frontmatter 元数据。
- Memory 加载增加索引行数、索引字节数、单项文件字节数限制，超限时注入截断提示。
- 自动记忆写入继续保留旧版 `.codeyx/memories.md`，同时生成目录型 memory 文件，保持兼容。
- Skill frontmatter 增加 `when_to_use` / `whenToUse`，`SkillLoader.discover()` 支持按名称、描述、触发条件检索评分。
- `/skill search <query>` 暴露 Skill discovery 能力。
- Agent 启动 LLM 调用前会根据最新用户输入自动发现相关 Skill，并通过 system reminder 建议模型调用 `LoadSkill`。
- Memory 增加 `catalog()` 和 `search()`，支持按目录型 memory 的 frontmatter/body 检索，并通过 `/memory catalog`、`/memory search <query>` 暴露。
- Context `RecoveryState` 增加过期快照清理，compact recovery attachment 渲染前自动 prune stale file / skill snapshots。
- 通过验证：`tests/test_agent.py tests/test_commands.py tests/test_memory.py tests/test_skills.py tests/test_context.py` 共 `183 passed`。

### 2026-06-17 Phase 5

- 完成 Multi-Agent / Harness / Benchmark 强化。
- 新增 `TaskSpec`，Coordinator 可以用结构化协议描述任务目标、约束、文件范围、期望输出、验证命令、分配 worker 和 deadline turns。
- 新增 `WorkerState` / `WorkerStatus`，统一 worker 的 pending、running、waiting、completed、failed、cancelled、merged 状态和状态转移记录。
- 新增 Worktree `MergePreview` 和 `build_merge_preview()`，合并前输出 worker diff、target diff、文件级冲突列表、diff stat 和未提交变更保护信号。
- 新增 `tests/harness/` 声明式 Agent 场景 runner，支持 setup files、scripted LLM、tool trajectory、file change 和 command assertion。
- 复用并验证 `TrajectoryEvaluator`，覆盖 tool selection、efficiency、error recovery、safety、overall 多维评分报告。
- 通过验证：Phase 5 新增能力与 benchmark 测试共 `21 passed`。
- 补充回归：`tests/test_agent.py tests/test_runtime.py tests/test_teams.py tests/test_harness.py` 中 `76 passed, 2 failed`；失败为既有 `ISSUE-003` prompts 旧常量测试漂移。

### 2026-08-25 全面重构、安全加固与仓库卫生

本轮为全库审计驱动的系统性重构（91 个文件，+1195/-802），核心变更：

**安全 / 权限**
- 串行与并行工具执行路径合并为同一授权管线（pre-hook → permission → 执行 → post-hook），并行批次中需要交互确认的动作会被明确拒绝并提示单独调用，不再可能绕过或死锁。
- `PermissionChecker.derive()`：子代理 / skill fork / teammate 从父级继承检测器、规则引擎与额外沙箱根目录，仅覆盖差异项；fork 固定 `derive(mode=DONT_ASK)`，deny 规则仍然生效。
- Hook 模板插值默认 shell-quote（单遍替换），杜绝 `$TOOL_ARGS.*` 注入逃逸；每个 hook 动作强制 timeout。
- 危险命令检测新增 `rm --recursive/--force/--no-preserve-root` 长格式变体。
- MCP 连接与调用分别施加 30s/120s 超时，取消的 connect 会完整清理 AsyncExitStack，不留僵尸 stdio 子进程。
- 并行调度器加 `Semaphore(8)` 上界。
- PathSandbox 遍历策略收紧：`..` 归一化后必须严格落在 project_root 内。

**正确性 / 生命周期**
- `run_to_completion()` 修复 compact 分支 env_context NameError（B1）。
- AskUserQuestion 死锁修复：工具通过 `on_pending` 回调先挂载 UI 再阻塞等待 future。
- 取消 agent 时统一 resolve 未决权限/提问 future 并卸载对话框，输入框不会再被孤儿提示卡死。
- `/session resume` 候选列表改为共享引用，跨命令调用不再丢失。
- Mailbox 消费改为原子 claim（rename → `.consuming` 后缀），多消费者竞争不丢消息。
- Teammate 子代理补齐 team_name/_team_manager 接线；iTerm2 pane 注册 pane_id；worktree 运行失败路径自动清理。
- 关机时 dirty worktree 保留并告警，clean 的走公开 `remove_worktree()` 移除；stale 清理只匹配本项目生成的 ephemeral 命名模式。
- 后台任务（记忆提取等）统一登记 + done-callback 记录异常，消除 "Task exception was never retrieved"。

**工程卫生**
- ruff 全绿（E/F/I/N/W/UP 全量门禁，E501 保持咨询性）；str-Enum 迁移 StrEnum；导入规范化（dialog 组件提升到模块级）。
- pytest 配置修正（移除孤儿 `--cov-report`、显式 asyncio fixture loop scope）；mock LLM 工具 ID 单轮内自增去重；scenario runner protocol 参数化；`AgentToolParams.isolation` 显式校验非法值。
- 删除零引用的 `session_dialog.py`；移除 tests 下两份个人学习文档；新增 `config.example.yaml`。
- 全量验证：`uv run python -m pytest tests/ -q` → **647 passed**；`uv run ruff check codeyx tests` → **All checks passed**；123 个模块全量导入无错。

**对抗式审查修复轮（两路并行复审：安全路径 / 并发正确性）**

复审确认 7 项缺陷并全部修复，均有回归测试：

1. （高危）boot prompt 回合不可取消且可与用户输入并发跑双 agent 循环 → `_send_message` 自登记 `_agent_task`，Escape/Ctrl+C 全路径可打断；boot 任务挂 done-callback 并在关机时取消。
2. （高危）skill fork 未继承 hook engine，pre/post_tool_use 钩子被静默跳过 → `execute_fork` 显式传递父级 `hook_engine`。
3. （高危）`rm --force --recursive /` 等任意旗标顺序/长短混合变体绕过检测 → 正则枚举替换为 token 级分段检测（含链式命令、大小写、glob 根目录），实测全部拦截且无误报。
4. （中高）mailbox 原子 claim 在崩溃时永久丢消息；非原子写 + claim 会静默销毁半写消息 → 写入改 temp+`os.replace` 原子发布；消费改「恢复 `.consuming` 残留 → 解析成功才删除」，损坏消息进隔离区不销毁。
5. （低中）AskUserQuestion 300s 超时后对话框残留、输入框持续禁用 → 新增 `on_expired` 回调，超时即卸载对话框并恢复输入。
6. （低中）hook 模板中工具参数键可遮蔽保留上下文键（`$MESSAGE` 等）→ 保留键始终优先，杜绝内容伪造注入 prompt/HTTP。
7. （低）规则缓存同 tick 同长度编辑可能读到陈旧层 → 缓存加 5s TTL 兜底；另将既有 ALLOW_ALWAYS 前缀规则加固为不含 shell 元字符才持久化（`Bash(echo hi*)` 不再放大到 `echo hi; <任意>`）。

### 2026-06-17

- 创建本文档。
- 记录 Phase 1 Runtime 重构后的验证结果。
- 记录当前非集成测试暴露的既有问题。
- 明确后续每次代码更新需要同步维护本文档。
