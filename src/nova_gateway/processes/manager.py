from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProcessJob:
    id: str
    command: str
    cwd: str
    process: subprocess.Popen[str]
    call_id: str | None = None
    status: str = "running"
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    exit_code: int | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def as_dict(self, *, include_output: bool = True) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "command": self.command,
            "cwd": self.cwd,
            "call_id": self.call_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_output:
            payload["stdout"] = "".join(self.stdout)
            payload["stderr"] = "".join(self.stderr)
        return payload


class ProcessManager:
    """管理 shell 进程，给 Codex-like 前台分片和后台任务复用。"""

    def __init__(self, *, chunk_size: int = 1024) -> None:
        self.chunk_size = chunk_size
        self._jobs: dict[str, ProcessJob] = {}
        self._jobs_by_call_id: dict[str, str] = {}
        self._lock = threading.Lock()

    def run_foreground(
        self,
        command: str,
        *,
        cwd: Path,
        timeout_ms: int,
        call_id: str | None = None,
        tool: str = "shell_command",
    ) -> Iterator[dict[str, Any]]:
        call_id = call_id or f"tool_{uuid4().hex[:12]}"
        job = self._start(command, cwd=cwd, call_id=call_id)
        output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        readers = [
            self._start_reader(job, "stdout", output_queue),
            self._start_reader(job, "stderr", output_queue),
        ]
        deadline = time.monotonic() + max(timeout_ms, 1) / 1000
        next_heartbeat = time.monotonic() + 0.25
        timed_out = False

        while True:
            try:
                stream, chunk = output_queue.get(timeout=0.05)
            except queue.Empty:
                stream, chunk = "", None
            if chunk:
                self._append(job, stream, chunk)
                yield {
                    "type": "tool_output",
                    "call_id": call_id,
                    "tool": tool,
                    "stream": stream,
                    "chunk": chunk,
                    "data": {"job_id": job.id},
                }
            elif time.monotonic() >= next_heartbeat:
                next_heartbeat = time.monotonic() + 0.25
                yield {
                    "type": "tool_heartbeat",
                    "call_id": call_id,
                    "tool": tool,
                    "data": {"job_id": job.id, "status": job.status},
                }
            if job.process.poll() is not None:
                terminal_status = job.status if job.status in {"cancelled", "killed"} else None
                self._finish(job, terminal_status or ("completed" if job.process.returncode == 0 else "failed"))
                for reader in readers:
                    if reader is not None:
                        reader.join(timeout=0.4)
                while not output_queue.empty():
                    stream, chunk = output_queue.get_nowait()
                    if chunk:
                        self._append(job, stream, chunk)
                        yield {
                            "type": "tool_output",
                            "call_id": call_id,
                            "tool": tool,
                            "stream": stream,
                            "chunk": chunk,
                            "data": {"job_id": job.id},
                        }
                self._close_pipes(job)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                self._terminate(job)
                self._finish(job, "timeout")
                for reader in readers:
                    if reader is not None:
                        reader.join(timeout=0.2)
                self._close_pipes(job)
                break

        stdout = "".join(job.stdout)
        stderr = "".join(job.stderr)
        output = "\n".join(part.strip() for part in [stdout, stderr] if part.strip())
        if job.status == "cancelled":
            output = f"{output}\n命令已取消".strip()
        if len(output) > 24000:
            output = output[:24000] + "\n...[输出已截断]"
        yield {
            "type": "tool_done",
            "call_id": call_id,
            "tool": tool,
            "ok": job.exit_code == 0 and not timed_out and job.status == "completed",
            "title": f"执行命令：{command}",
            "output": output or ("命令已取消" if job.status == "cancelled" else f"命令退出码：{job.exit_code}"),
            "data": {"exit_code": job.exit_code, "workdir": str(cwd), "job_id": job.id, "status": job.status},
        }

    def start_background(self, command: str, *, cwd: Path) -> dict[str, Any]:
        job = self._start(command, cwd=cwd)
        output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self._start_reader(job, "stdout", output_queue)
        self._start_reader(job, "stderr", output_queue)
        threading.Thread(target=self._drain_background, args=(job, output_queue), daemon=True).start()
        return job.as_dict(include_output=False)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        self._refresh(jobs)
        return [job.as_dict(include_output=False) for job in sorted(jobs, key=lambda item: item.created_at, reverse=True)]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        self._refresh([job])
        return job.as_dict(include_output=True)

    def kill(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        self._terminate(job)
        self._finish(job, "killed")
        return job.as_dict(include_output=True)

    def cancel_call(self, call_id: str) -> dict[str, Any]:
        with self._lock:
            job_id = self._jobs_by_call_id.get(call_id)
            job = self._jobs.get(job_id or "")
        if job is None:
            raise KeyError(call_id)
        self._terminate(job)
        self._finish(job, "cancelled")
        return job.as_dict(include_output=True)

    def kill_all(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.status == "running":
                self._terminate(job)
                self._finish(job, "killed")

    def _start(self, command: str, *, cwd: Path, call_id: str | None = None) -> ProcessJob:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            shell=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name != "nt"),
            bufsize=1,
        )
        job = ProcessJob(id=f"proc_{uuid4().hex[:12]}", command=command, cwd=str(cwd), process=process, call_id=call_id)
        with self._lock:
            self._jobs[job.id] = job
            if call_id:
                self._jobs_by_call_id[call_id] = job.id
        return job

    def _start_reader(
        self,
        job: ProcessJob,
        stream: str,
        output_queue: queue.Queue[tuple[str, str | None]],
    ) -> threading.Thread | None:
        pipe = job.process.stdout if stream == "stdout" else job.process.stderr
        if pipe is None:
            return None

        def read() -> None:
            buffer: list[str] = []
            while True:
                char = pipe.read(1)
                if not char:
                    if buffer:
                        output_queue.put((stream, "".join(buffer)))
                    break
                buffer.append(char)
                if char == "\n" or len(buffer) >= self.chunk_size:
                    output_queue.put((stream, "".join(buffer)))
                    buffer.clear()

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        return thread

    def _drain_background(self, job: ProcessJob, output_queue: queue.Queue[tuple[str, str | None]]) -> None:
        while job.process.poll() is None or not output_queue.empty():
            try:
                stream, chunk = output_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk:
                self._append(job, stream, chunk)
        self._finish(job, "completed" if job.process.returncode == 0 else "failed")
        self._close_pipes(job)

    def _append(self, job: ProcessJob, stream: str, chunk: str) -> None:
        with self._lock:
            target = job.stdout if stream == "stdout" else job.stderr
            target.append(chunk)
            if sum(len(part) for part in target) > 100000:
                target[:] = ["".join(target)[-100000:]]
            job.updated_at = _now()

    def _finish(self, job: ProcessJob, status: str) -> None:
        with self._lock:
            job.exit_code = job.process.poll()
            job.status = status
            job.updated_at = _now()

    def _refresh(self, jobs: list[ProcessJob]) -> None:
        for job in jobs:
            if job.status == "running" and job.process.poll() is not None:
                self._finish(job, "completed" if job.process.returncode == 0 else "failed")

    def _terminate(self, job: ProcessJob) -> None:
        if job.process.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(job.process.pid, signal.SIGTERM)
            else:
                job.process.terminate()
            job.process.wait(timeout=1.5)
        except Exception:
            try:
                if os.name != "nt":
                    os.killpg(job.process.pid, signal.SIGKILL)
                else:
                    job.process.kill()
            except Exception:
                pass
        self._close_pipes(job)

    def _close_pipes(self, job: ProcessJob) -> None:
        for pipe in [job.process.stdout, job.process.stderr]:
            try:
                if pipe is not None and not pipe.closed:
                    pipe.close()
            except Exception:
                pass
