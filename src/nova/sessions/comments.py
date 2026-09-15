"""划词评论存储（独立于 chats.json 的旁路数据）。

锚点+线程按会话组织，持久化到 <root>/comments.json。
主对话构建上下文时不读取本文件任何内容（旁路语义）。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ..models import CommentAnchor, CommentEntry, new_id, utc_now


class CommentStore:
    """锚点与评论线程的内存态+落盘存储，线程安全。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._anchors: dict[str, list[CommentAnchor]] = {}
        self._entries: dict[str, list[CommentEntry]] = {}
        self._lock = threading.RLock()
        self._file = root / "comments.json"
        self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for a in data.get("anchors", []):
            try:
                anchor = CommentAnchor.model_validate(a)
            except Exception:
                continue
            self._anchors.setdefault(anchor.session_id, []).append(anchor)
        for e in data.get("entries", []):
            try:
                entry = CommentEntry.model_validate(e)
            except Exception:
                continue
            self._entries.setdefault(entry.anchor_id, []).append(entry)

    def _save(self) -> None:
        anchors = [a.model_dump(mode="json") for lst in self._anchors.values() for a in lst]
        entries = [e.model_dump(mode="json") for lst in self._entries.values() for e in lst]
        payload = {"anchors": anchors, "entries": entries}
        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._file)

    # ---- 锚点 ----

    def create_anchor(
        self,
        session_id: str,
        message_id: str,
        quote: str,
        prefix: str = "",
        suffix: str = "",
        occurrence: int = 0,
    ) -> CommentAnchor:
        with self._lock:
            anchor = CommentAnchor(
                id=new_id("anchor"),
                session_id=session_id,
                message_id=message_id,
                quote=quote,
                prefix=prefix[-200:],
                suffix=suffix[:200],
                occurrence=max(0, occurrence),
                created_at=utc_now(),
            )
            self._anchors.setdefault(session_id, []).append(anchor)
            self._save()
            return anchor

    def get_anchor(self, anchor_id: str) -> CommentAnchor | None:
        with self._lock:
            for lst in self._anchors.values():
                for a in lst:
                    if a.id == anchor_id:
                        return a
        return None

    def list_anchors(self, session_id: str) -> list[CommentAnchor]:
        with self._lock:
            return list(self._anchors.get(session_id, []))

    def delete_anchor(self, session_id: str, anchor_id: str) -> bool:
        with self._lock:
            lst = self._anchors.get(session_id, [])
            before = len(lst)
            self._anchors[session_id] = [a for a in lst if a.id != anchor_id]
            self._entries.pop(anchor_id, None)
            changed = len(self._anchors[session_id]) != before
            if changed:
                self._save()
            return changed

    # ---- 评论 ----

    def add_entry(self, anchor_id: str, role: str, content: str) -> CommentEntry:
        with self._lock:
            entry = CommentEntry(
                id=new_id("cmt"),
                anchor_id=anchor_id,
                role=role,
                content=content,
                created_at=utc_now(),
            )
            self._entries.setdefault(anchor_id, []).append(entry)
            self._save()
            return entry

    def list_entries(self, anchor_id: str) -> list[CommentEntry]:
        with self._lock:
            return list(self._entries.get(anchor_id, []))

    # ---- 线程视图 ----

    def list_threads(self, session_id: str) -> list[dict]:
        """返回该会话全部线程（锚点+按时间排序的评论），供前端恢复。"""
        threads: list[dict] = []
        for anchor in self.list_anchors(session_id):
            entries = sorted(self.list_entries(anchor.id), key=lambda e: e.created_at)
            threads.append({
                "anchor": anchor.model_dump(mode="json"),
                "entries": [e.model_dump(mode="json") for e in entries],
            })
        threads.sort(key=lambda t: t["anchor"]["created_at"])
        return threads
