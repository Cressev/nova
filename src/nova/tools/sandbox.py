"""OS 级 bash 沙箱（对齐 dsh bash-sandbox 语义）。

dsh 的模型：每条 bash 命令都被 confine 成 runner argv——
- macOS：sandbox-exec（SBPL profile，deny file-write* + 可写根白名单）
- Linux：bubblewrap（bwrap，只读绑定 / + 可写根绑定）
- Windows：暂不支持内核级 confine（同 dsh 未覆盖路径），fail-closed 拒绝

fail-closed 语义：runner 程序不存在/无法启动时抛 SandboxUnavailableError，
宁可失败也不裸跑（对齐 dsh SandboxUnavailableError：isRunnerSpawnFailure → 抛错）。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SandboxUnavailableError(RuntimeError):
    """沙箱 runner 不可用：命令没有执行，按失败处理（fail-closed）。"""

    def __init__(self, mode: str, detail: str) -> None:
        super().__init__(f"沙箱 runner 不可用（mode={mode}）：{detail}。按 fail-closed 拒绝执行，不降级为无沙箱运行。")
        self.mode = mode
        self.detail = detail


@dataclass(frozen=True)
class SandboxPolicy:
    """一次执行的沙箱策略（对齐 dsh SandboxExecutionPolicy 的文件效果子集）。"""

    mode: str  # read_only | workspace_write | danger_full_access
    workspace_root: str


def seatbelt_profile_args(policy: SandboxPolicy) -> list[str]:
    """生成 macOS SBPL profile 参数（对齐 dsh seatbeltProfileArgs）。

    read-only：全盘拒绝写（仅 /dev/null 白名单）。
    workspace-write：deny file-write* + 可写根 subpath 白名单。
    """
    forms = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        '(allow file-write* (literal "/dev/null"))',
    ]
    if policy.mode == "workspace_write" and policy.workspace_root:
        forms.append(
            f'(allow file-write* (subpath "{policy.workspace_root}"))'
        )
    return ["-p", " ".join(forms)]


def bwrap_profile_args(policy: SandboxPolicy) -> list[str]:
    """生成 Linux bubblewrap 参数（对齐 dsh bwrapProfileArgs 的文件效果）。"""
    args = [
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
    ]
    if policy.mode == "workspace_write" and policy.workspace_root:
        args += ["--bind", policy.workspace_root, policy.workspace_root]
    return args


def confine(command: str, policy: SandboxPolicy, *, platform: str | None = None) -> list[str]:
    """把 shell 命令 confine 成 runner argv。

    返回可直接交给 subprocess.run(argv, shell=False) 的参数列表；
    runner 可执行文件不存在时抛 SandboxUnavailableError（fail-closed）。
    """
    system = (platform or _host_platform()).lower()
    if system == "darwin":
        runner = shutil.which("sandbox-exec")
        if runner is None:
            raise SandboxUnavailableError(policy.mode, "未找到 sandbox-exec")
        return [runner, *seatbelt_profile_args(policy), "/bin/bash", "-c", command]
    if system == "linux":
        runner = shutil.which("bwrap")
        if runner is None:
            raise SandboxUnavailableError(policy.mode, "未找到 bwrap")
        return [runner, *bwrap_profile_args(policy), "/bin/bash", "-c", command]
    # Windows：无内核级 runner（dsh 走 windows-acl，Nova 不带）——fail-closed
    raise SandboxUnavailableError(policy.mode, f"平台 {system} 无可用沙箱 runner")


def _host_platform() -> str:
    import sys

    return sys.platform


def probe_runner(platform: str | None = None) -> dict[str, Any]:
    """探测本机沙箱 runner 可用性（诊断用，不执行命令）。"""
    system = (platform or _host_platform()).lower()
    if system == "darwin":
        exe = shutil.which("sandbox-exec")
        if exe is None:
            return {"platform": system, "available": False, "runner": None}
        # 功能探测：用真实 read-only profile 跑 true——退出 0 说明内核接受该 profile
        # （对齐 dsh 的 functional Seatbelt probe 语义）
        try:
            ok = subprocess.run(
                [exe, *seatbelt_profile_args(SandboxPolicy("read_only", "")), "/usr/bin/true"],
                capture_output=True,
                timeout=5,
            ).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        return {"platform": system, "available": ok, "runner": exe}
    if system == "linux":
        exe = shutil.which("bwrap")
        return {"platform": system, "available": exe is not None, "runner": exe}
    return {"platform": system, "available": False, "runner": None}


def writable_roots(policy: SandboxPolicy) -> list[str]:
    """当前策略下的可写根（read_only 为空）。"""
    if policy.mode == "workspace_write" and policy.workspace_root:
        return [str(Path(policy.workspace_root).resolve())]
    return []
