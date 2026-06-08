# CodeYX 项目测试优化方案

> 从测试开发工程师视角，结合 2025-2026 年前沿 Agent 评测框架，对 CodeYX 项目提出系统性测试优化方案。
> 对标 JD 关键词：自动化测试框架、AI 驱动质量工程、AI Evals、Agent 质量评估、AI 可靠性。

---

## 一、当前测试现状分析

### 1.1 覆盖概况

| 指标 | 数值 |
|------|------|
| 测试文件数 | 15 个 |
| 测试代码总行数 | 6,683 行 |
| 测试框架 | pytest + pytest-asyncio |
| 核心 Mock 模式 | `MockLLMClient`（脚本化 LLM 响应流） |

### 1.2 现有测试模块

| 测试文件 | 行数 | 覆盖模块 | 质量评估 |
|----------|------|---------|---------|
| `test_permissions.py` | 641 | 五层权限（DangerousCommandDetector、PathSandbox、RuleEngine、PermissionChecker） | 良好 |
| `test_agent.py` | 486 | Agent 循环（工具分区、事件流、Plan Mode、max_tokens 恢复） | 良好 |
| `test_hooks.py` | 560 | Hooks 引擎、条件求值、加载器、执行器 | 良好 |
| `test_memory.py` | 561 | 自动记忆提取、Session 持久化、指令加载 | 良好 |
| `test_context.py` | 267 | Layer 1 压缩（工具结果持久化、预算控制、摘要提取） | 中等 |
| `test_subagent.py` | 737 | 子 Agent 系统 | 良好 |
| `test_teams.py` | 572 | Team 协作、邮箱、Coordinator | 良好 |
| `test_skills.py` | 551 | Skill 解析、加载、执行 | 良好 |
| `test_commands.py` | 479 | 斜杠命令注册、解析、补全 | 良好 |
| `test_mcp.py` | 299 | MCP 客户端连接、工具包装 | 中等 |
| `test_worktree.py` | 404 | Git Worktree 管理 | 良好 |
| `test_tool_search.py` | 317 | 延迟工具发现 | 良好 |
| `test_replacement_state.py` | 233 | ContentReplacementState 决策冻结 | 良好 |
| `test_recovery.py` | 70 | 压缩后恢复 | 薄弱 |
| `verify_subagent.py` | 506 | 子 Agent 端到端验证 | 良好 |

### 1.3 关键缺陷

1. **缺少 Agent 轨迹评测**：只测事件类型和数量，不测 Agent 的"行为正确性"（是否用了正确的工具顺序、是否有冗余操作）
2. **缺少端到端集成测试**：没有真实 LLM 调用的集成测试（全 mock），无法验证多协议客户端
3. **缺少 Benchmark 对接**：未对接任何外部评测基准（SWE-bench、Terminal-Bench 等）
4. **缺少 AI Evals 体系**：JD 要求的"AI 生成用例、缺陷智能分类、自动化结果自愈"尚未涉及
5. **缺少性能/压力测试**：上下文压缩的性能边界未验证（O(n²) 字符串拼接、大文件处理）
6. **缺少安全渗透测试**：危险命令检测的绕过测试不够系统化（缺大写变体、Unicode 混淆、命令注入等）
7. **缺少可靠性测试**：网络中断恢复、LLM 返回畸形数据、并发竞态等异常路径
8. **测试报告缺失**：无 Allure/HTML 报告、无覆盖率统计

---

## 二、前沿 Agent 评测框架概览

### 2.1 主流 Benchmark 体系

| Benchmark | 定位 | 2026 SOTA | 与 CodeYX 的相关性 |
|-----------|------|-----------|-------------------|
| **SWE-bench Verified** | 500 个真实 GitHub issue 修复任务，单文件 Python bug fix | Claude Opus 4.7: 82.0% | 高：验证 Agent 的代码修复能力 |
| **SWE-bench Pro** | 1,865 个跨文件、长时间任务（含私有/商业仓库） | GPT-5.5: 56.8% | 中：需要 Docker 沙箱 |
| **Terminal-Bench 2.0** | CLI 操作评测（DevOps、脚本、自动化） | GPT-5.5: 82.7% | 极高：直接测试 CLI Agent 能力 |
| **ProcBench** | 过程级缺陷评测——不只测结果，还测 Agent 行为轨迹（文件权限、操作顺序、规则遵循） | - | 极高：对齐 JD 的"Agent 质量评估" |
| **OctoCodingBench** | 过程评测——Agent 是否遵循规则（如不修改只读文件、正确使用工具），ISR 仅 36.2% | Claude Opus 4.5: 36.2% | 极高：验证 Agent 的工具使用规范性 |
| **LiveCodeBench** | 免污染的实时编程评测 | - | 中 |
| **Tool Decathlon** | 多样化、长周期工具使用 | - | 中 |

### 2.2 关键评测工具

| 工具 | 用途 |
|------|------|
| **Harbor** | Agent 评测运行框架 + RL 环境（1,011 stars） |
| **SWE-ReX** | AI Agent 的沙盒化代码执行环境（453 stars） |
| **LLM-as-a-Verifier** | 用 LLM 作为验证器缩放验证计算，在 Terminal-Bench 和 SWE-bench 上达到 SOTA |

### 2.3 2026 年评测趋势

1. **过程 > 结果**：ProcBench 和 OctoCodingBench 证明 Pass@k 指标遗漏了关键的过程违规（修改错误文件、忽略安全规则）
2. **反污染**：Benchmark 转向私有仓库防止数据泄露
3. **验证缩放**：不缩放训练计算，而是缩放验证时的推理计算来提升 Agent 表现
4. **人机协作**：CodeClash 致力于隔离模型能力，变化协作方式（单 Agent / 多 Agent / 人+Agent）

---

## 三、优化方案

### 3.1 方案总览

```
Layer 4: Agent Evals  ── 对接外部 Benchmark + 自定义评测集
Layer 3: 轨迹与过程  ── Agent 行为轨迹评测 + AI-as-Judge 质量评估
Layer 2: 集成与端到端 ── 真实 LLM 集成测试 + 多协议验证
Layer 1: 基础加固     ── 补齐异常路径、性能、安全渗透测试
```

### 3.2 Layer 1：基础测试加固

#### 3.2.1 安全渗透测试（`tests/security/`）

当前 `test_permissions.py` 只测试了 8 种基础危险命令，但缺少系统化的攻击向量覆盖：

| 测试场景 | 优先级 | 预计用例数 |
|----------|--------|-----------|
| 命令注入变体（大小写混淆 `R M  -R f`、双空格、Tab 分隔） | P0 | 12 |
| Unicode 混淆（全角字符、零宽字符插入 `rm\x00-rf`） | P0 | 8 |
| 路径遍历（`../../etc/passwd`、symlink 跳转） | P0 | 10 |
| 命令链接绕过（`ls && rm -rf /`、反引号、`$()` 嵌套） | P0 | 8 |
| 环境变量注入（`PATH` 劫持、`LD_PRELOAD`） | P1 | 6 |
| 提权命令（`sudo -u root`、`su -c`、`pkexec`、`doas`） | P1 | 8 |
| 间接执行（`python -c "import os; os.system('rm -rf /')"`、`perl -e`） | P1 | 6 |

**实施**：新增 `tests/security/test_command_injection.py`、`tests/security/test_path_bypass.py`

#### 3.2.2 异常路径测试（`tests/resilience/`）

| 测试场景 | 优先级 |
|----------|--------|
| LLM 返回畸形 JSON（不完整的 tool_use args） | P0 |
| LLM 返回未知 tool name | P0 |
| 网络中断后重连（`NetworkError` → 重试逻辑） | P1 |
| LLM 返回空响应（0 个 choices） | P0 |
| 上下文压缩失败（CircuitBreaker 触发） | P1 |
| 并发工具执行竞态（同一文件同时被多个工具读写） | P1 |
| Token 计数溢出（输入 token 超过 context_window） | P1 |
| MCP Server 启动失败 / 中途崩溃 | P0 |

**实施**：新增 `tests/resilience/test_error_recovery.py`、`tests/resilience/test_mcp_failures.py`

#### 3.2.3 性能基准测试（`tests/perf/`）

| 测试场景 | 指标 |
|----------|------|
| Layer 1 压缩性能（10MB 工具结果） | 处理时间 < 100ms |
| 大文件 ReadFile（100MB 文件） | 内存占用 < 2x 文件大小 |
| 100 轮对话的 context 压缩 | 压缩时间 < 5s |
| 100 个 MCP 工具的注册与发现 | 注册时间 < 2s |

**实施**：新增 `tests/perf/test_context_perf.py`、集成 `pytest-benchmark`

### 3.3 Layer 2：Agent 轨迹与过程评测

这是 **JD 最核心的要求**——"Agent 质量评估"、"AI Evals"。

#### 3.3.1 轨迹评测框架设计

核心理念来自 ProcBench 和 OctoCodingBench：**不只测 Agent 是否完成任务，还要测它做任务的过程是否正确**。

```
Agent 执行任务 → 记录完整轨迹 (Trajectory)
  ↓
轨迹包含:
  - 每轮 LLM 调用的 tool_calls 列表
  - 每个 tool 的 arguments 和执行结果
  - Permission check 的决策链
  - 时间戳和 token 用量
  ↓
评测维度:
  ├─ 工具选择正确性: 是否先 ReadFile/Grep 再 EditFile（不是直接猜）
  ├─ 工具使用效率: 是否有冗余读取/重复搜索
  ├─ 权限合规性: 是否在权限允许范围内操作
  ├─ 错误恢复: 工具执行失败后是否正确重试
  ├─ 安全合规: 是否触发了危险命令检测
  └─ 轨迹长度: 是否在合理轮次内完成任务
```

**实施**：新增 `tests/trajectory/trajectory_evaluator.py`

```python
@dataclass
class TrajectoryStep:
    turn: int
    tool_calls: list[str]       # 本轮调用的工具名列表
    tool_args: list[dict]
    tool_results: list[str]      # 工具结果摘要
    is_error: list[bool]
    tokens_used: int

class TrajectoryEvaluator:
    def evaluate(self, trajectory: list[TrajectoryStep], 
                 expected_pattern: list[str]) -> TrajectoryScore:
        """评测轨迹与预期模式的匹配度"""
        # 1. 工具顺序正确性
        # 2. 无冗余操作
        # 3. 错误恢复合理性
        ...
```

#### 3.3.2 30 个标准任务集

构建一组可复现的 Agent 任务，覆盖典型编程场景：

| 类别 | 示例任务 | 期望工具链 |
|------|---------|----------|
| 代码阅读 | "解释 main.py 的功能" | ReadFile("main.py") → 文本回复 |
| Bug 修复 | "修复 test.py 第 45 行的 syntax error" | ReadFile → EditFile → Bash("python test.py") |
| 代码搜索 | "找到所有调用 deprecated_func 的地方" | Grep("deprecated_func") → 文本回复 |
| 文件操作 | "创建一个新的 utils.py 并导入" | WriteFile → ReadFile 验证 |
| 重构 | "把 foo.py 的 bar() 函数提取到 helpers.py" | ReadFile(foo.py) → ReadFile(helpers.py) → EditFile × 2 |
| 多文件修复 | "修复所有文件中的 import 路径错误" | Grep → ReadFile × N → EditFile × N |
| Git 操作 | "查看最近的 commit 并回滚最后一个" | Bash("git log") → Bash("git revert") |

每个任务包含：
- 初始文件系统状态（`tmp_path` fixture)
- 期望的轨迹模式（最小工具调用序列）
- 期望的最终文件系统状态
- 质量评分标准（工具选择正确性 40% + 结果正确性 60%）

#### 3.3.3 AI-as-Judge 自动评测

引入 LLM 作为评测者（对齐 JD 的"AI 驱动质量工程"）：

```python
class AIJudgeEvaluator:
    """用 LLM 评估 Agent 轨迹质量。"""
    
    JUDGE_PROMPT = """
    你是一个 Agent 轨迹评审专家。请评估以下 Agent 执行任务的过程：

    任务：{task_description}
    工具可用：{available_tools}
    轨迹：{trajectory_json}

    请从以下维度打分（1-5）：
    1. 工具选择正确性：Agent 是否优先使用了正确的工具？
    2. 操作效率：是否有冗余或低效的操作？
    3. 错误处理：遇到错误是否正确响应？
    4. 安全合规：是否遵循了安全规则？
    5. 整体轨迹是否是最优解？
    """
    
    async def evaluate(self, task, trajectory) -> JudgeScore: ...
```

### 3.4 Layer 3：集成与端到端测试

#### 3.4.1 真实 LLM 集成测试

当前所有测试都用 `MockLLMClient`。需要加入真实 LLM 调用的冒烟测试：

```python
@pytest.mark.integration
@pytest.mark.slow
class TestRealLLMIntegration:
    """需要配置真实 API key 才能运行。CI 中用 skip 标记。"""
    
    @pytest.mark.parametrize("protocol", ["anthropic", "openai", "openai-compat", "deepseek"])
    async def test_basic_conversation(self, protocol: str, tmp_path):
        """验证所有协议的基本对话能力。"""
        config = ProviderConfig(name="test", protocol=protocol, base_url=..., model=...)
        client = create_client(config)
        conv = ConversationManager()
        conv.add_user_message("Hello, respond with just 'OK'")
        # ... stream and verify
    
    async def test_tool_use_roundtrip(self, tmp_path):
        """验证多轮工具调用。"""
        ...
```

**CI 策略**：默认 `skip`，每周定时触发。

#### 3.4.2 多协议兼容性矩阵

| 测试 | Anthropic | OpenAI | OpenAI-Compat | DeepSeek |
|------|-----------|--------|---------------|----------|
| 基本对话 | ✓ | ✓ | ✓ | ✓ |
| Tool Use（读文件） | ✓ | ✓ | ✓ | ✓ (chat only) |
| Tool Use（写文件） | ✓ | ✓ | ✓ | ✓ (chat only) |
| Thinking/Reasoning | ✓ (thinking) | - | - | ✓ (reasoner) |
| Prompt Cache | ✓ | - | - | - |
| Max Token 恢复 | ✓ | ✓ | ✓ | ✓ |
| 错误处理 | ✓ | ✓ | ✓ | ✓ |

### 3.5 Layer 4：对接外部 Benchmark

#### 3.5.1 Terminal-Bench 对接

Terminal-Bench 是 CodeYX 最自然的评测基准（都是 CLI Agent）：

```
CodeYX 作为被测 Agent
  ↓ 通过 Bash 工具执行命令
Terminal-Bench 环境 (Docker)
  ↓ 提供任务描述
Agent 执行终端操作
  ↓ 记录轨迹
Terminal-Bench 评分 → Resolve Rate
```

**实施步骤**：
1. 写一个 `run_terminal_bench.py` 适配器：将 Terminal-Bench 的 task prompt 注入 CodeYX 的 conversation
2. 在 Docker 沙箱中运行 CodeYX + Terminal-Bench
3. 收集轨迹和评分

#### 3.5.2 SWE-bench 对接

更复杂但更有说服力：

```
SWE-bench 任务 (issue + repo snapshot)
  ↓
CodeYX Agent 启动，work_dir = repo 根目录
  ↓
Agent 理解 issue → ReadFile/Grep 定位 → EditFile 修复 → Bash 验证
  ↓
生成 patch (git diff)
  ↓
SWE-bench 评测 → 运行 repo 测试 → Pass/Fail
```

#### 3.5.3 自定义 CodeYX-Bench

构建 20-30 个专门测 CLI Agent 的任务。优势：可控、可复现、不需要 Docker 沙箱。

### 3.6 测试基础设施建设

#### 3.6.1 测试报告（Allure）

```bash
pytest --alluredir=./allure-results
allure generate ./allure-results -o ./allure-report
```

#### 3.6.2 覆盖率

```bash
pytest --cov=codeyx --cov-report=html --cov-report=term-missing
```

#### 3.6.3 CI Pipeline（GitHub Actions）

```yaml
name: CodeYX Test Suite
on: [push, pull_request]
jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: uv sync
      - run: uv run pytest --cov=codeyx --cov-report=xml
      - run: uv run ruff check .
  
  security:
    runs-on: ubuntu-latest
    steps:
      - run: uv run pytest tests/security/ -v
  
  integration:
    runs-on: ubuntu-latest
    if: github.event_name == 'schedule'  # 每周触发
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    steps:
      - run: uv run pytest tests/integration/ -v -m integration
```

---

## 四、实施优先级与路线图

| 阶段 | 内容 | 预计新增用例数 | 工期 |
|------|------|-------------|------|
| Phase 1（本周） | 补齐安全渗透测试 + 异常路径测试 | ~80 | 3 天 |
| Phase 2（本月） | 构建轨迹评测框架 + 30 个标准任务集 | ~60 | 1 周 |
| Phase 3（本月） | AI-Judge 自动评测 + 测试报告/覆盖率 | ~20 | 3 天 |
| Phase 4（下月） | 真实 LLM 集成测试 + 多协议矩阵 | ~30 | 3 天 |
| Phase 5（下月） | Terminal-Bench / SWE-bench 对接 | 适配器开发 | 1 周 |
| Phase 6（持续） | CI Pipeline + 定期集成测试 | - | 持续 |

---

## 五、与 JD 的对标

| JD 要求 | 对应方案 | 体现点 |
|---------|---------|-------|
| 构建自动化测试框架 | pytest 体系完善 + Allure 报告 + CI Pipeline | Section 3.6 |
| AI 驱动质量工程 | AI-Judge 自动评测 Agent 轨迹 | Section 3.3.3 |
| AI 生成用例 | 基于 LLM 自动生成任务变体 | Section 3.3.2 |
| 缺陷智能分类 | 轨迹评测中的错误分类（工具选择/执行/安全） | Section 3.3.1 |
| 自动化结果自愈 | 异常路径测试 + 错误恢复验证 | Section 3.2.2 |
| AI Evals | 完整四层评测体系（基准→轨迹→集成→外部 Benchmark） | Section 3.3/3.5 |
| Agent 质量评估 | 三维度：功能正确性 + 过程规范性 + 效率 | Section 3.3.1 |
| AI 可靠性 | 异常路径、网络中断、LLM 畸形响应测试 | Section 3.2.2 |
