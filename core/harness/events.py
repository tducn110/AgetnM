"""
H04: Event-Sourced Session Store
Maintains append-only immutable event streams and provides projective state replay.
"""

from typing import Dict, Any, List, Optional
import os
import json
import time
import threading
from dataclasses import dataclass, field, asdict

DEFAULT_SESSIONS_DIR = os.path.expanduser("~/.config/agent-mesh/sessions")


@dataclass
class SessionEvent:
    seq: int
    event_id: str
    timestamp: float
    session_id: str
    agent_id: str
    turn_id: str
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionEvent":
        return cls(**data)


@dataclass
class ProjectedSessionState:
    session_id: str
    agent_id: str
    turns_count: int = 0
    total_tokens: int = 0
    tool_calls_count: int = 0
    status: str = "IDLE"
    created_artifacts: List[str] = field(default_factory=list)
    last_event_seq: int = 0
    last_output: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)


class RuntimeEventStore:
    """
    Append-only event log for agent sessions.
    Every turn transition, model call, tool request, and compaction is persisted.
    """
    def __init__(self, base_dir: str = DEFAULT_SESSIONS_DIR):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._seq_counters: Dict[str, int] = {}

    def _get_session_file(self, session_id: str) -> str:
        sess_dir = os.path.join(self.base_dir, session_id)
        os.makedirs(sess_dir, exist_ok=True)
        return os.path.join(sess_dir, "events.jsonl")

    def append_event(
        self,
        session_id: str,
        agent_id: str,
        turn_id: str,
        event_type: str,
        payload: Dict[str, Any]
    ) -> SessionEvent:
        with self._lock:
            if session_id not in self._seq_counters:
                # Initialize seq counter by counting existing lines
                events = self._read_all(session_id)
                self._seq_counters[session_id] = events[-1].seq if events else 0

            self._seq_counters[session_id] += 1
            seq = self._seq_counters[session_id]
            evt_id = f"evt_{int(time.time()*1000)}_{seq}"

            event = SessionEvent(
                seq=seq,
                event_id=evt_id,
                timestamp=time.time(),
                session_id=session_id,
                agent_id=agent_id,
                turn_id=turn_id,
                event_type=event_type,
                payload=payload
            )

            file_path = self._get_session_file(session_id)
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict()) + "\n")
                f.flush()

            return event

    def _read_all(self, session_id: str) -> List[SessionEvent]:
        file_path = self._get_session_file(session_id)
        if not os.path.exists(file_path):
            return []
        events = []
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    try:
                        events.append(SessionEvent.from_dict(json.loads(line_str)))
                    except Exception:
                        continue
        return events

    def get_events(self, session_id: str, since_seq: int = 0) -> List[SessionEvent]:
        events = self._read_all(session_id)
        return [e for e in events if e.seq > since_seq]

    def replay_session(self, session_id: str) -> ProjectedSessionState:
        """
        Pure function: Folds over the append-only event stream to reconstruct the exact session state.
        Ensures replay invariance across restarts.
        """
        events = self._read_all(session_id)
        if not events:
            return ProjectedSessionState(session_id=session_id, agent_id="unknown")

        agent_id = events[0].agent_id
        state = ProjectedSessionState(session_id=session_id, agent_id=agent_id)

        current_turn: Optional[Dict[str, Any]] = None

        for evt in events:
            state.last_event_seq = evt.seq
            t = evt.event_type
            p = evt.payload

            if t == "SESSION_CREATED":
                state.status = "IDLE"

            elif t == "TURN_STARTED":
                state.status = "TURN_RUNNING"
                state.turns_count += 1
                current_turn = {
                    "turn_id": evt.turn_id,
                    "prompt": p.get("prompt", ""),
                    "tool_calls": [],
                    "output": "",
                    "success": False
                }

            elif t == "TOOL_CALL_EXECUTED":
                state.tool_calls_count += 1
                if current_turn:
                    current_turn["tool_calls"].append({
                        "tool": p.get("tool"),
                        "args": p.get("args"),
                        "success": p.get("success"),
                        "output": p.get("output")
                    })
                # Check for created artifacts
                if p.get("tool") == "fs.write_file" and p.get("success"):
                    art_path = p.get("args", {}).get("path")
                    if art_path and art_path not in state.created_artifacts:
                        state.created_artifacts.append(art_path)

            elif t == "MODEL_RESPONSE":
                toks = p.get("usage", {}).get("total_tokens", 0)
                state.total_tokens += toks
                if current_turn:
                    current_turn["output"] = p.get("response", "")
                state.last_output = p.get("response", "")

            elif t == "TURN_COMPLETED":
                state.status = "IDLE"
                if current_turn:
                    current_turn["success"] = p.get("success", True)
                    current_turn["duration"] = p.get("duration", 0.0)
                    state.history.append(current_turn)
                    current_turn = None

            elif t == "TURN_FAILED":
                state.status = "FAILED"
                if current_turn:
                    current_turn["success"] = False
                    current_turn["error"] = p.get("error")
                    state.history.append(current_turn)
                    current_turn = None

            elif t == "SESSION_COMPACTED":
                # Compaction summarizes older turns
                compaction_info = p.get("summary", "")
                state.history = [{
                    "turn_id": "compacted_summary",
                    "prompt": "[Compacted Historical Context]",
                    "output": compaction_info,
                    "success": True
                }]

        return state
