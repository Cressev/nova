from __future__ import annotations

import ast
import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PythonSymbol:
    name: str
    kind: str
    path: str
    line: int
    character: int


class LspManager:
    """Nova 的最小 LSP 能力适配层。

    参考 Crush 的 LSP 资源模型，这里先暴露状态、诊断和定义查询三个稳定接口。
    当前 Python 能力用标准库 AST/compile 实现，后续可以在这个边界后面替换为
    pyright、ruff-lsp 或真实 JSON-RPC language server。
    """

    PYTHON_MARKERS = ["pyproject.toml", "setup.py", "requirements.txt", "poetry.lock", "uv.lock"]

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def status(self) -> dict[str, Any]:
        languages = self.detect_languages()
        servers: list[dict[str, Any]] = []
        if "python" in languages:
            counts = self._diagnostic_summary(self.python_diagnostics())
            sample_paths = [self._display(path) for path in self._iter_files("*.py", limit=8)]
            sample_symbol = self._first_python_symbol()
            servers.append(
                {
                    "name": "python",
                    "language": "python",
                    "state": "ready",
                    "transport": "builtin-ast",
                    "diagnostic_count": sum(counts.values()),
                    "diagnostics": counts,
                    "capabilities": ["diagnostics", "definition"],
                    "root_markers": [marker for marker in self.PYTHON_MARKERS if (self.project_root / marker).exists()],
                    "sample_paths": sample_paths,
                    "sample_symbol": sample_symbol.name if sample_symbol else "",
                    "sample_definition": self._symbol_payload(sample_symbol) if sample_symbol else None,
                }
            )
        return {
            "enabled": True,
            "project_root": str(self.project_root),
            "languages": languages,
            "servers": servers,
            "notes": "当前 Python 链路使用内置 AST/compile；后续可切换真实 LSP server。",
        }

    def detect_languages(self) -> list[str]:
        languages: list[str] = []
        has_python_marker = any((self.project_root / marker).exists() for marker in self.PYTHON_MARKERS)
        has_python_file = any(self._iter_files("*.py", limit=1))
        if has_python_marker or has_python_file:
            languages.append("python")
        return languages

    def diagnostics(self, path: str | None = None) -> dict[str, Any]:
        diagnostics = self.python_diagnostics(path=path)
        return {
            "language": "python" if "python" in self.detect_languages() else "unknown",
            "project_root": str(self.project_root),
            "path": path,
            "summary": self._diagnostic_summary(diagnostics),
            "diagnostics": diagnostics,
        }

    def definition(self, *, path: str, symbol: str) -> dict[str, Any]:
        target_file = self._resolve_workspace_path(path)
        if not symbol.strip():
            return {"ok": False, "error": "symbol 不能为空", "definition": None}
        if not target_file.exists():
            return {"ok": False, "error": f"文件不存在：{path}", "definition": None}
        definition = self._find_python_symbol(symbol.strip(), preferred_file=target_file)
        if definition is None:
            return {"ok": False, "error": f"未找到定义：{symbol}", "definition": None}
        return {
            "ok": True,
            "language": "python",
            "symbol": symbol.strip(),
            "definition": self._symbol_payload(definition),
        }

    def python_diagnostics(self, path: str | None = None) -> list[dict[str, Any]]:
        files = [self._resolve_workspace_path(path)] if path else list(self._iter_files("*.py", limit=300))
        diagnostics: list[dict[str, Any]] = []
        for file_path in files:
            if not file_path.exists() or not file_path.is_file():
                diagnostics.append(
                    self._diagnostic(
                        path=self._display(file_path),
                        severity="error",
                        message=f"文件不存在：{self._display(file_path)}",
                        line=1,
                        character=1,
                        source="nova-lsp",
                    )
                )
                continue
            try:
                source = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                diagnostics.append(
                    self._diagnostic(
                        path=self._display(file_path),
                        severity="warning",
                        message="文件不是 UTF-8 文本，已跳过 Python 诊断。",
                        line=1,
                        character=1,
                        source="nova-lsp",
                    )
                )
                continue
            try:
                compile(source, str(file_path), "exec", ast.PyCF_ONLY_AST)
            except SyntaxError as exc:
                diagnostics.append(
                    self._diagnostic(
                        path=self._display(file_path),
                        severity="error",
                        message=exc.msg or "invalid syntax",
                        line=exc.lineno or 1,
                        character=exc.offset or 1,
                        source="python-compile",
                    )
                )
        return diagnostics

    def _find_python_symbol(self, symbol: str, *, preferred_file: Path) -> PythonSymbol | None:
        candidates = [preferred_file, *[path for path in self._iter_files("*.py", limit=300) if path != preferred_file]]
        for file_path in candidates:
            found = self._symbols_in_file(file_path)
            for item in found:
                if item.name == symbol:
                    return item
        return None

    def _first_python_symbol(self) -> PythonSymbol | None:
        for file_path in self._iter_files("*.py", limit=300):
            symbols = self._symbols_in_file(file_path)
            if symbols:
                return symbols[0]
        return None

    def _symbols_in_file(self, file_path: Path) -> list[PythonSymbol]:
        symbols: list[PythonSymbol] = []
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            return symbols
        for node in ast.walk(tree):
            kind = ""
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "function"
            elif isinstance(node, ast.ClassDef):
                kind = "class"
            if kind:
                symbols.append(
                    PythonSymbol(
                        name=node.name,
                        kind=kind,
                        path=self._display(file_path),
                        line=int(getattr(node, "lineno", 1)),
                        character=int(getattr(node, "col_offset", 0)) + 1,
                    )
                )
        return sorted(symbols, key=lambda item: (item.path, item.line, item.character))

    def _symbol_payload(self, symbol: PythonSymbol) -> dict[str, Any]:
        return {
            "symbol": symbol.name,
            "kind": symbol.kind,
            "path": symbol.path,
            "range": {
                "start": {"line": symbol.line, "character": symbol.character},
                "end": {"line": symbol.line, "character": symbol.character + len(symbol.name)},
            },
        }

    def _diagnostic(
        self,
        *,
        path: str,
        severity: str,
        message: str,
        line: int,
        character: int,
        source: str,
    ) -> dict[str, Any]:
        return {
            "path": path,
            "severity": severity,
            "message": message,
            "source": source,
            "range": {
                "start": {"line": max(1, line), "character": max(1, character)},
                "end": {"line": max(1, line), "character": max(1, character + 1)},
            },
        }

    def _diagnostic_summary(self, diagnostics: list[dict[str, Any]]) -> dict[str, int]:
        summary = {"error": 0, "warning": 0, "information": 0, "hint": 0}
        for diagnostic in diagnostics:
            severity = str(diagnostic.get("severity") or "information")
            summary[severity if severity in summary else "information"] += 1
        return summary

    def _iter_files(self, pattern: str, *, limit: int) -> list[Path]:
        files: list[Path] = []
        ignored_dirs = {".git", ".nova", "__pycache__", "references", "output", ".playwright-cli", "node_modules"}
        for current_root, dirnames, filenames in os.walk(self.project_root):
            dirnames[:] = [dirname for dirname in dirnames if dirname not in ignored_dirs]
            for filename in filenames:
                if not fnmatch.fnmatch(filename, pattern):
                    continue
                files.append(Path(current_root) / filename)
                if len(files) >= limit:
                    return files
        return files

    def _resolve_workspace_path(self, raw_path: str | None) -> Path:
        if not raw_path:
            return self.project_root
        candidate = (self.project_root / raw_path).resolve()
        if candidate != self.project_root and self.project_root not in candidate.parents:
            raise ValueError(f"路径越界：{raw_path}")
        return candidate

    def _display(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            return str(path)
