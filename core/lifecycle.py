"""
AgentMesh Core Lifecycle, Execution Contracts & Evaluation Gate
Defines deterministic turn lifecycle states, event envelopes, evaluation triggers,
and formal contracts (GoalSpec, PlanSpec, TaskSpec, ExecutionReceipt, VerificationReport).
"""

import time
import uuid
from typing import Dict, Any, List, Optional
from enum import Enum


class ExecutionMode(str, Enum):
    MANAGED_SESSION = "MANAGED_SESSION"  # stdin/stdout structured JSON stream (automated)
    MANUAL_PTY = "MANUAL_PTY"            # Interactive pseudo-terminal (user direct control)


class InputProtocol(str, Enum):
    STREAM_JSON = "STREAM_JSON"          # NDJSON messages over stdin
    CLI_ARGS = "CLI_ARGS"                # One-off command line arguments
    STDIN_TEXT = "STDIN_TEXT"            # Raw text over stdin


class OutputProtocol(str, Enum):
    STREAM_JSON = "STREAM_JSON"          # NDJSON events over stdout
    JSONL = "JSONL"                      # JSON-Lines over stdout
    RAW_TEXT = "RAW_TEXT"                # Unstructured stdout


class AgentState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    TURN_RUNNING = "TURN_RUNNING"
    WAITING_INPUT = "WAITING_INPUT"
    WAITING_TOOL = "WAITING_TOOL"
    TURN_COMPLETED = "TURN_COMPLETED"
    VERIFYING = "VERIFYING"
    TASK_VERIFIED = "TASK_VERIFIED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class LifecycleEventType(str, Enum):
    SESSION_INITIALIZED = "SESSION_INITIALIZED"
    TURN_STARTED = "TURN_STARTED"
    STREAM_DELTA = "STREAM_DELTA"
    ITEM_COMPLETED = "ITEM_COMPLETED"
    TURN_COMPLETED = "TURN_COMPLETED"
    TURN_FAILED = "TURN_FAILED"
    INPUT_REQUIRED = "INPUT_REQUIRED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"


class AgentLifecycleEvent:
    def __init__(self, event_type: LifecycleEventType, agent_id: str, turn_id: str, data: Dict[str, Any]):
        self.event_type = event_type
        self.agent_id = agent_id
        self.turn_id = turn_id
        self.data = data
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "agent_id": self.agent_id,
            "turn_id": self.turn_id,
            "data": self.data,
            "timestamp": self.timestamp
        }


class TurnResult:
    def __init__(
        self,
        agent_id: str,
        turn_id: str,
        success: bool,
        output: str,
        artifacts: Optional[List[Dict[str, Any]]] = None,
        usage: Optional[Dict[str, Any]] = None,
        duration: float = 0.0,
        diagnostics: Optional[str] = None
    ):
        self.agent_id = agent_id
        self.turn_id = turn_id
        self.success = success
        self.output = output
        self.artifacts = artifacts or []
        self.usage = usage or {}
        self.duration = duration
        self.diagnostics = diagnostics or ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "turn_id": self.turn_id,
            "success": self.success,
            "output": self.output,
            "artifacts": self.artifacts,
            "usage": self.usage,
            "duration": self.duration,
            "diagnostics": self.diagnostics
        }


# --- FORMAL ARTIFACT CONTRACTS ---

def create_goal_spec(
    goal_id: str,
    objective: str,
    constraints: Optional[List[str]] = None,
    acceptance_criteria: Optional[List[str]] = None,
    version: int = 1
) -> Dict[str, Any]:
    return {
        "goal_id": goal_id,
        "version": version,
        "objective": objective,
        "constraints": constraints or [],
        "acceptance_criteria": acceptance_criteria or [],
        "created_at": time.time()
    }


def create_task_spec(
    task_id: str,
    goal_id: str,
    objective: str,
    required_capabilities: List[str],
    permission_profile: str,
    workspace_ref: str,
    acceptance_criteria: List[str],
    depends_on: Optional[List[str]] = None,
    max_turns: int = 10,
    max_tokens: int = 150000
) -> Dict[str, Any]:
    return {
        "task_id": task_id,
        "goal_id": goal_id,
        "objective": objective,
        "required_capabilities": required_capabilities,
        "permission_profile": permission_profile,  # "privileged" or "restricted"
        "workspace_ref": workspace_ref,
        "acceptance_criteria": acceptance_criteria,
        "depends_on": depends_on or [],
        "limits": {
            "max_turns": max_turns,
            "max_tokens": max_tokens,
            "max_wall_time_sec": 300
        },
        "status": "READY"
    }


def create_execution_receipt(
    task_id: str,
    attempt_id: str,
    session_id: str,
    turn_id: str,
    outcome: str,
    output: str,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    claims: Optional[List[str]] = None,
    duration: float = 0.0,
    usage: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return {
        "task_id": task_id,
        "attempt_id": attempt_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "outcome": outcome,  # "completed" | "failed" | "interrupted"
        "output": output,
        "artifacts": artifacts or [],
        "claims": claims or [],
        "duration": duration,
        "usage": usage or {},
        "timestamp": time.time()
    }


def create_verification_report(
    task_id: str,
    attempt_id: str,
    verdict: str,  # "PASS" | "FAIL" | "INCONCLUSIVE"
    checks: List[Dict[str, Any]],
    evaluator: str,
    summary: str
) -> Dict[str, Any]:
    return {
        "task_id": task_id,
        "attempt_id": attempt_id,
        "verdict": verdict,
        "checks": checks,
        "evaluator": evaluator,
        "summary": summary,
        "verified_at": time.time()
    }


def create_wire_envelope(
    run_id: str,
    turn_id: str,
    source_agent: str,
    target_agent: str,
    channel: str,
    status: str,
    output: str,
    artifacts: Optional[List[Any]] = None,
    verification: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Standardized Inter-Agent Communication Contract Envelope.
    Guarantees trace provenance, deduplication, and verification evidence.
    """
    return {
        "run_id": run_id or f"run_{int(time.time())}",
        "turn_id": turn_id or f"turn_{int(time.time() * 1000) % 100000}",
        "channel": channel,
        "source_agent": source_agent,
        "target_agent": target_agent,
        "timestamp": time.time(),
        "status": status,
        "payload": {
            "content": output,
            "artifacts": artifacts or [],
            "verification": verification or {"tests_passed": False, "evidence": "unverified"}
        }
    }


def apply_wire_filter(filter_name: str, raw_text: str) -> str:
    """
    Applies specialized wire channel transformation on inter-agent payloads:
    - extract_spec: Filters and retains architectural specs, requirements, and constraints.
    - extract_diff: Filters and isolates git diffs, patches, and code blocks.
    - extract_errors: Filters and extracts stack traces, compilation errors, and test failures.
    - compact_rollup: Condenses multi-thousand line logs into a concise executive summary.
    """
    if not raw_text or not raw_text.strip():
        return raw_text or ""

    lines = raw_text.strip().split("\n")

    if filter_name == "extract_spec":
        spec_lines = []
        for line in lines:
            if line.startswith(("#", "-", "*", ">")) or any(k in line.lower() for k in ["spec", "require", "goal", "todo", "constraint", "architecture", "acceptance"]):
                spec_lines.append(line)
        return "\n".join(spec_lines) if spec_lines else raw_text

    elif filter_name == "extract_diff":
        diff_lines = []
        in_code_block = False
        for line in lines:
            if line.startswith("```"):
                in_code_block = not in_code_block
                diff_lines.append(line)
            elif in_code_block or line.startswith(("diff --git", "---", "+++", "@@", "+", "-")):
                diff_lines.append(line)
        return "\n".join(diff_lines) if diff_lines else raw_text

    elif filter_name == "extract_errors":
        err_lines = []
        for line in lines:
            lower = line.lower()
            if any(k in lower for k in ["error", "fail", "fatal", "traceback", "exception", "panic", "assert", "exit status", "err"]):
                err_lines.append(line)
        return "\n".join(err_lines) if err_lines else raw_text

    elif filter_name == "compact_rollup":
        if len(lines) <= 8:
            return raw_text
        summary_lines = [
            f"// [Executive Rollup: condensed from {len(lines)} lines / {len(raw_text)} chars]",
            lines[0],
            lines[1] if len(lines) > 1 else "",
            "...",
            f"// Summary: {len(lines)} total lines emitted.",
            lines[-3] if len(lines) > 4 else "",
            lines[-2] if len(lines) > 3 else "",
            lines[-1]
        ]
        return "\n".join(l for l in summary_lines if l)

    return raw_text
