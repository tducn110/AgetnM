"""
H01: Capability Seam Architecture
Decouples:
1. Service Definition: Abstract contract (methods, parameter specs, error semantics)
2. Capability Provider: Concrete execution implementation (Local, Sandboxed, Container, Remote)
3. Consumer Tool Schema: Exposed tool definitions for model invocation
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import os
import subprocess
import time


class ServiceDefinition:
    """Contract specification for an agent capability."""
    def __init__(
        self,
        service_id: str,
        name: str,
        description: str,
        methods: List[str]
    ):
        self.service_id = service_id
        self.name = name
        self.description = description
        self.methods = methods


class CapabilityProvider(ABC):
    """Abstract provider that implements a capability."""
    @abstractmethod
    def get_service_id(self) -> str:
        pass


class FsCapabilityProvider(CapabilityProvider):
    """Abstract filesystem capability."""
    def get_service_id(self) -> str:
        return "service.fs"

    @abstractmethod
    def read_file(self, path: str) -> str:
        pass

    @abstractmethod
    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def list_dir(self, path: str) -> List[str]:
        pass


class SandboxedFsProvider(FsCapabilityProvider):
    """
    Filesystem provider with strict workspace boundary enforcement.
    Guarantees no traversal outside root_dir.
    """
    def __init__(self, root_dir: str, read_only: bool = False):
        self.root_dir = os.path.realpath(root_dir)
        self.read_only = read_only

    def _resolve_safe_path(self, path: str) -> str:
        if os.path.isabs(path):
            target = os.path.realpath(path)
        else:
            target = os.path.realpath(os.path.join(self.root_dir, path))

        # Boundary check
        if not target.startswith(self.root_dir):
            raise PermissionError(
                f"Access denied: Target path '{path}' ({target}) is outside authorized root '{self.root_dir}'"
            )
        return target

    def read_file(self, path: str) -> str:
        safe_path = self._resolve_safe_path(path)
        if not os.path.exists(safe_path):
            raise FileNotFoundError(f"File not found: {path}")
        with open(safe_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        if self.read_only:
            raise PermissionError("Write rejected: Provider is configured in READ-ONLY mode")
        safe_path = self._resolve_safe_path(path)
        os.makedirs(os.path.dirname(safe_path), exist_ok=True)
        with open(safe_path, "w", encoding="utf-8") as f:
            f.write(content)
        bytes_written = len(content.encode("utf-8"))
        return {
            "success": True,
            "path": safe_path,
            "bytes_written": bytes_written,
            "timestamp": time.time()
        }

    def list_dir(self, path: str = ".") -> List[str]:
        safe_path = self._resolve_safe_path(path)
        if not os.path.isdir(safe_path):
            raise NotADirectoryError(f"Not a directory: {path}")
        return sorted(os.listdir(safe_path))


class ShellCapabilityProvider(CapabilityProvider):
    """Abstract shell command execution capability."""
    def get_service_id(self) -> str:
        return "service.shell"

    @abstractmethod
    def execute(self, command: str, cwd: Optional[str] = None, timeout: float = 30.0) -> Dict[str, Any]:
        pass


class SandboxedShellProvider(ShellCapabilityProvider):
    """
    Subprocess execution provider restricted to allowed directories and blocking destructive commands.
    """
    FORBIDDEN_COMMANDS = [
        "rm -rf /", "rm -rf ~", "mkfs", "dd", "chmod -R 777 /",
        "> /etc", "> /var", "> /usr", "shutdown", "reboot"
    ]

    def __init__(self, allowed_cwd: str):
        self.allowed_cwd = os.path.realpath(allowed_cwd)

    def execute(self, command: str, cwd: Optional[str] = None, timeout: float = 30.0) -> Dict[str, Any]:
        # Inspect for banned destructive tokens
        cmd_lower = command.lower()
        for banned in self.FORBIDDEN_COMMANDS:
            if banned in cmd_lower:
                raise PermissionError(f"Command execution rejected: Forbidden command pattern '{banned}' detected")

        effective_cwd = os.path.realpath(cwd) if cwd else self.allowed_cwd
        if not effective_cwd.startswith(self.allowed_cwd):
            raise PermissionError(
                f"Execution rejected: Working directory '{effective_cwd}' is outside allowed '{self.allowed_cwd}'"
            )

        start = time.time()
        try:
            res = subprocess.run(
                command,
                shell=True,
                cwd=effective_cwd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            duration = round(time.time() - start, 3)
            return {
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "duration": duration,
                "success": (res.returncode == 0)
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "duration": timeout,
                "success": False
            }


class ConsumerToolSchema:
    """
    Exposes a Service capability as an LLM Tool Definition with JSON schema.
    """
    def __init__(
        self,
        tool_name: str,
        service_id: str,
        description: str,
        parameters_schema: Dict[str, Any],
        handler_fn: Any
    ):
        self.tool_name = tool_name
        self.service_id = service_id
        self.description = description
        self.parameters_schema = parameters_schema
        self.handler_fn = handler_fn

    def to_openai_tool(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.description,
                "parameters": self.parameters_schema
            }
        }
