from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .commands import builtin_command_names

if TYPE_CHECKING:
    from ..skills.manager import SkillManager


# 指令 token 只允许技能/命令常用的短名称；路径、环境变量和 shell 表达式不符合该边界。
_COMMAND_TOKEN = re.compile(r"^/[A-Za-z][A-Za-z0-9_-]*")
_SKILL_TOKEN = re.compile(r"^\$([A-Za-z][A-Za-z0-9_-]*)")


@dataclass(frozen=True)
class InputTrigger:
    kind: str
    token: str


def detect_input_trigger(content: str, skill_manager: SkillManager | None = None) -> InputTrigger | None:
    """只识别真正的内置命令或已存在 Skill，避免把路径当成指令。

    触发必须从消息第一个字符开始。Slash 命令必须来自后端注册表；Dollar
    触发必须匹配真实可调用 Skill。匹配后紧接路径分隔符也会被拒绝，保护
    `/Users/...`、`$HOME/...`、`$skill/file` 等普通路径文本。
    """
    if not content:
        return None
    if content.startswith("/"):
        match = _COMMAND_TOKEN.match(content)
        if not match:
            return None
        token = match.group(0).lower()
        if token not in builtin_command_names():
            return None
        if len(content) > len(token) and not content[len(token)].isspace():
            return None
        return InputTrigger("slash", token)
    if content.startswith("$") and skill_manager is not None:
        match = _SKILL_TOKEN.match(content)
        if not match:
            return None
        name = match.group(1)
        if len(content) > len(match.group(0)) and not content[len(match.group(0))].isspace():
            return None
        skill = skill_manager.find(name)
        if skill is None or not skill.as_summary().get("user_invocable", False):
            return None
        return InputTrigger("skill", match.group(0))
    return None
