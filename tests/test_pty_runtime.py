#!/usr/bin/env python3
"""
Test Suite for AgentMesh PTY Runtime Seam (Step 2 Verification)
Verifies:
1. Two concurrent, isolated real POSIX PTY sessions.
2. Raw byte-level bidirectional I/O.
3. Terminal resize (TIOCSWINSZ).
4. Direct OSC 7501 agent status parsing (\x1b]7501;state=...\x07).
5. Clean process termination and resource cleanup.
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath("."))
from core.pty_runtime import PtySessionConfig, LocalPosixPtyAdapter


def test_concurrent_ptys_and_osc7501():
    print("▶ [PTY Step 2] Spawning 2 independent POSIX PTY terminals...")
    adapter = LocalPosixPtyAdapter()

    out1 = []
    out2 = []

    cfg1 = PtySessionConfig(
        session_id="term-1",
        command=["/bin/bash", "--noprofile", "--norc"],
        cwd="/tmp",
        cols=80,
        rows=24
    )
    cfg2 = PtySessionConfig(
        session_id="term-2",
        command=["/bin/bash", "--noprofile", "--norc"],
        cwd="/tmp",
        cols=100,
        rows=30
    )

    s1 = adapter.spawn(cfg1)
    s2 = adapter.spawn(cfg2)
    assert s1 == "term-1" and s2 == "term-2"

    adapter.attach(s1, lambda chunk: out1.append(chunk))
    adapter.attach(s2, lambda chunk: out2.append(chunk))

    # Send different echo commands to verify isolation
    adapter.write_input(s1, b"echo 'PTY_ALPHA_READY'\n")
    adapter.write_input(s2, b"echo 'PTY_BETA_READY'\n")

    time.sleep(0.3)
    raw1 = b"".join(out1).decode("utf-8", errors="ignore")
    raw2 = b"".join(out2).decode("utf-8", errors="ignore")

    assert "PTY_ALPHA_READY" in raw1, f"Term 1 missing output: {raw1}"
    assert "PTY_BETA_READY" in raw2, f"Term 2 missing output: {raw2}"
    assert "PTY_BETA_READY" not in raw1, "Cross-talk detected! Term 1 saw Term 2 output"
    print("  ✔ PASS: Two independent PTY terminals verified with zero cross-talk.")

    # Test Terminal Resize
    print("▶ [PTY Step 2] Testing terminal resize...")
    ok = adapter.resize(s1, 120, 40)
    assert ok, "Resize failed"
    state1 = adapter.get_state(s1)
    assert state1.cols == 120 and state1.rows == 40
    print("  ✔ PASS: PTY resize to 120x40 acknowledged.")

    # Test OSC 7501 Program-Reported Agent State (not screen scraping!)
    print("▶ [PTY Step 2] Testing OSC 7501 state detection...")
    adapter.write_input(s1, b"printf '\\033]7501;state=working\\007'\n")
    time.sleep(0.2)
    state1 = adapter.get_state(s1)
    assert state1.agent_status == "working", f"Expected 'working', got {state1.agent_status}"
    print(f"  ✔ PASS: OSC 7501 correctly transitioned agent_status to: {state1.agent_status}")

    adapter.write_input(s1, b"printf '\\033]7501;state=done\\007'\n")
    time.sleep(0.2)
    state1 = adapter.get_state(s1)
    assert state1.agent_status == "done", f"Expected 'done', got {state1.agent_status}"
    print(f"  ✔ PASS: OSC 7501 correctly transitioned agent_status to: {state1.agent_status}")

    # Test Clean Termination
    print("▶ [PTY Step 2] Terminating PTY sessions...")
    adapter.terminate(s1)
    adapter.terminate(s2)
    time.sleep(0.1)
    assert not adapter.get_state(s1).alive
    assert not adapter.get_state(s2).alive
    print("  ✔ PASS: All PTY sessions cleanly terminated.")


if __name__ == "__main__":
    test_concurrent_ptys_and_osc7501()
    print("==========================================================")
    print("  ALL PTY RUNTIME STEP 2 VERIFICATION CHECKS PASSED       ")
    print("==========================================================")
