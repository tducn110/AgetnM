"""
H02: Agent Loop & Turn Lifecycle
Manages the Model -> Tool Call -> Tool Result -> Next Step cycle for Harness-Native agents.
Provides HarnessNativeAdapter that integrates into Agent Mesh Orchestrator.
"""

from typing import Dict, Any, List, Optional
import time
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

from .tools import ExecutionScope, ScopedToolRegistry, ToolExecutionResult
from .events import RuntimeEventStore, SessionEvent, ProjectedSessionState
from .context import ContextAssembler, AssembledContext
from ..adapters import BaseAgentAdapter, AgentState, TurnResult, AgentLifecycleEvent, LifecycleEventType


@dataclass
class ModelToolCall:
    tool_name: str
    arguments: Dict[str, Any]
    call_id: str = "call_0"


@dataclass
class ModelResponse:
    content: str
    tool_calls: List[ModelToolCall] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=lambda: {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})


class ModelClient(ABC):
    @abstractmethod
    def call(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]]
    ) -> ModelResponse:
        pass


class DeterministicModelClient(ModelClient):
    """
    High-fidelity deterministic model client for automated testing and structured evaluations.
    Can also execute real structured tool calls based on prompt intent.
    """
    def __init__(self, behavior_fn: Optional[Any] = None):
        self.behavior_fn = behavior_fn

    def call(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]]
    ) -> ModelResponse:
        if self.behavior_fn:
            return self.behavior_fn(system_prompt, messages, tools)

        last_msg = messages[-1] if messages else {"content": ""}
        orig_content = str(last_msg.get("content", ""))
        content_lower = orig_content.lower()

        # Step 2: Check if previous message was a tool result
        if last_msg.get("role") == "tool":
            tool_res = last_msg.get("content", "")
            return ModelResponse(
                content=f"Processed tool result: {tool_res}. Implementation task completed successfully.",
                tool_calls=[],
                usage={"input_tokens": 250, "output_tokens": 80, "total_tokens": 330}
            )

        # Step 1: Detect intent to invoke tools
        tool_names = [t["function"]["name"] for t in tools if "function" in t]

        if "write file" in content_lower or "create file" in content_lower:
            # Model requests to call write_file
            if "fs.write_file" in tool_names:
                import re
                m_path = re.search(r"(?:write|create)\s+file\s+([^\s,]+)", orig_content, re.IGNORECASE)
                path = m_path.group(1) if m_path else "artifact.txt"
                return ModelResponse(
                    content="Generating requested artifact...",
                    tool_calls=[ModelToolCall(
                        tool_name="fs.write_file",
                        arguments={"path": path, "content": "// Verified Harness Generated Code\nexport const status = 'OK';\n"},
                        call_id="call_write_1"
                    )],
                    usage={"input_tokens": 180, "output_tokens": 40, "total_tokens": 220}
                )

        if "run test" in content_lower or "execute" in content_lower:
            if "shell.execute" in tool_names:
                return ModelResponse(
                    content="Running automated test suite...",
                    tool_calls=[ModelToolCall(
                        tool_name="shell.execute",
                        arguments={"command": "pytest -q || true"},
                        call_id="call_test_1"
                    )],
                    usage={"input_tokens": 150, "output_tokens": 30, "total_tokens": 180}
                )

        # Default conversational response
        return ModelResponse(
            content=f"Acknowledged: {last_msg.get('content')}. Operational parameters confirmed under active scope.",
            tool_calls=[],
            usage={"input_tokens": 120, "output_tokens": 45, "total_tokens": 165}
        )


class HarnessAgentLoop:
    """
    Executes the Model -> Tool -> Next Step cycle.
    Persists every transition to the RuntimeEventStore.
    """
    def __init__(
        self,
        scope: ExecutionScope,
        tool_registry: ScopedToolRegistry,
        context_assembler: ContextAssembler,
        event_store: RuntimeEventStore,
        model_client: ModelClient,
        max_steps_per_turn: int = 5
    ):
        self.scope = scope
        self.tool_registry = tool_registry
        self.context_assembler = context_assembler
        self.event_store = event_store
        self.model_client = model_client
        self.max_steps_per_turn = max_steps_per_turn

    def run_turn(
        self,
        session_id: str,
        turn_id: str,
        user_prompt: str,
        workspace_dir: Optional[str] = None,
        event_callback: Optional[Any] = None
    ) -> TurnResult:
        start_time = time.time()

        # Replay current state
        session_state = self.event_store.replay_session(session_id)

        # Assemble scoped context
        assembled = self.context_assembler.assemble(
            scope=self.scope,
            raw_prompt=user_prompt,
            session_state=session_state,
            workspace_dir=workspace_dir
        )

        # Emit turn started event
        self.event_store.append_event(
            session_id=session_id,
            agent_id=self.scope.agent_id,
            turn_id=turn_id,
            event_type="TURN_STARTED",
            payload={"prompt": user_prompt, "scope": self.scope.role, "allowed_tools": list(self.scope.allowed_tools)}
        )

        if event_callback:
            event_callback(AgentLifecycleEvent(LifecycleEventType.TURN_STARTED, self.scope.agent_id, turn_id, {"prompt": user_prompt}))

        messages: List[Dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        artifacts_created = []
        final_output = ""
        turn_tokens = 0
        step_idx = 0

        while step_idx < self.max_steps_per_turn:
            step_idx += 1

            # Model Call
            resp = self.model_client.call(
                system_prompt=assembled.system_prompt,
                messages=messages,
                tools=assembled.scoped_tools
            )

            toks = resp.usage.get("total_tokens", 0)
            turn_tokens += toks

            self.event_store.append_event(
                session_id=session_id,
                agent_id=self.scope.agent_id,
                turn_id=turn_id,
                event_type="MODEL_RESPONSE",
                payload={"response": resp.content, "tool_calls_count": len(resp.tool_calls), "usage": resp.usage}
            )

            # If no tool calls, turn is finished
            if not resp.tool_calls:
                final_output = resp.content
                break

            # Execute tool calls
            for tc in resp.tool_calls:
                self.event_store.append_event(
                    session_id=session_id,
                    agent_id=self.scope.agent_id,
                    turn_id=turn_id,
                    event_type="TOOL_CALL_REQUESTED",
                    payload={"tool": tc.tool_name, "args": tc.arguments, "call_id": tc.call_id}
                )

                tool_res: ToolExecutionResult = self.tool_registry.execute_tool(
                    scope=self.scope,
                    tool_name=tc.tool_name,
                    arguments=tc.arguments
                )

                self.event_store.append_event(
                    session_id=session_id,
                    agent_id=self.scope.agent_id,
                    turn_id=turn_id,
                    event_type="TOOL_CALL_EXECUTED",
                    payload={
                        "tool": tc.tool_name,
                        "args": tc.arguments,
                        "success": tool_res.success,
                        "output": tool_res.output,
                        "error": tool_res.error,
                        "duration": tool_res.duration
                    }
                )

                if tool_res.success and tc.tool_name == "fs.write_file":
                    p = tc.arguments.get("path")
                    if p:
                        artifacts_created.append(p)

                # Append tool result to messages for model's next step
                messages.append({
                    "role": "assistant",
                    "content": resp.content,
                    "tool_calls": [{"id": tc.call_id, "function": {"name": tc.tool_name, "arguments": tc.arguments}}]
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.call_id,
                    "content": str(tool_res.output) if tool_res.success else f"Error: {tool_res.error}"
                })

        duration = round(time.time() - start_time, 3)
        success = True

        self.event_store.append_event(
            session_id=session_id,
            agent_id=self.scope.agent_id,
            turn_id=turn_id,
            event_type="TURN_COMPLETED",
            payload={"output": final_output, "duration": duration, "artifacts": artifacts_created, "steps": step_idx}
        )

        if event_callback:
            event_callback(AgentLifecycleEvent(
                LifecycleEventType.TURN_COMPLETED,
                self.scope.agent_id,
                turn_id,
                {"output": final_output, "duration": duration, "artifacts": artifacts_created}
            ))

        return TurnResult(
            agent_id=self.scope.agent_id,
            turn_id=turn_id,
            success=success,
            output=final_output,
            artifacts=artifacts_created,
            usage={"total_tokens": turn_tokens},
            duration=duration
        )


class HarnessNativeAdapter(BaseAgentAdapter):
    """
    Agent Mesh Adapter wrapping the HarnessAgentLoop.
    Co-exists seamlessly with CLI-Native adapters (PersistentAgySessionAdapter, CodexCliAdapter, ShellNativeAdapter).
    """
    def __init__(
        self,
        agent_id: str,
        name: str,
        role: str,
        scope: ExecutionScope,
        tool_registry: ScopedToolRegistry,
        context_assembler: ContextAssembler,
        event_store: RuntimeEventStore,
        model_client: Optional[ModelClient] = None,
        workspace_dir: Optional[str] = None
    ):
        super().__init__(
            agent_id=agent_id,
            name=name,
            role=role,
            model="HarnessNative (DSH-Style)",
            workspace_dir=workspace_dir,
            permission_policy={"role": scope.role, "fs_read": True, "fs_write": not scope.read_only}
        )
        self.scope = scope
        self.tool_registry = tool_registry
        self.context_assembler = context_assembler
        self.event_store = event_store
        self.model_client = model_client or DeterministicModelClient()
        self.session_id = f"sess_{agent_id.replace(':', '_')}"

        self.loop = HarnessAgentLoop(
            scope=self.scope,
            tool_registry=self.tool_registry,
            context_assembler=self.context_assembler,
            event_store=self.event_store,
            model_client=self.model_client
        )

        # Log session created event
        self.event_store.append_event(
            session_id=self.session_id,
            agent_id=self.agent_id,
            turn_id="init",
            event_type="SESSION_CREATED",
            payload={"name": self.name, "role": self.role, "workspace": self.workspace_dir}
        )

    def start(self) -> bool:
        self.state = AgentState.IDLE
        return True

    def send_task(
        self,
        prompt: str,
        turn_id: Optional[str] = None,
        event_callback: Optional[Any] = None,
        delta_callback: Optional[Any] = None
    ) -> TurnResult:
        tid = turn_id or f"turn_{int(time.time()*1000)%100000}"
        self.current_turn_id = tid
        self.state = AgentState.TURN_RUNNING

        res = self.loop.run_turn(
            session_id=self.session_id,
            turn_id=tid,
            user_prompt=prompt,
            workspace_dir=self.workspace_dir,
            event_callback=event_callback
        )

        self.turn_history.append(res)
        self.state = AgentState.IDLE
        return res

    def close(self):
        self.state = AgentState.STOPPED
