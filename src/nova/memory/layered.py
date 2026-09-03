"""分层长期记忆（dsh memory-plugin 的 Python 移植）。

dsh 的 memory 插件形态：每个 scope 一个目录 —— `index.md`（每条
`- <id> :: <title>` 一行）+ `items/<id>.md`（`# <title>` 标题 + 完整正文）。
只有索引进提示词；模型用标准 read 工具按需读条目详情，存的细节永远
不会撑爆系统提示词。写操作从 items/ 目录重建索引，index.md 是派生状态；
提示词段在每次组装时重读索引，所以写入在下一次请求即生效。

工具面：memory_write（scope/title/content/id）+ memory_remove（scope/id）。
路径：global = ~/.nova/memory/global；project = <workspace>/.nova-memory。
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any

DEFAULT_MAX_ENTRIES = 200
DEFAULT_MAX_TITLE_CHARS = 120
DEFAULT_MAX_CONTENT_CHARS = 20000
DEFAULT_MAX_PROMPT_ENTRIES = 60

PROJECT_DIRNAME = ".nova-memory"
ITEMS_DIRNAME = "items"
INDEX_FILENAME = "index.md"


class MemoryEntryError(ValueError):
    """模型提交的条目不合法（空标题/正文或超限）。"""


def slugify(text: str) -> str:
    """标题或显式 id 折叠成文件名安全的 slug：unicode 字母数字保留，
    其它连续段折叠为 `-`，去首尾连字符，长度上限 48。"""
    slug = (
        unicodedata.normalize("NFKD", text)
        .lower()
    )
    slug = re.sub(r"[^\w]+", "-", slug, flags=re.UNICODE)
    slug = slug.strip("-")[:48].strip("-")
    return slug or "memory"


def normalize_entry(
    raw: dict[str, Any],
    *,
    max_title_chars: int = DEFAULT_MAX_TITLE_CHARS,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
) -> dict[str, str]:
    """校验并规范化模型条目：非空标题/正文且在限内，id slug 化。"""
    title = str(raw.get("title") or "").strip()
    if not title:
        raise MemoryEntryError("invalid memory entry: `title` must be a non-empty string")
    if len(title) > max_title_chars:
        raise MemoryEntryError(f"invalid memory entry: title exceeds {max_title_chars} characters")
    content = str(raw.get("content") or "").strip()
    if not content:
        raise MemoryEntryError("invalid memory entry: `content` must be a non-empty string")
    if len(content) > max_content_chars:
        raise MemoryEntryError(
            f"invalid memory entry: content exceeds {max_content_chars} characters — split the entry"
        )
    return {"id": slugify(str(raw.get("id") or title)), "title": title, "content": content}


def extract_item_title(text: str) -> str:
    """从条目详情文件提取标题：第一个 `# ` 标题行。"""
    match = re.search(r"^# (.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else "untitled"


def parse_index_entries(text: str) -> list[dict[str, str]]:
    """解析索引文件：`- <id> :: <title>` 每行一条，按文件顺序。"""
    entries: list[dict[str, str]] = []
    for line in text.splitlines():
        match = re.match(r"^- (.+?) :: (.+)$", line)
        if match:
            entries.append({"id": match.group(1).strip(), "title": match.group(2).strip()})
    return entries


def render_index_file(scope: str, entries: list[dict[str, str]]) -> str:
    lines = [f"# Memory index — {scope}", ""]
    lines.extend(f"- {entry['id']} :: {entry['title']}" for entry in entries)
    lines.append("")
    return "\n".join(lines)


def render_entry_file(entry: dict[str, str]) -> str:
    return f"# {entry['title']}\n\n{entry['content']}\n"


def scope_of_dir(dir_path: Path) -> str:
    return "project" if dir_path.name == PROJECT_DIRNAME else "global"


def rebuild_index_files(dir_path: Path) -> int:
    """从 items/ 重建 index.md（派生状态）；目录缺失返回 0。"""
    items_dir = dir_path / ITEMS_DIRNAME
    if not items_dir.is_dir():
        return 0
    entries = []
    for item_path in sorted(items_dir.glob("*.md")):
        entries.append(
            {"id": item_path.stem, "title": extract_item_title(item_path.read_text(encoding="utf-8", errors="replace"))}
        )
    rendered = render_index_file(scope_of_dir(dir_path), entries)
    index_path = dir_path / INDEX_FILENAME
    if not index_path.is_file() or index_path.read_text(encoding="utf-8") != rendered:
        dir_path.mkdir(parents=True, exist_ok=True)
        index_path.write_text(rendered, encoding="utf-8")
    return len(entries)


def read_index(dir_path: Path) -> list[dict[str, str]]:
    index_path = dir_path / INDEX_FILENAME
    if not index_path.is_file():
        return []
    return parse_index_entries(index_path.read_text(encoding="utf-8", errors="replace"))


def global_dir() -> Path:
    home = Path(os.getenv("NOVA_HOME", "~/.nova")).expanduser().resolve()
    return home / "memory" / "global"


def project_dir(workspace_root: Path) -> Path:
    return workspace_root / PROJECT_DIRNAME


def write_entry(
    dir_path: Path,
    entry: dict[str, str],
    existing: list[dict[str, str]],
    *,
    has_explicit_id: bool,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> dict[str, Any]:
    """持久化一条：冲突解析 → 写详情文件 → 重建索引。

    显式 id 总是定向更新该条目；由标题派生的 id 与不同已存标题冲突时
    加数字后缀新建。scope 满且新建时报错。
    """
    entry_id = entry["id"]
    stored = next((candidate for candidate in existing if candidate["id"] == entry_id), None)
    if stored is None and len(existing) >= max_entries:
        raise MemoryEntryError(
            f"memory is full: at most {max_entries} entries — remove obsolete ones first"
        )
    if stored is not None and stored["title"] != entry["title"] and not has_explicit_id:
        suffix = 2
        while any(candidate["id"] == f"{entry_id}-{suffix}" for candidate in existing):
            suffix += 1
        entry_id = f"{entry_id}-{suffix}"
    items_dir = dir_path / ITEMS_DIRNAME
    items_dir.mkdir(parents=True, exist_ok=True)
    item_path = items_dir / f"{entry_id}.md"
    item_path.write_text(render_entry_file({**entry, "id": entry_id}), encoding="utf-8")
    total = rebuild_index_files(dir_path)
    return {"id": entry_id, "path": str(item_path), "total": total}


def remove_entry(dir_path: Path, entry_id: str) -> dict[str, Any]:
    """按 id 删除条目并重建索引；不存在时列出当前 id 报错。"""
    item_path = dir_path / ITEMS_DIRNAME / f"{slugify(entry_id)}.md"
    existed = item_path.exists()
    if existed:
        item_path.unlink()
    else:
        # 目录可能整个不存在
        ids = [entry["id"] for entry in read_index(dir_path)]
        shown = ", ".join(ids[:20]) + (", …" if len(ids) > 20 else "")
        raise MemoryEntryError(f"no memory entry '{entry_id}' in this scope (existing: {shown})")
    total = rebuild_index_files(dir_path)
    return {"path": str(item_path), "total": total}


def render_section(workspace_root: Path, *, max_prompt_entries: int = DEFAULT_MAX_PROMPT_ENTRIES) -> str:
    """渲染进系统提示词的分层索引段（dsh renderSection 的移植）。"""

    def render_index(dir_path: Path) -> str:
        entries = read_index(dir_path)
        if not entries:
            return "(empty)"
        shown = entries[:max_prompt_entries]
        omitted = len(entries) - len(shown)
        lines = [f"- {entry['id']} :: {entry['title']}" for entry in shown]
        if omitted > 0:
            lines.append(f"- … ({omitted} more stored, not shown)")
        return "\n".join(lines)

    gdir = global_dir()
    pdir = project_dir(workspace_root)
    return "\n\n".join(
        [
            "You maintain layered durable memory that persists across conversations. The indexes below list every stored entry by id and title only. When you need an entry's details, read its item file with the standard read tool: `<scope directory>/items/<id>.md`. To save or update one entry, call `memory_write` with its title and the COMPLETE detail text (that entry's body is replaced whole). To delete an entry, call `memory_remove` with its id. Use scope `global` for facts that hold in every project and scope `project` for facts tied to the current workspace. Do not store transient task state or secrets.",
            f"Global memory directory: {gdir}",
            f"Global index:\n{render_index(gdir)}",
            f"Project memory directory: {pdir} (workspace {workspace_root})",
            f"Project index:\n{render_index(pdir)}",
        ]
    )


def reconcile_index(dir_path: Path) -> None:
    """修复漂移索引（手增手删条目文件）且不让 agent 失败。"""
    try:
        rebuild_index_files(dir_path)
    except OSError:
        pass


__all__ = [
    "DEFAULT_MAX_CONTENT_CHARS",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_PROMPT_ENTRIES",
    "DEFAULT_MAX_TITLE_CHARS",
    "ITEMS_DIRNAME",
    "MemoryEntryError",
    "PROJECT_DIRNAME",
    "extract_item_title",
    "global_dir",
    "normalize_entry",
    "parse_index_entries",
    "project_dir",
    "read_index",
    "rebuild_index_files",
    "reconcile_index",
    "remove_entry",
    "render_entry_file",
    "render_index_file",
    "render_section",
    "slugify",
    "write_entry",
]
