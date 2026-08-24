# CodeYX 项目

## 技术栈
- Python 3.11+（asyncio / Textual TUI / Pydantic v2 / hatchling + uv）

## 代码规范
- commit message 用英文
- 变量命名用 snake_case
- 提交前运行 `uv run pytest` 与 `uv run ruff check codeyx tests`，两者必须全绿
