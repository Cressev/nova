from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltinCommand:
    name: str
    description: str
    argument_hint: str = ""
    group: str = "runtime"
    source: str = "builtin"
    aliases: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "argument_hint": self.argument_hint,
            "group": self.group,
            "source": self.source,
            "aliases": list(self.aliases),
        }


# 这里是 Slash 命令的唯一后端注册表，避免 /help、API 和前端提示各写一份后互相漂移。
BUILTIN_COMMANDS: tuple[BuiltinCommand, ...] = (
    BuiltinCommand("/help", "查看内置指令说明", group="help"),
    BuiltinCommand("/status", "查看网关、模型、权限、工作区和 Git 状态", group="runtime"),
    BuiltinCommand("/model", "查看当前模型、Base URL 和密钥配置状态", group="config"),
    BuiltinCommand("/tools", "列出当前可用工具、权限和并行能力", group="tools"),
    BuiltinCommand("/skills", "列出当前可用技能，显示来源和触发方式", group="skills"),
    BuiltinCommand("/skill", "读取并触发一个技能的 SKILL.md", "<技能名>", "skills"),
    BuiltinCommand("/permissions", "查看权限模式和限制", group="permissions"),
    BuiltinCommand("/approvals", "查看审批策略", group="permissions"),
    BuiltinCommand("/sandbox", "查看沙箱模式", group="permissions"),
    BuiltinCommand("/memory", "查看项目记忆；支持 search / summarize / compact", "[search|summarize|compact] [关键词]", "memory"),
    BuiltinCommand("/remember", "创建长期记忆候选，等待用户确认后写入", "<事实或偏好>", "memory"),
    BuiltinCommand("/ps", "查看后台任务", group="processes", aliases=("/jobs",)),
    BuiltinCommand("/jobs", "查看后台任务（/ps 的别名）", group="processes", aliases=("/ps",)),
    BuiltinCommand("/stop", "终止后台任务（/kill 的别名）", "<后台任务ID>", "processes", aliases=("/kill",)),
    BuiltinCommand("/kill", "终止后台任务", "<后台任务ID>", "processes", aliases=("/stop",)),
    BuiltinCommand("/review", "读取当前 diff 摘要，进入 Review 视角", group="review"),
    BuiltinCommand("/plan", "先拆解目标和验收标准，再进入执行", "<目标和验收标准>", "planning"),
    BuiltinCommand("/compact", "压缩当前会话并写入 session 记忆", "[压缩要求]", "context"),
    BuiltinCommand("/clear", "创建空线程提示，不删除已有历史", group="session"),
)


def list_builtin_commands() -> list[dict[str, object]]:
    return [command.as_dict() for command in BUILTIN_COMMANDS]


def builtin_command_names() -> set[str]:
    return {command.name for command in BUILTIN_COMMANDS}


def builtin_help_text() -> str:
    rows = []
    for command in BUILTIN_COMMANDS:
        hint = f" {command.argument_hint}" if command.argument_hint else ""
        rows.append(f"- {command.name}{hint}：{command.description}")
    return "可用内置指令：\n" + "\n".join(rows)
