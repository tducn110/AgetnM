"""
H05: Prompt & Context Assembler
Assembles system instructions, workspace context, scoped tool schemas, and active skills into a cohesive execution context.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from .tools import ExecutionScope, ScopedToolRegistry
from .skills import SkillRegistry
from .events import ProjectedSessionState


@dataclass
class AssembledContext:
    system_prompt: str
    user_prompt: str
    scoped_tools: List[Dict[str, Any]] = field(default_factory=list)
    attached_skills: List[str] = field(default_factory=list)


class ContextAssembler:
    """
    Builds the scoped context for each turn, preventing context pollution and unauthorized capability exposure.
    """
    def __init__(
        self,
        tool_registry: ScopedToolRegistry,
        skill_registry: SkillRegistry
    ):
        self.tool_registry = tool_registry
        self.skill_registry = skill_registry

    def assemble(
        self,
        scope: ExecutionScope,
        raw_prompt: str,
        session_state: Optional[ProjectedSessionState] = None,
        workspace_dir: Optional[str] = None,
        system_prompt_override: Optional[str] = None
    ) -> AssembledContext:
        # 1. Base Role & Identity
        lines = [
            f"# Agent Role: {scope.role} (Identifier: {scope.agent_id})",
            "You are an AI Agent running within the AgentMesh Harness Runtime."
        ]

        # 2. Execution Policy & Scope Enforcement Notice
        if scope.read_only:
            lines.append("\n> [!IMPORTANT]\n> POLICY: You are operating in RESTRICTED READ-ONLY mode. You cannot modify files or execute mutations.")
        else:
            lines.append(f"\n> POLICY: You are operating in WORKSPACE mode. Permitted paths: {scope.allowed_paths}")

        # 3. Workspace Context
        if workspace_dir:
            lines.append(f"\n## Active Workspace Directory\n`{workspace_dir}`")

        # 4. Scoped Tool Definitions
        scoped_tool_schemas = self.tool_registry.get_scoped_tools(scope)
        tool_dicts = [t.to_openai_tool() for t in scoped_tool_schemas]
        if scoped_tool_schemas:
            lines.append(f"\n## Available Scoped Tools ({len(scoped_tool_schemas)} tools permitted):")
            for t in scoped_tool_schemas:
                lines.append(f"- `{t.tool_name}`: {t.description}")
        else:
            lines.append("\n## Available Scoped Tools\nNone. You are in conversational deliberation mode only.")

        # 5. Skills Assembly & Lazy Loading Check
        skill_catalog = self.skill_registry.get_skill_catalog_summary()
        lines.append(f"\n## Available Skills Catalog\n{skill_catalog}")

        attached_skills = []
        # Detect if user prompt triggers any skill
        for skill_id, meta in self.skill_registry.skills.items():
            is_triggered = any(trig.lower() in raw_prompt.lower() for trig in meta.triggers) or (skill_id.lower() in raw_prompt.lower())
            if is_triggered:
                # Check permission: does the skill require tools outside scope?
                if self.skill_registry.check_skill_permission(skill_id, scope):
                    instructions = self.skill_registry.load_skill_instructions(skill_id)
                    if instructions:
                        lines.append(f"\n### Active Skill Instructions: [{meta.name}]\n{instructions}")
                        attached_skills.append(skill_id)
                else:
                    lines.append(f"\n> [!WARNING]\n> Skill '{meta.name}' was requested but rejected: requires tools not in agent scope.")

        # 6. Session History Projection
        if session_state and session_state.history:
            lines.append("\n## Session History")
            for t in session_state.history[-3:]:  # Last 3 turns
                lines.append(f"- User: {t.get('prompt')[:120]}")
                lines.append(f"  Agent: {t.get('output')[:120]}")

        # Custom system prompt injection
        if system_prompt_override:
            lines.append(f"\n## Additional Directives\n{system_prompt_override}")

        assembled_system = "\n".join(lines)
        return AssembledContext(
            system_prompt=assembled_system,
            user_prompt=raw_prompt,
            scoped_tools=tool_dicts,
            attached_skills=attached_skills
        )
