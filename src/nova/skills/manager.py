from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillCard:
    name: str
    description: str
    scope: str
    root: Path
    directory: Path
    file_path: Path
    content: str
    frontmatter: dict[str, str]

    @property
    def key(self) -> str:
        return f"{self.scope}:{self.name}"

    @property
    def trigger(self) -> str:
        return f"${self.name}"

    def as_summary(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "display_name": self.frontmatter.get("name") or self.name,
            "description": self.description,
            "scope": self.scope,
            "root": str(self.root),
            "directory": str(self.directory),
            "path": str(self.file_path),
            "trigger": self.trigger,
            "size_bytes": len(self.content.encode("utf-8")),
            "preview": self._preview(),
            "user_invocable": self._frontmatter_bool("user-invocable", True),
        }

    def as_detail(self) -> dict[str, Any]:
        return {
            **self.as_summary(),
            "content": self.content,
            "frontmatter": self.frontmatter,
        }

    def _preview(self) -> str:
        body = strip_frontmatter(self.content).strip()
        return body[:600]

    def _frontmatter_bool(self, key: str, fallback: bool) -> bool:
        value = self.frontmatter.get(key)
        if value is None:
            return fallback
        return value.strip().lower() not in {"0", "false", "no", "off"}


class SkillManager:
    """发现和读取 Nova 技能。

    参考 cc 的 `skills/loadSkillsDir.ts`：只识别 `skill-name/SKILL.md`
    目录格式；项目级技能与全局技能分开标记，触发方式统一为 `$skill`。
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def status(self) -> dict[str, Any]:
        skills = self.list_skills()
        return {
            "enabled": True,
            "project_root": str(self.project_root),
            "roots": {
                "global": [str(path) for path in self.global_roots()],
                "project": [str(path) for path in self.project_roots()],
            },
            "skills": [skill.as_summary() for skill in skills],
            "counts": {
                "total": len(skills),
                "global": len([skill for skill in skills if skill.scope == "global"]),
                "project": len([skill for skill in skills if skill.scope == "project"]),
            },
        }

    def list_skills(self) -> list[SkillCard]:
        cards: list[SkillCard] = []
        for root in self.project_roots():
            cards.extend(self._load_root(root, "project"))
        for root in self.global_roots():
            cards.extend(self._load_root(root, "global"))
        return cards

    def find(self, name: str, *, scope: str | None = None) -> SkillCard | None:
        target = name.strip()
        for skill in self.list_skills():
            if scope and skill.scope != scope:
                continue
            if skill.name == target or skill.frontmatter.get("name") == target:
                return skill
        return None

    def skill_index_prompt(self, *, limit: int = 40) -> str:
        skills = [skill for skill in self.list_skills() if skill.as_summary()["user_invocable"]]
        if not skills:
            return "暂无可用技能。"
        rows = [
            f"- {skill.trigger}（{skill.scope}）：{skill.description or skill.name}"
            for skill in skills[:limit]
        ]
        if len(skills) > limit:
            rows.append(f"- 还有 {len(skills) - limit} 个技能，可用 /skills 查看。")
        return "\n".join(rows)

    def global_roots(self) -> list[Path]:
        env_value = os.getenv("NOVA_GLOBAL_SKILLS_DIRS")
        if env_value:
            return [Path(item).expanduser().resolve() for item in env_value.split(os.pathsep) if item.strip()]
        return [
            Path("~/.nova/skills").expanduser().resolve(),
            Path("~/.codex/skills").expanduser().resolve(),
            Path("~/.agents/skills").expanduser().resolve(),
        ]

    def project_roots(self) -> list[Path]:
        return [
            (self.project_root / ".nova" / "skills").resolve(),
            (self.project_root / ".codex" / "skills").resolve(),
        ]

    def _load_root(self, root: Path, scope: str) -> list[SkillCard]:
        try:
            entries = sorted(root.iterdir(), key=lambda path: path.name.lower())
        except OSError:
            return []
        cards: list[SkillCard] = []
        for entry in entries:
            if not entry.is_dir():
                continue
            skill_file = entry / "SKILL.md"
            try:
                content = skill_file.read_text(encoding="utf-8")
            except OSError:
                continue
            frontmatter = parse_frontmatter(content)
            cards.append(
                SkillCard(
                    name=entry.name,
                    description=frontmatter.get("description", "").strip(),
                    scope=scope,
                    root=root,
                    directory=entry,
                    file_path=skill_file,
                    content=content,
                    frontmatter=frontmatter,
                )
            )
        return cards


def parse_frontmatter(content: str) -> dict[str, str]:
    if not content.startswith("---"):
        return {}
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def strip_frontmatter(content: str) -> str:
    if not content.startswith("---"):
        return content
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return content
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :])
    return content
