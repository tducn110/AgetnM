"""
H06: Skill Registry & Lazy Loader
Discovers SKILL.md bundles, parses frontmatter metadata, and lazy-loads instructions without privilege escalation.
"""

from typing import Dict, Any, List, Optional
import os
import re
from dataclasses import dataclass, field
from .tools import ExecutionScope


@dataclass
class SkillMetadata:
    skill_id: str
    name: str
    description: str
    triggers: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    file_path: str = ""
    is_lazy_loaded: bool = True


class SkillRegistry:
    """
    Central discovery and management for Agent Skills.
    Supports lazy loading of markdown instructions and RBAC compatibility checks.
    """
    def __init__(self, search_paths: Optional[List[str]] = None):
        self.search_paths = search_paths or [
            os.path.abspath(".agentmesh/skills"),
            os.path.expanduser("~/.gemini/skills"),
            os.path.expanduser("~/.gemini/config/skills")
        ]
        self.skills: Dict[str, SkillMetadata] = {}
        self._instructions_cache: Dict[str, str] = {}
        self.discover_skills()

    def discover_skills(self):
        """Scans search paths for SKILL.md files and parses metadata headers."""
        self.skills.clear()
        for base_path in self.search_paths:
            if not os.path.isdir(base_path):
                continue
            for root, dirs, files in os.walk(base_path):
                if "SKILL.md" in files:
                    skill_file = os.path.join(root, "SKILL.md")
                    meta = self._parse_skill_file(skill_file)
                    if meta:
                        self.skills[meta.skill_id] = meta

    def _parse_skill_file(self, file_path: str) -> Optional[SkillMetadata]:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(4096)  # Read header up to 4KB

            # Parse YAML frontmatter between --- and ---
            match = re.search(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
            skill_id = os.path.basename(os.path.dirname(file_path))
            name = skill_id
            description = "No description provided."
            triggers = []
            required_tools = []

            if match:
                fm = match.group(1)
                for line in fm.split("\n"):
                    line = line.strip()
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip('"\'')
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip('"\'')
                    elif line.startswith("tools:"):
                        raw_tools = line.split(":", 1)[1].strip()
                        required_tools = [t.strip().strip('"\'[]') for t in raw_tools.split(",") if t.strip()]

            return SkillMetadata(
                skill_id=skill_id,
                name=name,
                description=description,
                triggers=triggers,
                required_tools=required_tools,
                file_path=file_path,
                is_lazy_loaded=True
            )
        except Exception:
            return None

    def register_in_memory_skill(
        self,
        skill_id: str,
        name: str,
        description: str,
        instructions: str,
        required_tools: Optional[List[str]] = None
    ):
        """Allows registering dynamic or test skills programmatically."""
        meta = SkillMetadata(
            skill_id=skill_id,
            name=name,
            description=description,
            required_tools=required_tools or [],
            file_path="",
            is_lazy_loaded=False
        )
        self.skills[skill_id] = meta
        self._instructions_cache[skill_id] = instructions

    def get_skill_catalog_summary(self, allowed_skills: Optional[List[str]] = None) -> str:
        """Returns a concise markdown list of skills for system prompt context injection."""
        items = []
        for sid, skill in sorted(self.skills.items()):
            if allowed_skills is not None and sid not in allowed_skills:
                continue
            items.append(f"- **{skill.name}** (`{sid}`): {skill.description}")
        return "\n".join(items) if items else "No skills registered."

    def load_skill_instructions(self, skill_id: str) -> Optional[str]:
        """Lazy-loads the full markdown body of the skill."""
        if skill_id in self._instructions_cache:
            return self._instructions_cache[skill_id]

        meta = self.skills.get(skill_id)
        if not meta or not meta.file_path or not os.path.exists(meta.file_path):
            return None

        try:
            with open(meta.file_path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            # Strip YAML frontmatter if present
            body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", raw, flags=re.DOTALL).strip()
            self._instructions_cache[skill_id] = body
            return body
        except Exception:
            return None

    def check_skill_permission(self, skill_id: str, scope: ExecutionScope) -> bool:
        """
        Guarantees that invoking a skill cannot escalate permissions.
        Every tool required by the skill must already be in scope.allowed_tools.
        """
        meta = self.skills.get(skill_id)
        if not meta:
            return False
        for req_tool in meta.required_tools:
            if req_tool not in scope.allowed_tools:
                return False
        return True
