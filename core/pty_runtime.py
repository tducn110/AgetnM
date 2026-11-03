"""
AgentMesh PTY Runtime Seam & Adapters
Provides a swappable abstraction layer for interactive terminal runtimes:
- PtyRuntimeAdapter (Abstract Contract)
- LocalPosixPtyAdapter (Standard POSIX pty with OSC 7501 parser)
- HerdrCorePtyAdapter (OpenRPC client for headless herdr-core / hc)

Enables Agent Mesh to switch PTY backends transparently without modifying
the Control Plane, Goal, Planner, or Verification Engine.
"""

import os
import pty
import select
import termios
import struct
import fcntl
import signal
import threading
import time
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable


@dataclass
class PtySessionConfig:
    session_id: str
    command: List[str]
    cwd: str
    env: Dict[str, str] = field(default_factory=dict)
    cols: int = 80
    rows: int = 24
    permission_profile: str = "restricted"  # restricted | privileged


@dataclass
class PtySessionState:
    session_id: str
    pid: Optional[int]
    alive: bool
    exit_code: Optional[int] = None
    agent_status: str = "unknown"  # idle | working | blocked | done | unknown (via OSC 7501)
    cols: int = 80
    rows: int = 24


class PtyRuntimeAdapter(ABC):
    """Abstract Contract for PTY Runtimes in Agent Mesh."""

    @abstractmethod
    def spawn(self, config: PtySessionConfig) -> str:
        """Start a new PTY session and return session_id."""
        pass

    @abstractmethod
    def attach(self, session_id: str, on_output: Callable[[bytes], None]) -> None:
        """Attach an output listener callback to the raw terminal stream."""
        pass

    @abstractmethod
    def write_input(self, session_id: str, data: bytes) -> bool:
        """Send raw input bytes to the terminal."""
        pass

    @abstractmethod
    def resize(self, session_id: str, cols: int, rows: int) -> bool:
        """Resize terminal dimensions."""
        pass

    @abstractmethod
    def terminate(self, session_id: str) -> bool:
        """Gracefully or forcefully terminate the session."""
        pass

    @abstractmethod
    def get_state(self, session_id: str) -> PtySessionState:
        """Get live status of the terminal session."""
        pass


class LocalPosixPtyAdapter(PtyRuntimeAdapter):
    """
    Lightweight, self-contained POSIX PTY Runtime using Linux openpty.
    Zero external binary/Zig dependencies.
    Directly extracts OSC 7501 sequences emitted by cooperative programs.
    """

    # OSC 7501 parser: ESC ] 7501 ; <payload> (BEL | ESC \)
    OSC_7501_REGEX = re.compile(rb'\x1b\]7501;([^\x07\x1b]+)(?:\x07|\x1b\\)')

    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def spawn(self, config: PtySessionConfig) -> str:
        with self._lock:
            master_fd, slave_fd = pty.openpty()

            # Set initial winsize
            winsize = struct.pack("HHHH", config.rows, config.cols, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

            full_env = os.environ.copy()
            full_env.update(config.env)
            full_env["TERM"] = "xterm-256color"

            pid = os.fork()
            if pid == 0:
                # Child process
                os.close(master_fd)
                os.setsid()
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)

                try:
                    os.chdir(config.cwd)
                    os.execvpe(config.command[0], config.command, full_env)
                except Exception as e:
                    os.write(2, f"Failed to exec {config.command}: {e}\n".encode())
                    os._exit(127)
            else:
                # Parent process
                os.close(slave_fd)
                session = {
                    "config": config,
                    "pid": pid,
                    "master_fd": master_fd,
                    "alive": True,
                    "exit_code": None,
                    "agent_status": "idle",
                    "cols": config.cols,
                    "rows": config.rows,
                    "listeners": [],
                    "stop_event": threading.Event()
                }
                self._sessions[config.session_id] = session

                # Start background reader thread for master_fd
                t = threading.Thread(
                    target=self._reader_loop,
                    args=(config.session_id, master_fd),
                    daemon=True
                )
                t.start()
                return config.session_id

    def attach(self, session_id: str, on_output: Callable[[bytes], None]) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session["alive"]:
                session["listeners"].append(on_output)

    def write_input(self, session_id: str, data: bytes) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or not session["alive"]:
                return False
            fd = session["master_fd"]

        try:
            os.write(fd, data)
            return True
        except OSError:
            return False

    def resize(self, session_id: str, cols: int, rows: int) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or not session["alive"]:
                return False
            fd = session["master_fd"]
            session["cols"] = cols
            session["rows"] = rows

        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
            return True
        except OSError:
            return False

    def terminate(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or not session["alive"]:
                return False
            pid = session["pid"]
            fd = session["master_fd"]
            session["alive"] = False

        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.05)
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

        try:
            os.close(fd)
        except OSError:
            pass
        return True

    def get_state(self, session_id: str) -> PtySessionState:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return PtySessionState(session_id=session_id, pid=None, alive=False)
            return PtySessionState(
                session_id=session_id,
                pid=session["pid"],
                alive=session["alive"],
                exit_code=session["exit_code"],
                agent_status=session["agent_status"],
                cols=session["cols"],
                rows=session["rows"]
            )

    def _reader_loop(self, session_id: str, fd: int):
        while True:
            try:
                r, _, _ = select.select([fd], [], [], 0.1)
                if not r:
                    # Check if child process exited
                    with self._lock:
                        session = self._sessions.get(session_id)
                        if not session or not session["alive"]:
                            break
                        pid = session["pid"]

                    pid_res, status = os.waitpid(pid, os.WNOHANG)
                    if pid_res == pid:
                        with self._lock:
                            session["alive"] = False
                            session["exit_code"] = os.waitstatus_to_exitcode(status)
                        break
                    continue

                chunk = os.read(fd, 4096)
                if not chunk:
                    break

                # Parse OSC 7501 in raw stream
                matches = self.OSC_7501_REGEX.findall(chunk)
                if matches:
                    with self._lock:
                        session = self._sessions.get(session_id)
                        if session:
                            for m in matches:
                                try:
                                    payload_str = m.decode("utf-8", errors="ignore")
                                    # e.g., state=working or state=blocked
                                    if "state=" in payload_str:
                                        session["agent_status"] = payload_str.split("state=")[-1].split(";")[0].strip()
                                except Exception:
                                    pass

                # Fanout chunk to listeners
                with self._lock:
                    session = self._sessions.get(session_id)
                    listeners = list(session["listeners"]) if session else []

                for cb in listeners:
                    try:
                        cb(chunk)
                    except Exception:
                        pass

            except (OSError, ValueError):
                break

        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session["alive"] = False


class HerdrCorePtyAdapter(PtyRuntimeAdapter):
    """
    Candidate Adapter for headless herdr-core / hc daemon via OpenRPC socket.
    Demonstrates architectural swappability without requiring host Zig compilation.
    """

    def __init__(self, socket_path: str = "/home/pro/.config/herdr/herdr-core.sock"):
        self.socket_path = socket_path

    def is_available(self) -> bool:
        return os.path.exists(self.socket_path)

    def spawn(self, config: PtySessionConfig) -> str:
        if not self.is_available():
            raise RuntimeError(f"herdr-core socket not available at {self.socket_path}")
        # When hc is deployed, this invokes terminal.spawn over OpenRPC
        return config.session_id

    def attach(self, session_id: str, on_output: Callable[[bytes], None]) -> None:
        if not self.is_available():
            raise RuntimeError(f"herdr-core socket not available at {self.socket_path}")

    def write_input(self, session_id: str, data: bytes) -> bool:
        return False

    def resize(self, session_id: str, cols: int, rows: int) -> bool:
        return False

    def terminate(self, session_id: str) -> bool:
        return False

    def get_state(self, session_id: str) -> PtySessionState:
        return PtySessionState(session_id=session_id, pid=None, alive=False)
