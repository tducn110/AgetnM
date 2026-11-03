"""
Agent Mesh Harness Runtime Package
Implements the 7 core harness mechanisms (H01-H07) modeled after DeepSeek Harness (DSH):
- H01: Capability Seam (Service, Provider, Consumer)
- H02: Agent Loop & Turn Lifecycle
- H03: Scoped Tool Execution & RBAC Boundary
- H04: Event-Sourced Session Store & Replay
- H05: Prompt & Context Assembler
- H06: Skill Registry & Discovery
- H07: Token Metering & Compaction
"""

from .seam import ServiceDefinition, CapabilityProvider, ConsumerToolSchema, SandboxedFsProvider, SandboxedShellProvider
from .tools import ExecutionScope, ScopedToolRegistry, ToolScopeSecurityViolation, ToolExecutionResult
from .events import SessionEvent, RuntimeEventStore, ProjectedSessionState
from .skills import SkillMetadata, SkillRegistry
from .context import ContextAssembler, AssembledContext
from .loop import HarnessAgentLoop, ModelClient, DeterministicModelClient, HarnessNativeAdapter

__all__ = [
    "ServiceDefinition",
    "CapabilityProvider",
    "ConsumerToolSchema",
    "ExecutionScope",
    "ScopedToolRegistry",
    "ToolScopeSecurityViolation",
    "ToolExecutionResult",
    "SessionEvent",
    "RuntimeEventStore",
    "ProjectedSessionState",
    "SkillMetadata",
    "SkillRegistry",
    "ContextAssembler",
    "AssembledContext",
    "HarnessAgentLoop",
    "ModelClient",
    "DeterministicModelClient",
    "HarnessNativeAdapter"
]
