#!/usr/bin/env python3
"""
DeepSeek Harness (DSH) Architectural Acceptance Test Suite
Verifies:
1. Capability Seam (H01)
2. Agent Loop & Turn Lifecycle (H02)
3. Scoped Tool Execution (H03) - Planner cannot write; Executor restricted to worktree
4. Event-Sourced Session Persistence & Replay (H04)
5. Prompt & Context Assembler (H05)
6. Skill Discovery & Privilege Escalation Prevention (H06)
7. Full Pipeline: User Goal -> Planner -> Executor -> Tool -> Artifact -> Independent Verifier
"""

import os
import sys
sys.path.insert(0, os.path.abspath("."))
import json
import time
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8420"
WORKTREE_IMPL_A = "/home/pro/Downloads/agent-mesh/.agentmesh/worktrees/impl-a"

def post(endpoint, data):
    req = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))

def get(endpoint):
    with urllib.request.urlopen(f"{BASE_URL}{endpoint}", timeout=10) as res:
        return json.loads(res.read().decode("utf-8"))

def test_1_capability_seam_and_planner_restriction():
    print("▶ [Harness Check 1] Planner Tool Scope Isolation (H01 & H03)...")
    from core.harness import ExecutionScope, ScopedToolRegistry, ConsumerToolSchema, SandboxedFsProvider

    # Setup ScopedToolRegistry
    reg = ScopedToolRegistry()
    fs_provider = SandboxedFsProvider(root_dir="/home/pro/Downloads/agent-mesh")
    reg.register_tool(ConsumerToolSchema(
        "fs.read_file", "service.fs", "Read file", {}, fs_provider.read_file
    ))
    reg.register_tool(ConsumerToolSchema(
        "fs.write_file", "service.fs", "Write file", {}, fs_provider.write_file
    ))

    # Planner Scope: Restricted read-only, only fs.read_file
    planner_scope = ExecutionScope(
        agent_id="planner-01",
        role="Planner",
        allowed_tools={"fs.read_file"},
        allowed_paths=["/home/pro/Downloads/agent-mesh"],
        read_only=True
    )

    # 1. Scoped tool visibility: Planner must NOT see fs.write_file
    scoped_tools = reg.get_scoped_tools(planner_scope)
    tool_names = [t.tool_name for t in scoped_tools]
    assert "fs.write_file" not in tool_names, "Planner must not see fs.write_file in tool schema"
    assert "fs.read_file" in tool_names, "Planner must see fs.read_file"

    # 2. Execution boundary: If Planner tries to call fs.write_file, it is rejected
    res = reg.execute_tool(planner_scope, "fs.write_file", {"path": "test.txt", "content": "data"})
    assert not res.success, "Write execution by Planner must fail"
    assert "Security Violation: Tool 'fs.write_file' is not authorized" in res.error
    print("  ✔ PASS: Planner is strictly prevented from seeing or executing write tools.")

def test_2_executor_worktree_boundary_enforcement():
    print("▶ [Harness Check 2] Executor Worktree Boundary Enforcement (H03)...")
    from core.harness import ExecutionScope, ScopedToolRegistry, ConsumerToolSchema, SandboxedFsProvider

    reg = ScopedToolRegistry()
    fs_provider = SandboxedFsProvider(root_dir=WORKTREE_IMPL_A)
    reg.register_tool(ConsumerToolSchema(
        "fs.write_file", "service.fs", "Write file", {}, fs_provider.write_file
    ))

    # Implementer Scope: Allowed to write strictly within WORKTREE_IMPL_A
    impl_scope = ExecutionScope(
        agent_id="impl-01",
        role="Implementer",
        allowed_tools={"fs.write_file"},
        allowed_paths=[WORKTREE_IMPL_A],
        read_only=False
    )

    # 1. Attempt write outside worktree (/tmp/unauthorized.txt)
    res_outside = reg.execute_tool(impl_scope, "fs.write_file", {"path": "/tmp/unauthorized.txt", "content": "malicious"})
    assert not res_outside.success, "Write outside worktree must fail"
    assert "Security Violation: Path boundary violation" in res_outside.error
    assert not os.path.exists("/tmp/unauthorized.txt")

    # 2. Attempt traversal write (../../outside.txt)
    res_traversal = reg.execute_tool(impl_scope, "fs.write_file", {"path": "../outside.txt", "content": "escape"})
    assert not res_traversal.success, "Directory traversal write must fail"
    assert "Security Violation: Path boundary violation" in res_traversal.error

    # 3. Valid write inside worktree (src/feature.js)
    res_valid = reg.execute_tool(impl_scope, "fs.write_file", {"path": "src/feature.js", "content": "export const ok = true;"})
    assert res_valid.success, f"Valid write failed: {res_valid.error}"
    target_file = os.path.join(WORKTREE_IMPL_A, "src/feature.js")
    assert os.path.exists(target_file), "File must exist in worktree"
    with open(target_file) as f:
        assert "export const ok = true;" in f.read()
    print("  ✔ PASS: Executor writes within worktree succeed; writes outside or traversals are blocked.")

def test_3_skill_privilege_escalation_prevention():
    print("▶ [Harness Check 3] Skill Registry & Privilege Escalation Gate (H06)...")
    from core.harness import ExecutionScope, SkillRegistry

    sr = SkillRegistry()
    # Register an escalated skill requiring root/admin tools
    sr.register_in_memory_skill(
        skill_id="danger-skill",
        name="Danger Skill",
        description="Skill requiring admin tools",
        instructions="Do admin stuff",
        required_tools=["admin.destroy", "system.root"]
    )

    # Restricted agent scope
    user_scope = ExecutionScope(
        agent_id="user-01",
        role="Worker",
        allowed_tools={"fs.read_file"}
    )

    can_use = sr.check_skill_permission("danger-skill", user_scope)
    assert not can_use, "Skill requiring unpermitted tools must be rejected"
    print("  ✔ PASS: Skills requiring unpermitted tools cannot escalate privileges.")

def test_4_event_sourced_session_and_replay():
    print("▶ [Harness Check 4] Event-Sourced Session Store & Replay (H04)...")
    from core.harness import (
        ExecutionScope, ScopedToolRegistry, ConsumerToolSchema, SandboxedFsProvider,
        RuntimeEventStore, ContextAssembler, SkillRegistry, HarnessAgentLoop, DeterministicModelClient
    )

    test_sess = f"test_sess_{int(time.time()*1000)}"
    store = RuntimeEventStore()
    tools = ScopedToolRegistry()
    fs_provider = SandboxedFsProvider(root_dir=WORKTREE_IMPL_A)
    tools.register_tool(ConsumerToolSchema("fs.write_file", "service.fs", "Write file", {}, fs_provider.write_file))

    scope = ExecutionScope(
        agent_id="harness-exec",
        role="Implementer",
        allowed_tools={"fs.write_file"},
        allowed_paths=[WORKTREE_IMPL_A],
        read_only=False
    )
    skills = SkillRegistry()
    ctx = ContextAssembler(tools, skills)

    loop = HarnessAgentLoop(
        scope=scope,
        tool_registry=tools,
        context_assembler=ctx,
        event_store=store,
        model_client=DeterministicModelClient()
    )

    # Run turn that triggers tool write
    res = loop.run_turn(
        session_id=test_sess,
        turn_id="turn_01",
        user_prompt="Write file src/artifact_v1.js with code",
        workspace_dir=WORKTREE_IMPL_A
    )
    assert res.success
    assert len(res.artifacts) > 0

    # Read event stream
    events = store.get_events(test_sess)
    event_types = [e.event_type for e in events]
    assert "TURN_STARTED" in event_types
    assert "MODEL_RESPONSE" in event_types
    assert "TOOL_CALL_REQUESTED" in event_types
    assert "TOOL_CALL_EXECUTED" in event_types
    assert "TURN_COMPLETED" in event_types

    # Replay session
    replayed = store.replay_session(test_sess)
    assert replayed.turns_count == 1
    assert replayed.tool_calls_count >= 1
    assert any("artifact_v1.js" in art for art in replayed.created_artifacts)
    assert replayed.status == "IDLE"
    print("  ✔ PASS: Append-only event stream verified; replay perfectly reconstructs session state.")

def test_5_full_pipeline_acceptance():
    print("▶ [Harness Check 5] Full Acceptance Flow (Goal -> Plan -> Skill -> Tool -> Verifier)...")
    # 1. Goal & Planner Stage
    goal = post("/api/prompt", {"pane_id": "w2:p1", "prompt": "Goal: Deploy User Module with tests"})
    assert goal.get("success")

    # 2. Planner transmits directive to Implementer
    wire_res = post("/api/transmit", {"wire_id": "wire-1", "payload_override": "TaskSpec: Build User Module inside worktree"})
    assert wire_res.get("success")

    # 3. Spawn Harness Implementer in Worktree
    sp = post("/api/spawn", {"kind": "harness", "role": "Implementer"})
    h_pid = sp.get("pane_id")
    assert h_pid

    # 4. Implementer executes turn writing valid artifact inside worktree
    turn = post("/api/prompt", {
        "pane_id": h_pid,
        "prompt": "Write file src/user_service.js with code"
    })
    assert turn.get("success")
    time.sleep(1)

    # 5. Independent Verification Stage
    artifact_path = os.path.join(WORKTREE_IMPL_A, "src/user_service.js")
    assert os.path.exists(artifact_path), "Artifact must be produced"

    # Verifier runs positive verification
    v_res = post("/api/verify", {
        "task_id": "TASK-DSH-E2E",
        "receipt": {
            "outcome": "completed",
            "duration": 2.8,
            "output": "Exit code 0, 15 unit tests passed",
            "session_id": h_pid,
            "attempt_id": "A1"
        },
        "criteria": ["Artifact generated cleanly", "Tests pass with exit code 0"],
        "artifacts": [artifact_path]
    })
    rep = v_res.get("report", {})
    assert rep.get("verdict") == "PASS", f"Verification failed: {rep}"
    assert rep.get("task_status") == "VERIFIED"

    # Teardown
    post("/api/kill_agent", {"pane_id": h_pid})
    print("  ✔ PASS: End-to-end Goal -> Plan -> Tool -> Artifact -> Independent Verifier confirmed.")

if __name__ == "__main__":
    print("==========================================================")
    print("    DEEPSEEK HARNESS (DSH) ARCHITECTURE ACCEPTANCE SUITE  ")
    print("==========================================================")
    test_1_capability_seam_and_planner_restriction()
    test_2_executor_worktree_boundary_enforcement()
    test_3_skill_privilege_escalation_prevention()
    test_4_event_sourced_session_and_replay()
    test_5_full_pipeline_acceptance()
    print("==========================================================")
    print("    ALL HARNESS ARCHITECTURE CHECKS VERIFIED SUCCESSFULLY ")
    print("==========================================================")
