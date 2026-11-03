#!/usr/bin/env python3
"""
AgentMesh Core Backend Server (Dual-Plane Option A)
Implements Event-Driven Control Plane with Native CLI Adapters (AGY stream-json, Codex exec/app-server)
and Real-Time Display Plane with SSE streaming and Monad UI aesthetics.
"""

import os
import sys
import json
import time
import subprocess
import threading
import queue
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Import Core Architecture Components
from core.lifecycle import AgentState, LifecycleEventType, create_wire_envelope
from core.adapters import AgyNativeAdapter, CodexNativeAdapter, ShellNativeAdapter
from core.orchestrator import MultiAgentMeshOrchestrator

PORT = 8420
STATE_FILE = "/home/pro/.config/agent-mesh/app_state.json"

# SSE Real-time Streaming Subscriber List
sse_clients = []
sse_lock = threading.Lock()

def broadcast_sse(event_type, payload):
    with sse_lock:
        msg = f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
        dead = []
        for q in sse_clients:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for d in dead:
            if d in sse_clients:
                sse_clients.remove(d)

# Initialize Global Orchestrator with SSE Broadcasting
orchestrator = MultiAgentMeshOrchestrator(sse_broadcast_fn=broadcast_sse)

class MeshRequestHandler(BaseHTTPRequestHandler):
    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.serve_static("index.html", "text/html; charset=utf-8")
        elif path == "/api/status":
            self.handle_api_status()
        elif path == "/api/wires":
            state = orchestrator.load_state()
            self.respond_json({"success": True, "wires": state.get("wires", [])})
        elif path == "/api/read_pane":
            query = parse_qs(parsed.query)
            pane_id = query.get("pane_id", ["w2:p1"])[0]
            self.handle_api_read_pane(pane_id)
        elif path == "/api/preview_wire":
            query = parse_qs(parsed.query)
            wire_id = query.get("wire_id", [""])[0]
            self.handle_api_preview_wire(wire_id)
        elif path == "/api/stream":
            self.handle_api_stream()
        elif path == "/api/registry":
            self.respond_json({
                "success": True,
                "registry": orchestrator.registry.records,
                "default_evaluation": orchestrator.eval_gate.evaluate_task_route("coding.frontend.react", ["react", "typescript"])
            })
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {}

        if path == "/api/prompt":
            self.handle_api_prompt(payload)
        elif path == "/api/compact":
            self.handle_api_compact(payload)
        elif path == "/api/spawn":
            self.handle_api_spawn(payload)
        elif path == "/api/quota":
            self.handle_api_quota(payload)
        elif path == "/api/permission":
            self.handle_api_permission(payload)
        elif path == "/api/approve":
            self.handle_api_approve(payload)
        elif path == "/api/transmit":
            self.handle_api_transmit(payload)
        elif path == "/api/wires":
            self.handle_api_save_wire(payload)
        elif path == "/api/update_wire":
            self.handle_api_update_wire(payload)
        elif path == "/api/delete_wire":
            self.handle_api_delete_wire(payload)
        elif path == "/api/update_agent":
            self.handle_api_update_agent(payload)
        elif path == "/api/kill_agent":
            self.handle_api_kill_agent(payload)
        elif path == "/api/clear_pane":
            self.handle_api_clear_pane(payload)
        elif path == "/api/clear_logs":
            self.handle_api_clear_logs(payload)
        elif path == "/api/emergency_stop":
            self.handle_api_emergency_stop(payload)
        elif path == "/api/evaluate":
            task_class = payload.get("task_class", "coding.frontend.react")
            capabilities = payload.get("capabilities", ["react", "typescript"])
            is_live = payload.get("live", False)
            if is_live:
                decision = orchestrator.eval_gate.run_live_evaluation(orchestrator, task_class, capabilities)
            else:
                decision = orchestrator.eval_gate.evaluate_task_route(task_class, capabilities)
            self.respond_json({"success": True, "evaluation": decision})
        elif path == "/api/verify":
            task_id = payload.get("task_id", "T-01")
            receipt = payload.get("receipt", {})
            criteria = payload.get("criteria", ["Reproduction test passes", "No regression"])
            artifacts = payload.get("artifacts", None)
            report = orchestrator.verify_task_execution(task_id, receipt, criteria, artifacts)
            self.respond_json({"success": True, "report": report})
        elif path == "/api/mode":
            pane_id = payload.get("pane_id")
            mode = payload.get("mode", "MANAGED_SESSION")
            ok = orchestrator.switch_execution_mode(pane_id, mode)
            self.respond_json({"success": ok, "mode": mode})
        else:
            self.send_error(404, "Not Found")

    def serve_static(self, filename, content_type):
        dir_path = os.path.join(os.path.dirname(__file__), "static")
        file_path = os.path.join(dir_path, filename)
        if not os.path.exists(file_path):
            self.send_error(404, f"File {filename} not found")
            return
        with open(file_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_cors()
        self.end_headers()
        self.wfile.write(data)

    def handle_api_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_cors()
        self.end_headers()

        q = queue.Queue(maxsize=200)
        with sse_lock:
            sse_clients.append(q)

        try:
            # Push initial status update
            state = orchestrator.load_state()
            init_msg = f"event: status_update\ndata: {json.dumps({'state': state})}\n\n"
            self.wfile.write(init_msg.encode('utf-8'))
            self.wfile.flush()

            # Push current buffer snapshots
            for pid, buf in orchestrator.output_buffers.items():
                chunk_msg = f"event: pane_chunk\ndata: {json.dumps({'pane_id': pid, 'output': buf})}\n\n"
                self.wfile.write(chunk_msg.encode('utf-8'))
            self.wfile.flush()

            while True:
                try:
                    msg = q.get(timeout=15.0)
                    self.wfile.write(msg.encode('utf-8'))
                    self.wfile.flush()
                except queue.Empty:
                    # Keepalive ping
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except Exception:
            pass
        finally:
            with sse_lock:
                if q in sse_clients:
                    sse_clients.remove(q)

    def handle_api_status(self):
        state = orchestrator.load_state()
        agents_live = {}
        panes_raw = []

        for pid, adapter in orchestrator.adapters.items():
            st_val = adapter.state.value.lower()
            agents_live[pid] = {
                "pane_id": pid,
                "agent": adapter.name,
                "agent_status": st_val
            }
            panes_raw.append({
                "pane_id": pid,
                "agent": adapter.name,
                "agent_status": st_val
            })

        self.respond_json({
            "service_alive": True,
            "herdr_alive": True,
            "panes_count": len(orchestrator.adapters),
            "agents_live": agents_live,
            "panes_raw": panes_raw,
            "state": state
        })

    def handle_api_read_pane(self, pane_id):
        text = orchestrator.output_buffers.get(pane_id, "No output buffer.")
        self.respond_json({"success": True, "output": text})

    def handle_api_preview_wire(self, wire_id):
        state = orchestrator.load_state()
        wires = state.get("wires", [])
        wire = next((w for w in wires if w["id"] == wire_id), None)
        if not wire:
            self.respond_json({"success": False, "error": "Wire not found"})
            return

        from_pane = wire["from_pane"]
        source_adapter = orchestrator.adapters.get(from_pane)
        last_turn = source_adapter.turn_history[-1] if (source_adapter and source_adapter.turn_history) else None
        raw_text = last_turn.output if last_turn else orchestrator.output_buffers.get(from_pane, "No recent turn output.")

        envelope = create_wire_envelope(
            run_id=f"run_{int(time.time())}",
            turn_id=last_turn.turn_id if last_turn else f"t_{int(time.time()*1000)%10000}",
            source_agent=from_pane,
            target_agent=wire["to_pane"],
            channel=wire.get("type", "spec_directive"),
            status="turn_completed",
            output=raw_text,
            artifacts=last_turn.artifacts if last_turn else []
        )

        formatted = (
            f"// [AGENTMESH ENVELOPE: {wire.get('name')}]\n"
            f"// Run: {envelope['run_id']} | Turn: {envelope['turn_id']} | Route: {from_pane} ──▶ {wire['to_pane']}\n"
            f"// Status: {envelope['status']}\n\n"
            f"{raw_text}"
        )

        self.respond_json({
            "success": True,
            "wire": wire,
            "raw_text": raw_text,
            "formatted_payload": formatted
        })

    def handle_api_prompt(self, payload):
        pane_id = payload.get("pane_id")
        prompt = payload.get("prompt", "").strip()
        if not pane_id or not prompt:
            self.respond_json({"success": False, "error": "Missing pane_id or prompt"})
            return

        if pane_id not in orchestrator.adapters:
            self.respond_json({"success": False, "error": f"Agent {pane_id} not registered"})
            return

        def async_turn():
            try:
                orchestrator.submit_prompt(pane_id, prompt)
            except Exception as e:
                print(f"[Turn Execution Error] {e}")

        threading.Thread(target=async_turn, daemon=True).start()
        self.respond_json({"success": True, "message": f"Turn initiated for {pane_id}"})

    def handle_api_transmit(self, payload):
        wire_id = payload.get("wire_id")
        override_payload = payload.get("payload_override")
        res = orchestrator.transmit_wire(wire_id, override_payload)
        self.respond_json(res)

    def handle_api_compact(self, payload):
        pane_id = payload.get("pane_id")
        if not pane_id:
            self.respond_json({"success": False, "error": "Missing pane_id"})
            return

        success = orchestrator.safe_compact(pane_id)
        if success:
            self.respond_json({"success": True, "message": f"Compacted {pane_id}"})
        else:
            self.respond_json({"success": False, "error": f"Compaction deferred: {pane_id} is busy or already compacting"})

    def handle_api_spawn(self, payload):
        kind = payload.get("kind", "codex")
        role = payload.get("role", "Worker")

        # Determine next available pane ID
        existing_indices = []
        for pid in orchestrator.adapters.keys():
            if ":p" in pid:
                try:
                    existing_indices.append(int(pid.split(":p")[-1]))
                except Exception:
                    pass
        next_idx = (max(existing_indices) + 1) if existing_indices else 1
        new_pane_id = f"w2:p{next_idx}"

        if kind == "agy":
            model = "Gemini 3.8 Flash (High)"
            adapter = AgyNativeAdapter(new_pane_id, "agy", role, model)
        elif kind == "codex":
            model = "gpt-5.6-luna (v0.155.0)"
            adapter = CodexNativeAdapter(new_pane_id, "codex", role, model)
        else:
            model = "Subprocess Shell"
            adapter = ShellNativeAdapter(new_pane_id, kind, role, model)

        orchestrator.adapters[new_pane_id] = adapter
        orchestrator.output_buffers[new_pane_id] = f"{kind.upper()} Native Stream Session Initialized.\n"

        state = orchestrator.load_state()
        state["agents"][new_pane_id] = {
            "name": kind.lower(),
            "role": role,
            "model": model,
            "max_context": 1048576 if kind == "agy" else 200000,
            "compact_threshold_pct": 70,
            "max_turns": 12 if kind == "agy" else 8,
            "turns_used": 0,
            "estimated_tokens": 12000,
            "compaction_count": 0,
            "sandbox": "WorkspaceOnly",
            "auto_approve_safe": True,
            "status": "IDLE",
            "system_prompt": ""
        }
        orchestrator.save_state(state)
        orchestrator.sync_state_and_broadcast()
        self.respond_json({"success": True, "pane_id": new_pane_id})

    def handle_api_kill_agent(self, payload):
        pane_id = payload.get("pane_id")
        if pane_id in orchestrator.adapters:
            del orchestrator.adapters[pane_id]
            orchestrator.output_buffers.pop(pane_id, None)
            state = orchestrator.load_state()
            state.get("agents", {}).pop(pane_id, None)
            # Remove wires referencing this pane
            state["wires"] = [w for w in state.get("wires", []) if w.get("from_pane") != pane_id and w.get("to_pane") != pane_id]
            orchestrator.save_state(state)
            orchestrator.sync_state_and_broadcast()
        self.respond_json({"success": True})

    def handle_api_clear_pane(self, payload):
        pane_id = payload.get("pane_id")
        if pane_id:
            orchestrator.output_buffers[pane_id] = f"// Terminal output cleared at {time.strftime('%H:%M:%S')}\n"
            broadcast_sse("pane_chunk", {"pane_id": pane_id, "output": orchestrator.output_buffers[pane_id]})
        self.respond_json({"success": True})

    def handle_api_clear_logs(self, payload):
        state = orchestrator.load_state()
        state["activity_logs"] = []
        orchestrator.save_state(state)
        orchestrator.sync_state_and_broadcast()
        self.respond_json({"success": True})

    def handle_api_emergency_stop(self, payload):
        # Gracefully mark adapters as interrupted
        for pid, adapter in orchestrator.adapters.items():
            adapter.state = AgentState.IDLE
        state = orchestrator.load_state()
        state["activity_logs"].insert(0, {
            "time": time.strftime("%H:%M:%S"),
            "level": "warning",
            "msg": "🛑 EMERGENCY STOP: Interrupted active agent pipelines"
        })
        orchestrator.save_state(state)
        orchestrator.sync_state_and_broadcast()
        self.respond_json({"success": True, "message": "All pipelines halted"})

    def handle_api_quota(self, payload):
        pane_id = payload.get("pane_id")
        state = orchestrator.load_state()
        if pane_id and pane_id in state.get("agents", {}):
            if "compact_threshold_pct" in payload:
                state["agents"][pane_id]["compact_threshold_pct"] = int(payload["compact_threshold_pct"])
            if "max_turns" in payload:
                state["agents"][pane_id]["max_turns"] = int(payload["max_turns"])
        if "max_session_cost_usd" in payload:
            state["quotas"]["max_session_cost_usd"] = float(payload["max_session_cost_usd"])
        if "auto_compact_enabled" in payload:
            state["quotas"]["auto_compact_enabled"] = bool(payload["auto_compact_enabled"])
        orchestrator.save_state(state)
        orchestrator.sync_state_and_broadcast()
        self.respond_json({"success": True})

    def handle_api_permission(self, payload):
        pane_id = payload.get("pane_id")
        state = orchestrator.load_state()
        if pane_id and pane_id in state.get("agents", {}):
            if "sandbox" in payload:
                state["agents"][pane_id]["sandbox"] = payload["sandbox"]
            if "auto_approve_safe" in payload:
                state["agents"][pane_id]["auto_approve_safe"] = bool(payload["auto_approve_safe"])
        orchestrator.save_state(state)
        orchestrator.sync_state_and_broadcast()
        self.respond_json({"success": True})

    def handle_api_approve(self, payload):
        self.respond_json({"success": True})

    def handle_api_save_wire(self, payload):
        state = orchestrator.load_state()
        wires = state.get("wires", [])
        new_wire = {
            "id": f"wire-{int(time.time()) % 1000}",
            "from_pane": payload.get("from_pane"),
            "to_pane": payload.get("to_pane"),
            "type": payload.get("type", "spec_directive"),
            "name": payload.get("name", "Custom Wire"),
            "color": payload.get("color", "#3b82f6"),
            "description": payload.get("description", "Custom Pipeline Channel")
        }
        wires.append(new_wire)
        state["wires"] = wires
        orchestrator.save_state(state)
        orchestrator.sync_state_and_broadcast()
        self.respond_json({"success": True, "wire": new_wire})

    def handle_api_update_wire(self, payload):
        wire_id = payload.get("wire_id")
        state = orchestrator.load_state()
        wires = state.get("wires", [])
        wire = next((w for w in wires if w["id"] == wire_id), None)
        if wire:
            if "name" in payload: wire["name"] = payload["name"]
            if "description" in payload: wire["description"] = payload["description"]
            if "type" in payload: wire["type"] = payload["type"]
            orchestrator.save_state(state)
            orchestrator.sync_state_and_broadcast()
            self.respond_json({"success": True})
        else:
            self.respond_json({"success": False, "error": "Wire not found"})

    def handle_api_delete_wire(self, payload):
        wire_id = payload.get("wire_id")
        state = orchestrator.load_state()
        state["wires"] = [w for w in state.get("wires", []) if w["id"] != wire_id]
        orchestrator.save_state(state)
        orchestrator.sync_state_and_broadcast()
        self.respond_json({"success": True})

    def handle_api_update_agent(self, payload):
        pane_id = payload.get("pane_id")
        name = payload.get("name")
        role = payload.get("role")
        model = payload.get("model")
        system_prompt = payload.get("system_prompt") or payload.get("custom_prompt")

        success = orchestrator.update_agent_config(
            pane_id=pane_id,
            name=name,
            role=role,
            model=model,
            system_prompt=system_prompt
        )
        if success:
            self.respond_json({"success": True})
        else:
            self.respond_json({"success": False, "error": f"Agent {pane_id} not found"})

    def respond_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)

def auto_compaction_guard_loop():
    """
    Background daemon continuously evaluating the Safe Compaction Predicate:
    CanCompact = (state == IDLE) and (pid not in active_compactions)
    Safely compacts context limits without interrupting active tool executions.
    """
    print("Safe Context Compactor Daemon started.")
    while True:
        try:
            time.sleep(15)
            state = orchestrator.load_state()
            if not state.get("quotas", {}).get("auto_compact_enabled", True):
                continue

            for pid, ag in list(state.get("agents", {}).items()):
                max_ctx = ag.get("max_context", 200000)
                used_tokens = ag.get("estimated_tokens", 0)
                threshold_pct = ag.get("compact_threshold_pct", 70)
                turns_used = ag.get("turns_used", 0)
                max_turns = ag.get("max_turns", 10)

                token_pct = (used_tokens / max_ctx) * 100
                if turns_used >= max_turns or token_pct >= threshold_pct:
                    # Enforce safety predicate
                    orchestrator.safe_compact(pid)

        except Exception as e:
            print(f"[Compaction Guard Error] {e}")

def main():
    # 1. Initialize state synchronization
    orchestrator.sync_state_and_broadcast()

    # 2. Start safe compaction guard thread
    threading.Thread(target=auto_compaction_guard_loop, daemon=True).start()

    # 3. Launch ThreadingHTTPServer for high-performance non-blocking streaming
    server = ThreadingHTTPServer(("127.0.0.1", PORT), MeshRequestHandler)
    print(f"AgentMesh Server running on http://127.0.0.1:{PORT} (Option A: Dual-Plane Architecture)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
