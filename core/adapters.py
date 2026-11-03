"""
AgentMesh Native Lifecycle Adapters
Implements Event-Driven Control Plane for:
- Antigravity AGY Persistent Process (NDJSON stdin/stdout multi-turn stream)
- OpenAI Codex Adapter (JSONL exec/resume)
- Subprocess Shell & Tool Proxy Runner
"""

import os
import sys
import json
import time
import subprocess
import threading
from typing import Dict, Any, List, Optional, Callable
from .lifecycle import (
    AgentState, LifecycleEventType, AgentLifecycleEvent, TurnResult,
    ExecutionMode, InputProtocol, OutputProtocol
)

import shutil

AGY_BIN = (
    "/home/pro/.local/bin/agy" if os.path.exists("/home/pro/.local/bin/agy")
    else (shutil.which("agy") or "/snap/bin/agy")
)
CODEX_BIN = "/home/pro/.local/bin/codex"


class BaseAgentAdapter:
    def __init__(
        self,
        agent_id: str,
        name: str,
        role: str,
        model: str,
        provider: str = "custom",
        execution_mode: ExecutionMode = ExecutionMode.MANAGED_SESSION,
        input_protocol: InputProtocol = InputProtocol.STREAM_JSON,
        output_protocol: OutputProtocol = OutputProtocol.STREAM_JSON,
        workspace_dir: Optional[str] = None,
        permission_policy: Optional[Dict[str, Any]] = None
    ):
        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.model = model
        self.provider = provider
        self.execution_mode = execution_mode
        self.input_protocol = input_protocol
        self.output_protocol = output_protocol
        self.workspace_dir = workspace_dir or os.getcwd()
        self.permission_policy = permission_policy or {
            "role": "restricted",
            "fs_read": True,
            "fs_write": False,
            "process_exec": False,
            "network": "restricted"
        }
        self.system_prompt: Optional[str] = None
        self.state = AgentState.IDLE
        self.session_id: Optional[str] = None
        self.current_turn_id: Optional[str] = None
        self.turn_history: List[TurnResult] = []

    def start(self) -> bool:
        """Starts any persistent process or underlying session."""
        return True

    def send_task(
        self,
        prompt: str,
        delta_callback: Optional[Callable[[str, str], None]] = None,
        event_callback: Optional[Callable[[AgentLifecycleEvent], None]] = None
    ) -> TurnResult:
        """Sends a task / prompt into the agent session and awaits turn completion."""
        raise NotImplementedError

    def execute_turn(
        self,
        prompt: str,
        delta_callback: Optional[Callable[[str, str], None]] = None,
        event_callback: Optional[Callable[[AgentLifecycleEvent], None]] = None
    ) -> TurnResult:
        """Backward-compatible alias for send_task."""
        return self.send_task(prompt, delta_callback, event_callback)

    def cancel(self) -> bool:
        """Interrupts or halts current active turn."""
        self.state = AgentState.IDLE
        return True

    def close(self):
        """Terminates persistent session process cleanly."""
        pass


class PersistentAgySessionAdapter(BaseAgentAdapter):
    """
    Antigravity AGY Persistent Session Adapter.
    Maintains a single long-lived `agy --input-format stream-json --output-format stream-json` process.
    Sends user messages via NDJSON stdin: `{"event": "user", "message": {"content": prompt}}`
    Reads structured events via stdout until `{"event": "result", ...}`.
    Keeps conversation context alive across turns without re-spawning!
    """
    def __init__(
        self,
        agent_id: str = "w2:p1",
        name: str = "agy",
        role: str = "Lead Architect",
        model: str = "Gemini 3.8 Flash (High)",
        workspace_dir: Optional[str] = None,
        permission_policy: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            agent_id=agent_id,
            name=name,
            role=role,
            model=model,
            provider="agy",
            execution_mode=ExecutionMode.MANAGED_SESSION,
            input_protocol=InputProtocol.STREAM_JSON,
            output_protocol=OutputProtocol.STREAM_JSON,
            workspace_dir=workspace_dir,
            permission_policy=permission_policy
        )
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._is_starting = False

    def start(self) -> bool:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return True

            self.state = AgentState.STARTING
            cmd = [
                AGY_BIN,
                "--input-format", "stream-json",
                "--output-format", "stream-json",
                "--dangerously-skip-permissions"
            ]
            if self.workspace_dir and os.path.exists(self.workspace_dir):
                cmd.extend(["--add-dir", self.workspace_dir])

            try:
                self.proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    cwd=self.workspace_dir if os.path.exists(self.workspace_dir) else None
                )

                # Read first line: expect init event
                init_line = self.proc.stdout.readline()
                if init_line:
                    try:
                        init_ev = json.loads(init_line.strip())
                        self.session_id = init_ev.get("conversation_id")
                    except Exception:
                        pass

                self.state = AgentState.IDLE
                return True
            except Exception as e:
                print(f"[{self.agent_id}] Failed to start persistent AGY process: {e}")
                self.state = AgentState.FAILED
                return False

    def send_task(
        self,
        prompt: str,
        delta_callback: Optional[Callable[[str, str], None]] = None,
        event_callback: Optional[Callable[[AgentLifecycleEvent], None]] = None
    ) -> TurnResult:
        with self._lock:
            # Ensure process is alive
            if not self.proc or self.proc.poll() is not None:
                if not self.start():
                    return TurnResult(self.agent_id, "err", False, "Failed to start AGY persistent process", duration=0.0)

            turn_id = f"agy_{int(time.time() * 1000) % 1000000}"
            self.current_turn_id = turn_id
            self.state = AgentState.TURN_RUNNING

            effective_prompt = prompt
            if self.system_prompt and not self.turn_history:
                effective_prompt = f"[System Instructions: {self.system_prompt}]\n\n{prompt}"

            if event_callback:
                event_callback(AgentLifecycleEvent(
                    LifecycleEventType.TURN_STARTED, self.agent_id, turn_id, {"prompt": effective_prompt}
                ))

            # Send NDJSON message to stdin
            user_msg = {
                "event": "user",
                "message": {
                    "content": effective_prompt
                }
            }
            start_time = time.time()
            final_response: List[str] = []
            turn_success = False
            usage_data: Dict[str, Any] = {}
            error_msg = None

            try:
                self.proc.stdin.write(json.dumps(user_msg) + "\n")
                self.proc.stdin.flush()

                # Read lines until result event
                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        break
                    line_str = line.strip()
                    if not line_str or not line_str.startswith("{"):
                        continue
                    try:
                        ev = json.loads(line_str)
                    except Exception:
                        continue

                    ev_type = ev.get("event")

                    if ev_type == "step_update":
                        step = ev.get("step_update", {})
                        delta = step.get("text_delta")
                        if delta:
                            final_response.append(delta)
                            if delta_callback:
                                delta_callback(self.agent_id, delta)
                            if event_callback:
                                event_callback(AgentLifecycleEvent(
                                    LifecycleEventType.STREAM_DELTA, self.agent_id, turn_id, {"delta": delta}
                                ))

                    elif ev_type == "result":
                        res = ev.get("result", {})
                        status = res.get("status")
                        if status == "SUCCESS":
                            turn_success = True
                            resp_text = res.get("response", "").strip()
                            if resp_text and not final_response:
                                final_response.append(resp_text)
                            usage_data = res.get("usage", {})
                        else:
                            error_msg = res.get("error", f"Status: {status}")
                        break

            except Exception as ex:
                error_msg = str(ex)
                turn_success = False

            duration = round(time.time() - start_time, 2)
            full_text = "".join(final_response).strip()

            if not turn_success:
                full_text = full_text or (f"Error: {error_msg}" if error_msg else "Execution failed")
                if delta_callback:
                    delta_callback(self.agent_id, f"\n[AGENT FAILURE: {error_msg or 'Execution failed'}]\n")

            if turn_success:
                self.state = AgentState.TURN_COMPLETED
                if event_callback:
                    event_callback(AgentLifecycleEvent(LifecycleEventType.TURN_COMPLETED, self.agent_id, turn_id, {
                        "output": full_text,
                        "duration": duration,
                        "usage": usage_data
                    }))
            else:
                self.state = AgentState.FAILED
                if event_callback:
                    event_callback(AgentLifecycleEvent(LifecycleEventType.TURN_FAILED, self.agent_id, turn_id, {
                        "error": error_msg or "Execution failed",
                        "duration": duration
                    }))

            res_obj = TurnResult(
                agent_id=self.agent_id,
                turn_id=turn_id,
                success=turn_success,
                output=full_text,
                artifacts=[],
                usage=usage_data,
                duration=duration
            )
            self.turn_history.append(res_obj)
            self.state = AgentState.IDLE
            return res_obj

    def cancel(self) -> bool:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                # Restart the persistent process to clear stuck turn
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=2)
                except Exception:
                    self.proc.kill()
                self.proc = None
            self.state = AgentState.IDLE
            return True

    def close(self):
        with self._lock:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=2)
                except Exception:
                    self.proc.kill()
                self.proc = None
            self.state = AgentState.IDLE


# Keep alias for backward compatibility
AgyNativeAdapter = PersistentAgySessionAdapter


class CodexNativeAdapter(BaseAgentAdapter):
    """
    OpenAI Codex Native Adapter.
    Uses official `codex exec --json` with session continuation via `codex exec resume <thread_id>`.
    Strictly filters out reasoning/thinking deltas from wire transmission.
    """
    def __init__(
        self,
        agent_id: str = "w2:p2",
        name: str = "codex",
        role: str = "Core Implementer",
        model: str = "gpt-5.6-luna (v0.155.0)",
        workspace_dir: Optional[str] = None,
        permission_policy: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            agent_id=agent_id,
            name=name,
            role=role,
            model=model,
            provider="codex",
            execution_mode=ExecutionMode.MANAGED_SESSION,
            input_protocol=InputProtocol.CLI_ARGS,
            output_protocol=OutputProtocol.JSONL,
            workspace_dir=workspace_dir,
            permission_policy=permission_policy
        )

    def send_task(
        self,
        prompt: str,
        delta_callback: Optional[Callable[[str, str], None]] = None,
        event_callback: Optional[Callable[[AgentLifecycleEvent], None]] = None
    ) -> TurnResult:
        turn_id = f"cdx_{int(time.time() * 1000) % 1000000}"
        self.current_turn_id = turn_id
        self.state = AgentState.TURN_RUNNING

        effective_prompt = prompt
        if self.system_prompt and not self.session_id:
            effective_prompt = f"[System Instructions: {self.system_prompt}]\n\n{prompt}"

        if event_callback:
            event_callback(AgentLifecycleEvent(
                LifecycleEventType.TURN_STARTED, self.agent_id, turn_id, {"prompt": effective_prompt}
            ))

        if self.session_id:
            cmd = [
                CODEX_BIN, "exec", "resume", self.session_id,
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
                "--json", effective_prompt
            ]
        else:
            cmd = [
                CODEX_BIN, "exec",
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
                "--json", effective_prompt
            ]

        start_time = time.time()
        final_message = ""
        turn_success = False
        usage_data: Dict[str, Any] = {}
        error_msg = None

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=self.workspace_dir if os.path.exists(self.workspace_dir) else None
            )

            for line in proc.stdout:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue

                ev_type = ev.get("type")
                if ev_type == "thread.started":
                    th_id = ev.get("thread_id")
                    if th_id:
                        self.session_id = th_id
                    if event_callback:
                        event_callback(AgentLifecycleEvent(
                            LifecycleEventType.SESSION_INITIALIZED, self.agent_id, turn_id, {"thread_id": self.session_id}
                        ))

                elif ev_type == "item.completed":
                    item = ev.get("item", {})
                    if item.get("type") == "agent_message":
                        text = item.get("text", "")
                        final_message = text
                        if delta_callback:
                            delta_callback(self.agent_id, text)
                        if event_callback:
                            event_callback(AgentLifecycleEvent(
                                LifecycleEventType.ITEM_COMPLETED, self.agent_id, turn_id, {"text": text}
                            ))

                elif ev_type == "turn.completed":
                    turn_success = True
                    usage_data = ev.get("usage", {})

                elif ev_type == "turn.failed":
                    error_msg = ev.get("error", {}).get("message", "Turn failed")

                elif ev_type == "error":
                    error_msg = ev.get("message", "Codex error")

            proc.wait(timeout=180)
            if not turn_success and not error_msg:
                if proc.returncode != 0:
                    error_msg = f"Codex exited with code {proc.returncode}"
                else:
                    turn_success = True
        except subprocess.TimeoutExpired:
            proc.kill()
            error_msg = "Codex turn timed out after 180 seconds"
        except Exception as ex:
            error_msg = str(ex)

        duration = round(time.time() - start_time, 2)
        if not turn_success:
            final_message = final_message or (f"Error: {error_msg}" if error_msg else "Execution failed")
            if delta_callback:
                delta_callback(self.agent_id, f"\n[CODEX FAILURE: {error_msg or 'Execution failed'}]\n")

        if turn_success:
            self.state = AgentState.TURN_COMPLETED
            if event_callback:
                event_callback(AgentLifecycleEvent(LifecycleEventType.TURN_COMPLETED, self.agent_id, turn_id, {
                    "output": final_message,
                    "duration": duration,
                    "usage": usage_data
                }))
        else:
            self.state = AgentState.FAILED
            if event_callback:
                event_callback(AgentLifecycleEvent(LifecycleEventType.TURN_FAILED, self.agent_id, turn_id, {
                    "error": error_msg or "Turn execution error",
                    "duration": duration
                }))

        res_obj = TurnResult(self.agent_id, turn_id, turn_success, final_message, [], usage_data, duration)
        self.turn_history.append(res_obj)
        self.state = AgentState.IDLE
        return res_obj


class ShellNativeAdapter(BaseAgentAdapter):
    """
    Test Harness & Shell Tool Proxy Runner.
    Runs commands with deterministic exit code, safety timeout, and execution captures.
    """
    def __init__(
        self,
        agent_id: str = "w2:p3",
        name: str = "shell",
        role: str = "Test Harness & Runner",
        model: str = "Subprocess Shell",
        workspace_dir: Optional[str] = None,
        permission_policy: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            agent_id=agent_id,
            name=name,
            role=role,
            model=model,
            provider="shell",
            execution_mode=ExecutionMode.MANAGED_SESSION,
            input_protocol=InputProtocol.CLI_ARGS,
            output_protocol=OutputProtocol.RAW_TEXT,
            workspace_dir=workspace_dir,
            permission_policy=permission_policy or {
                "role": "implementer",
                "fs_read": True,
                "fs_write": True,
                "process_exec": True,
                "network": "allowlist"
            }
        )

    def send_task(
        self,
        prompt: str,
        delta_callback: Optional[Callable[[str, str], None]] = None,
        event_callback: Optional[Callable[[AgentLifecycleEvent], None]] = None
    ) -> TurnResult:
        turn_id = f"sh_{int(time.time() * 1000) % 1000000}"
        self.current_turn_id = turn_id
        self.state = AgentState.TURN_RUNNING

        if event_callback:
            event_callback(AgentLifecycleEvent(
                LifecycleEventType.TURN_STARTED, self.agent_id, turn_id, {"prompt": prompt}
            ))

        start_time = time.time()
        try:
            res = subprocess.run(
                prompt,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=self.workspace_dir if os.path.exists(self.workspace_dir) else None
            )
            success = (res.returncode == 0)
            output = res.stdout if success else (res.stderr or res.stdout or f"Process exited with code {res.returncode}")
        except subprocess.TimeoutExpired:
            success = False
            output = "[TIMEOUT] Shell command exceeded 120s limit"
        except Exception as e:
            success = False
            output = f"[SHELL ERROR] {str(e)}"

        duration = round(time.time() - start_time, 2)

        if delta_callback:
            delta_callback(self.agent_id, output)

        if event_callback:
            ev_type = LifecycleEventType.TURN_COMPLETED if success else LifecycleEventType.TURN_FAILED
            event_callback(AgentLifecycleEvent(ev_type, self.agent_id, turn_id, {
                "output": output,
                "duration": duration,
                "success": success
            }))

        res_obj = TurnResult(self.agent_id, turn_id, success, output, [], {}, duration)
        self.turn_history.append(res_obj)
        self.state = AgentState.IDLE
        return res_obj
