# Agent Mesh — Harness Runtime Architecture Specification (H01–H07)

**Document Status:** REQUIREMENTS BASELINE / ARCHITECTURE SPECIFICATION  
**Version:** 1.1.0  
**Group:** Harness Runtime  
**Status:** IMPLEMENTED & VERIFIED  
**Reference Model:** DeepSeek Harness (DSH) & Cordis-style Capability Seams  

---

## 1. Executive Summary & Problem Statement

Agent Mesh previously implemented control-plane orchestration, terminal multiplexing, and process adapters for external CLIs (`agy`, `codex`, `shell`). However, it lacked a formal **Agent Harness** layer.

In a pure CLI-Native model, the agent's internal loop (`Model → Tool Call → Tool Execution → Next Step`), tool scoping, context assembly, and session persistence are opaque black boxes managed entirely by the external binary. 

The **Harness Runtime** establishes a pluggable, modular agent foundation for Agent Mesh based on the principle of **Capability Seams**:
> *Service Definition, Capability Provider, and Consumer Tool Schema must be decoupled so that adapters, sandboxes, tools, skills, and model backends can be swapped without modifying the Orchestrator or the Agent Loop.*

---

## 2. Dual-Runtime Architecture

Agent Mesh explicitly supports and segregates two execution paradigms without nesting loops:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        AGENT MESH CONTROL PLANE                        │
│   (Whiteboard, Wire Routing, Capability Evaluation, Independent QA)    │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
              ┌────────────────────┴────────────────────┐
              ▼                                         ▼
   ┌───────────────────────┐                 ┌───────────────────────┐
   │  CLI-NATIVE RUNTIME   │                 │ HARNESS-NATIVE RUNTIME│
   │  (AGY / Codex / Shell)│                 │   (DSH-Style Mesh)    │
   ├───────────────────────┤                 ├───────────────────────┤
   │ Process-Level Adapter │                 │ Custom Agent Loop     │
   │ Stream-JSON Wire PTY  │                 │ Prompt/Context Assembly│
   │ Tool Proxy Intercept  │                 │ Scoped Tool Registry  │
   │ Binary-Owned Loop     │                 │ Event-Sourced Store   │
   └──────────┬────────────┘                 └──────────┬────────────┘
              │                                         │
              └────────────────────┬────────────────────┘
                                   ▼
                      NORMALIZED RUNTIME EVENTS
      (TurnStarted, ToolInvoked, StepUpdate, TurnCompleted)
                                   │
                                   ▼
                       EVENT-SOURCED SESSION STORE
                        (Append-Only Replayable)
```

---

## 3. The 7 Harness Mechanisms (H01–H07)

| ID  | Mechanism | Purpose in Agent Mesh | Status |
| :-- | :--- | :--- | :---: |
| **H01** | **Plugin / Capability Seam** | Strict 3-way separation: Service Definition, Capability Provider, and Consumer Tool Schema. | VERIFIED |
| **H02** | **Agent Loop & Turn Lifecycle** | Full cycle: `Prompt → Model Call → Tool Call → Provider Exec → Tool Result → Next Step → Turn Completion`. | VERIFIED |
| **H03** | **Scoped Tool Execution** | Enforce tool whitelisting and path containment checks at the tool boundary before execution. | VERIFIED |
| **H04** | **Event-Sourced Session Store** | Append-only `events.jsonl` with monotonic sequence IDs, projective state recovery, and replay verification. | VERIFIED |
| **H05** | **Prompt & Context Assembler** | Assembles system prompts, execution policy constraints, active skills, and worktree context per agent scope. | VERIFIED |
| **H06** | **Skill Registry & Lazy Loader** | Discovers `SKILL.md` bundles, parses frontmatter metadata, and lazy-loads instructions only when triggered. | VERIFIED |
| **H07** | **Compaction & Token Metering** | Measures prompt, model, and tool delta tokens; projects compacted summaries when context thresholds are crossed. | VERIFIED |

---

## 4. Capability Seam Design (H01)

```
[Service Definition] (Contract: input types, output types, error schema)
         ▲
         │ implements
[Capability Provider] (LocalFsProvider, SandboxedFsProvider, SandboxedShellProvider)
         ▲
         │ binds to
[Consumer Tool Schema] (JSON Schema exposed to LLM: "fs.read_file", "fs.write_file", "shell.exec")
```

- **Pluggability:** To run an agent in Docker, Kubernetes, or a temporary chroot, only the `CapabilityProvider` is replaced. The Agent Loop, tool definitions, and system prompts remain completely unchanged.

---

## 5. Scoped Tool Execution (H03)

Every agent possesses an `ExecutionScope`:
```python
@dataclass
class ExecutionScope:
    agent_id: str
    role: str
    allowed_tools: Set[str]        # Whitelisted tool IDs (e.g., {"fs.read_file", "mesh.read_spec"})
    allowed_paths: List[str]       # Canonical workspace boundaries
    read_only: bool                # Disallow all write operations
    network_access: bool           # Disallow network egress
```

- **Tool Whitelist Enforcement:** If an agent attempts to invoke a tool not present in `allowed_tools`, execution is aborted immediately with `ToolScopeSecurityViolation`.
- **Workspace Boundary Enforcement:** For filesystem and shell tools, all target paths are canonicalized (`os.path.realpath`). Targets outside `allowed_paths` trigger a security violation.

---

## 6. Event-Sourced Session Log (H04)

Sessions are persisted as immutable append-only event streams (`~/.config/agent-mesh/sessions/<session_id>/events.jsonl`):
- Events contain: `seq` (monotonic integer), `event_id`, `timestamp`, `session_id`, `agent_id`, `turn_id`, `event_type`, `payload`.
- **State Projection:** Agent turn history, active artifacts, token consumption, and status are derived by folding over the event stream.
- **Crash Recovery & Replay:** When Agent Mesh restarts, replaying events up to sequence $N$ guarantees that completed turns and active tasks are recovered with zero state drift or false completion.

---

## 7. Acceptance Criteria for Harness Implementation

1. **Planner Tool Isolation:** An agent configured as Planner (Restricted) cannot call write tools (e.g. `fs.write_file` is not in its tool definitions and blocked at tool boundary).
2. **Implementer Worktree Isolation:** An Implementer agent can call `fs.write_file` inside its designated worktree (`.agentmesh/worktrees/impl-a`), but any attempt to write outside that worktree is blocked with a path boundary violation.
3. **Skill Security Boundary:** Skills can provide specialized instructions, but cannot escalate an agent's `ExecutionScope`.
4. **Tool Result Persistence:** Tool executions and tool results are recorded in the session's event log.
5. **Replay Invariance:** Replaying the event stream from disk reconstructs the identical turn history and state without invoking external model APIs.
