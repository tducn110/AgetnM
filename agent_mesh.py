#!/usr/bin/env python3
"""
AgentMesh CLI: Command-Line Interface & Interactive Terminal Dashboard
Orchestrates AI Agent Mesh (AGY, Codex, Shell) via the AgentMesh Server API.
Supports 6 Terminal Slots, FR-CLI-01 Managed Session vs Manual PTY,
Capability Registry 0-token routing, and Independent Verification Reports.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
import argparse
import select
import termios
import tty

API_BASE = "http://127.0.0.1:8420"


def is_server_reachable():
    try:
        req = urllib.request.Request(f"{API_BASE}/api/status", method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as res:
            return res.status == 200
    except Exception:
        return False


def api_get(endpoint):
    req = urllib.request.Request(f"{API_BASE}{endpoint}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        return {"success": False, "error": str(e)}


def api_post(endpoint, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        return {"success": False, "error": str(e)}


def render_bar(current, max_val, length=18):
    if not max_val or max_val <= 0:
        return "[------------------] 0.0%"
    pct = min(1.0, max(0.0, current / max_val))
    filled = int(length * pct)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {pct*100:5.1f}% ({current:,} / {max_val:,} tokens)"


def print_dashboard():
    os.system("clear")
    status_data = api_get("/api/status")

    if not status_data or (not status_data.get("service_alive") and not status_data.get("herdr_alive")):
        print("\033[1;31m[!] AgentMesh Server is not running on http://127.0.0.1:8420\033[0m")
        print("    Run `./launch_desktop.sh` or `python3 server.py &` to start the daemon.")
        return

    state = status_data.get("state", {})
    agents = state.get("agents", {})
    wires = state.get("wires", [])
    logs = state.get("activity_logs", [])

    print("\033[1;36m" + "═" * 88 + "\033[0m")
    print("\033[1;34m        ⚡ AGENT MESH STUDIO — 6-TERMINAL ORCHESTRATOR & GOVERNANCE ⚡\033[0m")
    print("\033[1;36m" + "═" * 88 + "\033[0m\n")

    # Evaluation Gate & Telemetry Summary
    reg_data = api_get("/api/registry")
    if reg_data.get("success"):
        eval_info = reg_data.get("default_evaluation", {})
        is_live = eval_info.get("should_live_evaluate", False)
        cost_badge = "\033[1;33m[LIVE EVAL: 1,500 TOKENS]\033[0m" if is_live else "\033[1;32m[0 LLM TOKENS (RULE ROUTED)]\033[0m"
        selected_agent = eval_info.get("selected_agent", "agy:gemini-3.8-flash")
        triggers = ", ".join(eval_info.get("trigger_reasons", [])) or "None (Healthy benchmarks)"
        print(f"\033[1;33m[CAPABILITY GATE TELEMETRY]:\033[0m Routing Gate: {cost_badge}")
        print(f"  • Preferred Target: \033[1;36m{selected_agent}\033[0m  |  Triggers: \033[1;37m{triggers}\033[0m")
        print("\033[1;36m" + "─" * 88 + "\033[0m\n")

    if not agents:
        print("  \033[1;33mNo active agent panes registered.\033[0m\n")
    else:
        for pid, ag in sorted(agents.items()):
            status = ag.get("status", "IDLE").upper()
            status_color = "\033[1;32m" if status == "IDLE" else "\033[1;33m" if status in ["WORKING", "TURN_RUNNING"] else "\033[1;31m"
            used_tokens = ag.get("estimated_tokens", 0)
            max_tokens = ag.get("max_context", 200000)
            turns = ag.get("turns_used", 0)
            max_turns = ag.get("max_turns", 10)
            compacts = ag.get("compaction_count", 0)
            mode = ag.get("execution_mode", "MANAGED_SESSION")
            perm = ag.get("permission_profile", "restricted")

            mode_badge = "\033[1;34m[MANAGED JSON]\033[0m" if mode == "MANAGED_SESSION" else "\033[1;33m[MANUAL PTY]\033[0m"
            perm_badge = "\033[1;32m[PRIVILEGED]\033[0m" if perm == "implementer" else "\033[1;36m[TOOL PROXY]\033[0m"

            needs_compact = (used_tokens >= (max_tokens * (ag.get("compact_threshold_pct", 70) / 100.0))) or (turns >= max_turns)
            compact_badge = "\033[1;31m[COMPACT NEEDED]\033[0m" if needs_compact else "\033[1;32m[HEALTHY]\033[0m"

            print(f"\033[1;37m┌─ {ag.get('name', 'agent').upper()} \033[0m({pid}) ── Role: \033[1;36m{ag.get('role', 'Worker')}\033[0m ── Model: \033[1;34m{ag.get('model', 'CLI')}\033[0m")
            print(f"│  • Mode   : {mode_badge}  |  Perm: {perm_badge}  |  Sandbox: {ag.get('sandbox', 'ToolProxySandbox')}")
            print(f"│  • Status : {status_color}{status}\033[0m  |  Memory: {compact_badge}  |  Compacted: {compacts} times")
            print(f"│  • Context: {render_bar(used_tokens, max_tokens)}")
            print(f"│  • Turns  : {turns} / {max_turns} turns (Compaction threshold: {ag.get('compact_threshold_pct', 70)}%)")
            print(f"\033[1;37m└───────────────────────────────────────────────────────────────────────────────────────────\033[0m\n")

    print("\033[1;33m[ACTIVE PIPELINE WIRES]:\033[0m")
    if not wires:
        print("  (No wires established)")
    else:
        for w in wires:
            print(f"  • \033[1;32m[{w.get('id')}]\033[0m {w.get('name')} \033[1;35m({w.get('from_pane')} ──▶ {w.get('to_pane')})\033[0m | Filter: \033[1;36m{w.get('filter')}\033[0m")
    print()

    print("\033[1;33m[RECENT ACTIVITY]:\033[0m")
    for l in logs[:4]:
        lvl_color = "\033[1;32m" if l.get("level") == "success" else "\033[1;31m" if l.get("level") == "error" else "\033[1;34m"
        print(f"  [{l.get('time')}] {lvl_color}{l.get('msg')}\033[0m")
    print("\033[1;36m" + "─" * 88 + "\033[0m")
    print("\033[1;37m[CONTROLS]:\033[0m \033[1;32m[p]\033[0m Prompt  \033[1;32m[c]\033[0m Compact  \033[1;32m[t]\033[0m Transmit  \033[1;32m[m]\033[0m Mode  \033[1;32m[v]\033[0m Verify  \033[1;32m[g]\033[0m EvalGate  \033[1;31m[e]\033[0m Stop  \033[1;32m[r]\033[0m Refresh  \033[1;32m[q]\033[0m Quit")


def get_key():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())
        r, _, _ = select.select([sys.stdin], [], [], 2.5)
        if r:
            return sys.stdin.read(1)
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def interactive_loop():
    while True:
        print_dashboard()
        print("\nCommand [p/c/t/m/v/g/s/e/r/q]: ", end="", flush=True)
        key = get_key()
        if key in ['q', '\x03']:
            print("\nExiting AgentMesh.")
            break
        elif key == 'p':
            print("\nEnter Target Pane ID (e.g. w2:p1): ", end="", flush=True)
            pid = input().strip()
            print("Enter Prompt: ", end="", flush=True)
            prompt = input().strip()
            if pid and prompt:
                res = api_post("/api/prompt", {"pane_id": pid, "prompt": prompt})
                print(f"Result: {res}")
                time.sleep(1.5)
        elif key == 'c':
            print("\nEnter Pane ID to Compact (e.g. w2:p1): ", end="", flush=True)
            pid = input().strip()
            if pid:
                res = api_post("/api/compact", {"pane_id": pid})
                print(f"Result: {res}")
                time.sleep(1.5)
        elif key == 't':
            print("\nEnter Wire ID to Transmit (e.g. wire-1): ", end="", flush=True)
            wid = input().strip()
            if wid:
                res = api_post("/api/transmit", {"wire_id": wid})
                print(f"Result: {res}")
                time.sleep(1.5)
        elif key == 'm':
            print("\nEnter Pane ID to Toggle Mode (e.g. w2:p1): ", end="", flush=True)
            pid = input().strip()
            print("Select Mode [1: MANAGED_SESSION, 2: MANUAL_PTY]: ", end="", flush=True)
            choice = input().strip()
            mode = "MANUAL_PTY" if choice == "2" else "MANAGED_SESSION"
            if pid:
                res = api_post("/api/mode", {"pane_id": pid, "mode": mode})
                print(f"Mode Switch: {res}")
                time.sleep(1.5)
        elif key == 'v':
            print("\nEnter Task ID to Verify (e.g. T-001): ", end="", flush=True)
            tid = input().strip() or "T-001"
            res = api_post("/api/verify", {
                "task_id": tid,
                "receipt": {"outcome": "completed", "duration": 4.5, "output": "Tests passed, 0 failures"},
                "criteria": ["Automated test passes", "No regression"]
            })
            print(f"Verification Verdict: {json.dumps(res, indent=2)}")
            time.sleep(2.5)
        elif key == 'g':
            print("\nEnter Task Class to Route (default: coding.frontend.react): ", end="", flush=True)
            tc = input().strip() or "coding.frontend.react"
            res = api_post("/api/evaluate", {"task_class": tc, "capabilities": ["react", "typescript"]})
            print(f"Evaluation: {json.dumps(res, indent=2)}")
            time.sleep(2.5)
        elif key == 's':
            print("\nSelect kind [agy/codex/shell]: ", end="", flush=True)
            kind = input().strip() or "agy"
            print("Select role: ", end="", flush=True)
            role = input().strip() or "Worker"
            res = api_post("/api/spawn", {"kind": kind, "role": role})
            print(f"Spawned: {res}")
            time.sleep(1.5)
        elif key == 'e':
            res = api_post("/api/emergency_stop", {})
            print(f"Emergency stop: {res}")
            time.sleep(1.5)
        elif key == 'r':
            continue


def main():
    parser = argparse.ArgumentParser(description="AgentMesh Orchestrator CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Dashboard command
    subparsers.add_parser("dashboard", help="Launch interactive TUI dashboard")

    # Status command
    subparsers.add_parser("status", help="Query live agent and wire status")

    # Prompt command
    prompt_parser = subparsers.add_parser("prompt", help="Send a prompt to an agent pane")
    prompt_parser.add_argument("pane_id", help="Target pane ID (e.g. w2:p1)")
    prompt_parser.add_argument("text", help="Prompt text")

    # Compact command
    compact_parser = subparsers.add_parser("compact", help="Trigger memory compaction on an agent")
    compact_parser.add_argument("pane_id", help="Target pane ID (e.g. w2:p1)")

    # Transmit command
    transmit_parser = subparsers.add_parser("transmit", help="Transmit data along a wire")
    transmit_parser.add_argument("wire_id", help="Wire ID (e.g. wire-1)")
    transmit_parser.add_argument("--override", help="Optional payload override", default=None)

    # Mode command (FR-CLI-01)
    mode_parser = subparsers.add_parser("mode", help="Switch pane execution mode (MANAGED_SESSION / MANUAL_PTY)")
    mode_parser.add_argument("pane_id", help="Target pane ID (e.g. w2:p1)")
    mode_parser.add_argument("mode", choices=["MANAGED_SESSION", "MANUAL_PTY"], help="Execution mode")

    # Verify command (Decoupled independent verification)
    verify_parser = subparsers.add_parser("verify", help="Generate independent VerificationReport")
    verify_parser.add_argument("task_id", help="Task ID (e.g. T-001)")
    verify_parser.add_argument("--criteria", help="Comma-separated criteria", default="Reproduction test passes,No regression")
    verify_parser.add_argument("--outcome", default="completed", choices=["completed", "failed"])

    # Evaluate command (Capability registry 0-token routing gate)
    eval_parser = subparsers.add_parser("evaluate", help="Query EvaluationGate for task route")
    eval_parser.add_argument("--task-class", default="coding.frontend.react", help="Task signature class")
    eval_parser.add_argument("--capabilities", nargs="*", default=["react", "typescript"], help="Required capabilities")

    # Registry command
    subparsers.add_parser("registry", help="Print capability registry benchmark data")

    # Spawn command
    spawn_parser = subparsers.add_parser("spawn", help="Spawn a new agent pane")
    spawn_parser.add_argument("kind", choices=["agy", "codex", "shell"], default="agy")
    spawn_parser.add_argument("--role", default="Worker", help="Assigned role")

    # Wires command
    subparsers.add_parser("wires", help="List all configured wires")

    args = parser.parse_args()

    if not args.command or args.command == "dashboard":
        interactive_loop()
    elif args.command == "status":
        res = api_get("/api/status")
        print(json.dumps(res, indent=2))
    elif args.command == "prompt":
        res = api_post("/api/prompt", {"pane_id": args.pane_id, "prompt": args.text})
        print(json.dumps(res, indent=2))
    elif args.command == "compact":
        res = api_post("/api/compact", {"pane_id": args.pane_id})
        print(json.dumps(res, indent=2))
    elif args.command == "transmit":
        payload = {"wire_id": args.wire_id}
        if args.override:
            payload["payload_override"] = args.override
        res = api_post("/api/transmit", payload)
        print(json.dumps(res, indent=2))
    elif args.command == "mode":
        res = api_post("/api/mode", {"pane_id": args.pane_id, "mode": args.mode})
        print(json.dumps(res, indent=2))
    elif args.command == "verify":
        crit_list = [c.strip() for c in args.criteria.split(",") if c.strip()]
        res = api_post("/api/verify", {
            "task_id": args.task_id,
            "receipt": {"outcome": args.outcome, "duration": 5.0, "output": "Exit code 0, checks passed"},
            "criteria": crit_list
        })
        print(json.dumps(res, indent=2))
    elif args.command == "evaluate":
        res = api_post("/api/evaluate", {"task_class": args.task_class, "capabilities": args.capabilities})
        print(json.dumps(res, indent=2))
    elif args.command == "registry":
        res = api_get("/api/registry")
        print(json.dumps(res, indent=2))
    elif args.command == "spawn":
        res = api_post("/api/spawn", {"kind": args.kind, "role": args.role})
        print(json.dumps(res, indent=2))
    elif args.command == "wires":
        res = api_get("/api/wires")
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
