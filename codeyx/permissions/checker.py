from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from codeyx.permissions.dangerous import DangerousCommandDetector, is_safe_command
from codeyx.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from codeyx.permissions.rules import RuleEngine, extract_content
from codeyx.permissions.sandbox import PathSandbox
from codeyx.tools.base import Tool

_PLAN_MODE_ALLOWED_TOOLS = frozenset({"Agent", "ToolSearch", "AskUserQuestion"})


@dataclass
class Decision:
    effect: DecisionEffect
    reason: str
    source: str = "unknown"
    risk_level: str = "low"
    matched_rule: str | None = None
    details: list[str] = field(default_factory=list)


class PermissionChecker:


    def __init__(
        self,
        detector: DangerousCommandDetector,
        sandbox: PathSandbox,
        rule_engine: RuleEngine,
        mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> None:
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.mode = mode
        self.plan_file_path: str = ""


    def check(self, tool: Tool, arguments: dict[str, Any]) -> Decision:
        content = extract_content(tool.name, arguments)

        # Layer 0: Plan mode exceptions
        if self.mode == PermissionMode.PLAN:
            if tool.name in _PLAN_MODE_ALLOWED_TOOLS:
                return Decision(
                    effect="allow",
                    reason="Plan mode: allowed tool",
                    source="plan_mode",
                    risk_level="low",
                )
            if tool.name in ("WriteFile", "EditFile") and content:
                if self._is_plan_file(content):
                    return Decision(
                        effect="allow",
                        reason="Plan mode: plan file write",
                        source="plan_mode",
                        risk_level="low",
                    )

        # Layer 1: safe read-only commands (auto-allow)
        if tool.category == "command" and is_safe_command(content or ""):
            return Decision(
                effect="allow",
                reason="Safe read-only command",
                source="safe_command",
                risk_level="low",
            )

        # Layer 1b: dangerous command blacklist (Bash only)
        if tool.category == "command":
            hit, reason = self.detector.detect(content)
            if hit:
                return Decision(
                    effect="deny",
                    reason=f"危险命令拦截: {reason}",
                    source="dangerous_detector",
                    risk_level="high",
                    matched_rule=reason,
                    details=[content],
                )

        # Layer 2: path sandbox (file tools only)
        if tool.category in ("read", "write") and content:
            ok, reason = self.sandbox.check(content)
            if not ok:
                return Decision(
                    effect="deny",
                    reason=f"路径沙箱拦截: {reason}",
                    source="sandbox",
                    risk_level="high" if tool.category == "write" else "medium",
                    details=[content],
                )

        # Layer 3: rule engine
        rule_result = self.rule_engine.evaluate(tool.name, content)
        if rule_result == "allow":
            return Decision(
                effect="allow",
                reason="权限规则放行",
                source="rule_engine",
                risk_level="low",
                matched_rule=f"{tool.name}({content})",
            )
        if rule_result == "deny":
            return Decision(
                effect="deny",
                reason="权限规则拒绝",
                source="rule_engine",
                risk_level="high",
                matched_rule=f"{tool.name}({content})",
            )

        # Layer 4: permission mode
        effect = mode_decide(self.mode, tool.category)
        if effect == "allow":
            return Decision(
                effect="allow",
                reason=f"权限模式 {self.mode.value} 放行",
                source="permission_mode",
                risk_level="low",
            )
        if effect == "deny":
            return Decision(
                effect="deny",
                reason=f"权限模式 {self.mode.value} 拒绝",
                source="permission_mode",
                risk_level="medium",
            )

        # Layer 5: ASK → triggers HITL
        return Decision(
            effect="ask",
            reason="需要用户确认",
            source="permission_mode",
            risk_level="medium" if tool.category == "write" else "low",
        )


    def _is_plan_file(self, target_path: str) -> bool:
        if not self.plan_file_path or not target_path:
            return ".codeyx/plans/" in target_path
        try:
            abs_target = os.path.abspath(target_path)
            abs_plan = os.path.abspath(self.plan_file_path)
            if abs_target == abs_plan:
                return True
        except Exception:
            pass
        if os.path.basename(target_path) == os.path.basename(self.plan_file_path):
            return True
        return ".codeyx/plans/" in target_path
