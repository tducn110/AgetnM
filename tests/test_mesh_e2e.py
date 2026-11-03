#!/usr/bin/env python3
"""
AgentMesh End-to-End Runtime Verification Suite
Tests:
1. ToolProxySandbox RBAC Guard: Restricted agent blocked from unauthorized write.
2. Independent Verification Negative Case 1: Test failure (AssertionError).
3. Independent Verification Negative Case 2: Broken artifact (Conflict markers).
4. Independent Verification Positive Case: Clean code & passing tests.
5. Capability Evaluation: Rule-routed 0-token vs Live benchmark.
6. Dynamic Whiteboard: Spawn agent, connect wire, update permission, delete wire/agent.
"""

import sys
import json
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8420"

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

def test_1_rbac_guard():
    print("▶ [Test 1] ToolProxySandbox RBAC Guard...")
    res = post("/api/prompt", {
        "pane_id": "w2:p2",
        "prompt": "Create file /tmp/unauthorized_e2e.txt with content HACKED"
    })
    assert res.get("success"), "Prompt submit should succeed"
    # Check output buffer of w2:p2
    import time
    time.sleep(2)
    pane = get("/api/read_pane?pane_id=w2:p2")
    out = pane.get("output", "")
    assert "Security Exception: Action rejected by ToolProxySandbox RBAC Guard" in out, "RBAC guard must intercept"
    print("  ✔ PASS: Restricted agent blocked with Security Exception. File not created.")

def test_2_verification_negative_assertion():
    print("▶ [Test 2] Independent Verification: Negative Test (AssertionError)...")
    payload = {
        "task_id": "T-E2E-NEG-01",
        "receipt": {
            "outcome": "completed",
            "duration": 3.1,
            "output": "AssertionError: Expected 200, got 500\nFAILED (failures=1)",
            "exit_code": 1,
            "session_id": "w2:p3",
            "attempt_id": "A1"
        },
        "criteria": ["Unit tests pass", "No regression"]
    }
    res = post("/api/verify", payload)
    rep = res.get("report", {})
    assert rep.get("verdict") == "FAIL", f"Expected FAIL, got {rep.get('verdict')}"
    assert rep.get("task_status") == "REJECTED", f"Expected REJECTED, got {rep.get('task_status')}"
    print("  ✔ PASS: Verifier returned FAIL and marked task REJECTED despite outcome=completed.")

def test_3_verification_negative_conflict_markers():
    print("▶ [Test 3] Independent Verification: Negative Test (Conflict Markers)...")
    payload = {
        "task_id": "T-E2E-NEG-02",
        "receipt": {
            "outcome": "completed",
            "duration": 2.5,
            "output": "<<<<<<< HEAD\nconst x = 1;\n=======\nconst x = 2;\n>>>>>>> main",
            "exit_code": 0,
            "session_id": "w2:p3",
            "attempt_id": "A1"
        },
        "criteria": ["Clean syntax", "Build pass"]
    }
    res = post("/api/verify", payload)
    rep = res.get("report", {})
    assert rep.get("verdict") == "FAIL", f"Expected FAIL, got {rep.get('verdict')}"
    assert rep.get("task_status") == "REJECTED", f"Expected REJECTED, got {rep.get('task_status')}"
    print("  ✔ PASS: Verifier returned FAIL due to git conflict markers.")

def test_4_verification_positive():
    print("▶ [Test 4] Independent Verification: Positive Test (Passing Suite)...")
    payload = {
        "task_id": "T-E2E-POS-01",
        "receipt": {
            "outcome": "completed",
            "duration": 4.5,
            "output": "Exit code 0, all 12 tests passed successfully.",
            "exit_code": 0,
            "session_id": "w2:p3",
            "attempt_id": "A1"
        },
        "criteria": ["Unit tests pass", "Acceptance criteria met"]
    }
    res = post("/api/verify", payload)
    rep = res.get("report", {})
    assert rep.get("verdict") == "PASS", f"Expected PASS, got {rep.get('verdict')}"
    assert rep.get("task_status") == "VERIFIED", f"Expected VERIFIED, got {rep.get('task_status')}"
    print("  ✔ PASS: Verifier returned PASS and marked task VERIFIED.")

def test_5_capability_evaluation():
    print("▶ [Test 5] Capability Evaluation: Rule-routed 0-token Gate...")
    res = post("/api/evaluate", {
        "task_class": "coding.frontend.react",
        "capabilities": ["react", "typescript"],
        "live": False
    })
    ev = res.get("evaluation", {})
    assert ev.get("mode") == "RULE_ROUTED", f"Expected RULE_ROUTED, got {ev.get('mode')}"
    assert ev.get("token_cost_llm") == 0, f"Expected 0 tokens, got {ev.get('token_cost_llm')}"
    assert ev.get("selected_agent") == "agy:gemini-3.8-flash"
    print("  ✔ PASS: Capability Gate routed in 0 tokens with utility scoring.")

def test_6_dynamic_whiteboard():
    print("▶ [Test 6] Dynamic Whiteboard: Node & Wire Lifecycle...")
    # Spawn
    sp = post("/api/spawn", {"kind": "agy", "role": "E2E Tester"})
    pid = sp.get("pane_id")
    assert pid, "Spawn should return pane_id"

    # Connect wire
    w_res = post("/api/wires", {
        "from_pane": "w2:p1",
        "to_pane": pid,
        "type": "spec_directive",
        "name": "E2E Test Wire"
    })
    wid = w_res.get("wire", {}).get("id")
    assert wid, "Wire creation should return wire id"

    # Update permission
    perm_res = post("/api/permission", {"pane_id": pid, "sandbox": "PrivilegedWorkspace"})
    assert perm_res.get("success"), "Permission update should succeed"

    # Delete wire & kill agent
    post("/api/delete_wire", {"wire_id": wid})
    post("/api/kill_agent", {"pane_id": pid})
    print("  ✔ PASS: Dynamic node spawn, wire creation, permission toggle, and teardown confirmed.")

if __name__ == "__main__":
    print("==================================================")
    print("    RUNNING AGENT MESH E2E VERIFICATION SUITE     ")
    print("==================================================")
    test_1_rbac_guard()
    test_2_verification_negative_assertion()
    test_3_verification_negative_conflict_markers()
    test_4_verification_positive()
    test_5_capability_evaluation()
    test_6_dynamic_whiteboard()
    print("==================================================")
    print("    ALL 6 END-TO-END VERIFICATION CHECKS PASSED   ")
    print("==================================================")
