"""
H03: Scoped Tool Execution Pipeline & Policy Enforcement
Enforces agent tool whitelisting and boundary checks at the tool boundary.
"""

from typing import Dict, Any, List, Set, Optional
import os
import time
from dataclasses import dataclass, field
from .seam import ConsumerToolSchema


class ToolScopeSecurityViolation(Exception):
    """Raised when an agent attempts to execute an unpermitted tool or breach boundaries."""
    pass


@dataclass
class ExecutionScope:
    """RBAC Scope assigned to a specific agent instance."""
    agent_id: str
    role: str
    allowed_tools: Set[str] = field(default_factory=set)
    allowed_paths: List[str] = field(default_factory=list)
    read_only: bool = False
    network_access: bool = False

    def can_access_path(self, target_path: str, is_write: bool = False) -> bool:
        if is_write and self.read_only:
            return False
        if not self.allowed_paths:
            return False
        if os.path.isabs(target_path):
            real_target = os.path.realpath(target_path)
        else:
            real_target = os.path.realpath(os.path.join(self.allowed_paths[0], target_path))

        for base in self.allowed_paths:
            real_base = os.path.realpath(base)
            if real_target.startswith(real_base):
                return True
        return False


@dataclass
class ToolExecutionResult:
    tool_name: str
    success: bool
    output: Any
    error: Optional[str] = None
    duration: float = 0.0


class ScopedToolRegistry:
    """
    Central repository of capability tools.
    Filters available tool definitions and executes tools strictly against the agent's ExecutionScope.
    """
    def __init__(self):
        self.tools: Dict[str, ConsumerToolSchema] = {}

    def register_tool(self, tool: ConsumerToolSchema):
        self.tools[tool.tool_name] = tool

    def get_scoped_tools(self, scope: ExecutionScope) -> List[ConsumerToolSchema]:
        """Returns only the tool schemas that are explicitly in the agent's allowed_tools whitelist."""
        return [
            tool for tool_name, tool in self.tools.items()
            if tool_name in scope.allowed_tools
        ]

    def execute_tool(
        self,
        scope: ExecutionScope,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> ToolExecutionResult:
        start_time = time.time()

        # Gate 1: Tool Whitelist Verification
        if tool_name not in scope.allowed_tools:
            err = (
                f"Security Violation: Tool '{tool_name}' is not authorized for agent [{scope.agent_id} ({scope.role})]. "
                f"Allowed tools: {sorted(list(scope.allowed_tools))}"
            )
            return ToolExecutionResult(tool_name, False, None, error=err, duration=0.0)

        # Gate 2: Tool existence check
        tool = self.tools.get(tool_name)
        if not tool:
            return ToolExecutionResult(tool_name, False, None, error=f"Tool '{tool_name}' not found in registry", duration=0.0)

        # Gate 3: Path boundary checks for filesystem tools
        if "path" in arguments:
            raw_path = arguments["path"]
            is_write_tool = ("write" in tool_name or "delete" in tool_name or "modify" in tool_name)
            resolved_path = raw_path if os.path.isabs(raw_path) else os.path.join(scope.allowed_paths[0], raw_path) if scope.allowed_paths else raw_path
            if not scope.can_access_path(resolved_path, is_write=is_write_tool):
                err = (
                    f"Security Violation: Path boundary violation. Agent [{scope.agent_id}] attempted to "
                    f"{'write to' if is_write_tool else 'access'} '{raw_path}', which lies outside authorized boundaries: {scope.allowed_paths}"
                )
                return ToolExecutionResult(tool_name, False, None, error=err, duration=round(time.time() - start_time, 3))
            arguments["path"] = resolved_path

        # Gate 4: Execute handler
        try:
            res = tool.handler_fn(**arguments)
            duration = round(time.time() - start_time, 3)
            return ToolExecutionResult(tool_name, True, res, error=None, duration=duration)
        except Exception as e:
            duration = round(time.time() - start_time, 3)
            return ToolExecutionResult(tool_name, False, None, error=str(e), duration=duration)
