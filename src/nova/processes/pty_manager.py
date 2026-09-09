"""持久 PTY 终端会话（dsh terminal 对齐：跨工具调用保留状态的交互式终端）。

macOS/Linux 用 pty.openpty() 建主从端：子进程的 stdin/stdout/stderr 都接从端，
主端非阻塞读取输出缓冲；工作区外进程也受 OS 级 bash 沙箱策略约束（与 bash 工具
同一 confine 路径——PTY 会话不豁免沙箱）。
"""

from __future__ import annotations

import os
import pty
import select
import shutil
import signal
import subprocess
import threading
import uuid
from typing import Any

from nova.tools.sandbox import SandboxPolicy, SandboxUnavailableError, confine


class PtySession:
    def __init__(self, session_id: str, command: str, argv: list[str], cwd: str, cols: int, rows: int) -> None:
        self.id = session_id
        self.command = command
        self.argv = argv
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self.buffer: list[str] = []          # 全量输出（滚动窗口，保留尾部 256KB）
        self.buffer_bytes = 0
        self._lock = threading.Lock()
        master, slave = pty.openpty()
        self.master_fd = master
        # 终端尺寸（TIOCSWINSZ）
        import fcntl
        import struct
        import termios

        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.process = subprocess.Popen(
            argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave)  # 主端持有者是我们；从端被子进程继承后即可关闭本地引用
        self._reader_stop = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        while not self._reader_stop.is_set():
            try:
                ready, _, _ = select.select([self.master_fd], [], [], 0.2)
            except (OSError, ValueError):
                return
            if not ready:
                if self.process.poll() is not None:
                    # 进程退出：吸干残余输出
                    self._drain()
                    return
                continue
            try:
                chunk = os.read(self.master_fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            self._append(chunk)

    def _drain(self) -> None:
        while True:
            try:
                ready, _, _ = select.select([self.master_fd], [], [], 0)
            except (OSError, ValueError):
                return
            if not ready:
                return
            try:
                chunk = os.read(self.master_fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            self._append(chunk)

    def _append(self, chunk: bytes) -> None:
        text = chunk.decode("utf-8", errors="replace")
        with self._lock:
            self.buffer.append(text)
            self.buffer_bytes += len(text)
            # 滚动窗口：超过 256KB 丢弃最旧的一半
            if self.buffer_bytes > 262144:
                dropped = 0
                while dropped < self.buffer_bytes // 2:
                    dropped += len(self.buffer[0])
                    self.buffer.pop(0)
                self.buffer_bytes -= dropped

    # ---- 对外接口 ----

    def write(self, data: str) -> None:
        os.write(self.master_fd, data.encode("utf-8"))

    def send_signal(self, sig: int) -> None:
        # 进程组（start_new_session 使子进程成为组长）
        try:
            os.killpg(self.process.pid, sig)
        except OSError:
            pass

    def read_new(self) -> str:
        """返回自上次读取以来的新增输出（流式语义，dsh job_output 同款游标）。"""
        with self._lock:
            out = "".join(self.buffer)
            self.buffer = []
            self.buffer_bytes = 0
            return out

    def status(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "cwd": self.cwd,
            "running": self.process.poll() is None,
            "exitCode": self.process.returncode,
            "bufferedBytes": self.buffer_bytes,
        }

    def kill(self) -> None:
        self._reader_stop.set()
        self.send_signal(signal.SIGKILL)
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.close(self.master_fd)
        except OSError:
            pass


class PtyManager:
    """PTY 会话注册表：创建/列出/读/写/杀，进程内全局共享（跨工具调用持久）。"""

    LIMIT = 6

    def __init__(self) -> None:
        self._sessions: dict[str, PtySession] = {}
        self._lock = threading.Lock()

    def start(self, command: str, *, cwd: str, sandbox_mode: str = "workspace_write", workspace_root: str = "", cols: int = 120, rows: int = 30) -> dict[str, Any]:
        with self._lock:
            alive = [s for s in self._sessions.values() if s.process.poll() is None]
            if len(alive) >= self.LIMIT:
                raise RuntimeError(f"PTY 会话数已达上限（{self.LIMIT}）；先 pty_kill 释放")
        # 沙箱约束与 bash 工具一致：danger 之外全部经 runner confine
        if sandbox_mode == "danger_full_access":
            argv = ["/bin/bash", "-c", command]
        else:
            policy = SandboxPolicy(
                mode="read_only" if sandbox_mode == "read_only" else "workspace_write",
                workspace_root=workspace_root,
            )
            argv = confine(command, policy)
        session_id = f"pty_{uuid.uuid4().hex[:10]}"
        session = PtySession(session_id, command, argv, cwd, cols, rows)
        with self._lock:
            self._sessions[session_id] = session
        return session.status()

    def get(self, session_id: str) -> PtySession:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"unknown PTY session: {session_id}")
        return session

    def list(self) -> list[dict[str, Any]]:
        # 清理已退出的旧会话（保留最近 3 个供读残余输出）
        with self._lock:
            dead = [sid for sid, s in self._sessions.items() if s.process.poll() is not None]
            for sid in dead[:-3]:
                self._sessions.pop(sid, None)
            return [s.status() for s in self._sessions.values()]

    def kill(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        session.kill()
        return session.status()


_pty_manager: PtyManager | None = None


def pty_manager() -> PtyManager:
    global _pty_manager
    if _pty_manager is None:
        _pty_manager = PtyManager()
    return _pty_manager
