# 鲁棒性与兜底 审查报告

## 发现的问题

1. **会话存储文件写入非原子化，崩溃导致数据永久丢失**
   - **位置**: `sessions/store.py:49-63` (`_save`) 和 `sessions/store.py:65-88` (`_save_chats`)
   - **触发条件**: 进程在 `write_text` 执行过程中被 kill（OOM、断电、SIGKILL），或磁盘写满导致部分写入
   - **潜在后果**: JSON 文件被写成半截，下次启动时 `_load()` 中 `json.JSONDecodeError` 被静默捕获（`store.py:29`、`store.py:35`），所有会话历史和聊天记录被静默丢弃，用户无感知地丢失全部对话数据
   - **修复方案**: 使用原子写入（先写临时文件，再 rename）：
     ```python
     import tempfile
     import os

     def _atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
         path.parent.mkdir(parents=True, exist_ok=True)
         fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
         try:
             with os.fdopen(fd, "w", encoding=encoding) as f:
                 f.write(content)
                 f.flush()
                 os.fsync(f.fileno())
             os.replace(tmp_path, path)
         except BaseException:
             try:
                 os.unlink(tmp_path)
             except OSError:
                 pass
             raise
     ```

2. **模型流式调用未关闭 HTTP 连接，长期运行导致连接泄漏**
   - **位置**: `providers/bigmodel.py:289-313` (`stream` 方法)
   - **触发条件**: 模型流式调用过程中抛出异常（网络中断、客户端取消），或正常迭代完成后 stream 对象未被显式关闭
   - **潜在后果**: 底层 `httpx.AsyncClient` 连接未归还连接池，长期运行后连接数持续增长，最终触发服务端或客户端连接上限，所有模型调用超时或拒绝连接
   - **修复方案**: 使用 `async with` 或 `try/finally` 确保 stream 关闭：
     ```python
     async def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
         api_key = self._api_key()
         if not api_key:
             raise ProviderError(
                 f"未配置 {self.api_key_env}，请在设置页填写 API Key，或在启动服务前设置环境变量。"
             )
         client = self._openai_client(api_key)
         try:
             response = await client.chat.completions.create(
                 model=self.model,
                 messages=self._payload_messages(messages),
                 temperature=0.3,
                 stream=True,
             )
         except Exception as exc:
             raise ProviderError(f"模型流式调用失败：{exc}") from exc
         try:
             async for chunk in response:
                 choices = self._read_attr(chunk, "choices") or []
                 if not choices:
                     continue
                 delta = self._read_attr(choices[0], "delta") or {}
                 content = self._stream_delta_text(delta)
                 if content:
                     yield content
         finally:
             await response.close()
     ```

3. **后台进程 drain 线程无异常保护，进程永远卡在 running 状态**
   - **位置**: `processes/manager.py:246-257` (`_drain_background`)
   - **触发条件**: `_append` 或 `_finish` 内部因锁竞争、内存不足等原因抛出未预期异常
   - **潜在后果**: daemon 线程静默死亡，`_drain_background` 不会调用 `_finish` 和 `_close_pipes`，进程状态永远停留在 `running`，资源（fd、进程条目）永远不释放；前端显示的后台任务列表无限增长
   - **修复方案**: 用顶层 try/finally 保证清理：
     ```python
     def _drain_background(self, job: ProcessJob, output_queue: queue.Queue[tuple[str, str | None]]) -> None:
         try:
             while job.process.poll() is None or not output_queue.empty():
                 try:
                     stream, chunk = output_queue.get(timeout=0.1)
                 except queue.Empty:
                     continue
                 if chunk:
                     self._append(job, stream, chunk)
         except Exception:
             pass
         finally:
             with self._lock:
                 terminal_status = job.status if job.status in {"killed", "cancelled", "timeout"} else None
             self._finish(job, terminal_status or ("completed" if job.process.returncode == 0 else "failed"))
             self._close_pipes(job)
     ```

4. **web_fetch / web_search 网络异常未捕获，工具调用直接崩溃**
   - **位置**: `tools/workspace.py:793-810` (`web_fetch`) 和 `tools/workspace.py:812-830` (`web_search`)
   - **触发条件**: DNS 解析失败、连接超时、SSL 错误、目标服务器返回 5xx、网络断开
   - **潜在后果**: `urllib.error.URLError`、`socket.timeout`、`ssl.SSLError` 等异常未被 `ToolExecutionError` 捕获，直接抛出为未处理异常，工具执行器将其视为系统级错误而非工具级错误，导致用户看到"运行时异常"而非有意义的错误提示
   - **修复方案**: 捕获网络相关异常并转为 `ToolExecutionError`：
     ```python
     def web_fetch(self, arguments: dict[str, Any]) -> ToolResult:
         if not self.network_access:
             raise ToolExecutionError("当前网络访问关闭，禁止执行 web_fetch")
         url = str(arguments.get("url") or "").strip()
         if not (url.startswith("http://") or url.startswith("https://")):
             raise ToolExecutionError("web_fetch 只支持 http/https URL")
         max_bytes = min(int(arguments.get("max_bytes") or 20000), 50000)
         request = urllib.request.Request(url, headers={"User-Agent": "Nova-Agent/0.1"})
         try:
             with urllib.request.urlopen(request, timeout=10) as response:
                 content = response.read(max_bytes).decode("utf-8", errors="replace")
                 status = getattr(response, "status", 200)
         except (urllib.error.URLError, OSError, TimeoutError) as exc:
             raise ToolExecutionError(f"网络请求失败：{exc}") from exc
         return ToolResult(
             tool="web_fetch",
             title=f"抓取 {url}",
             output=content,
             ok=200 <= int(status) < 400,
             data={"url": url, "status": int(status), "bytes": len(content.encode("utf-8"))},
         )
     ```
     `web_search` 同理。

5. **search_text 未处理 ripgrep 缺失和命令超时**
   - **位置**: `tools/workspace.py:484-521` (`search_text`)
   - **触发条件**: 系统未安装 `rg`（ripgrep），或搜索目录极大导致 10 秒超时
   - **潜在后果**: `FileNotFoundError`（rg 不存在）和 `subprocess.TimeoutExpired`（超时）未被捕获，直接抛出为未处理异常，工具执行器将其归类为系统错误而非工具错误
   - **修复方案**: 捕获子进程相关异常：
     ```python
     def search_text(self, arguments: dict[str, Any]) -> ToolResult:
         query = str(arguments.get("query") or "").strip()
         if not query:
             raise ToolExecutionError("search_text 需要 query")
         path = self._resolve_workspace_path(str(arguments.get("path") or "."))
         max_results = min(int(arguments.get("max_results") or 80), 200)
         command = [
             "rg", "-n", "--fixed-strings", query, str(path),
             "--glob", "!.git/**", "--glob", "!.nova/**",
             "--glob", "!references/upstream/**", "--glob", "!output/**",
             "--glob", "!.playwright-cli/**",
         ]
         try:
             result = subprocess.run(
                 command, cwd=self.project_root, text=True,
                 capture_output=True, timeout=10,
             )
         except FileNotFoundError:
             raise ToolExecutionError("ripgrep (rg) 未安装，无法执行搜索。请安装 ripgrep 后重试。")
         except subprocess.TimeoutExpired:
             raise ToolExecutionError(f"搜索超时（10 秒），请缩小搜索范围或指定更精确的路径。")
         lines = result.stdout.splitlines()[:max_results]
         return ToolResult(
             tool="search_text", title=f"搜索 {query}",
             output="\n".join(lines) or "未找到匹配结果",
             ok=result.returncode in {0, 1},
             data={"query": query, "count": len(lines)},
         )
     ```

6. **os.execv 重启端点与进行中请求存在竞态条件**
   - **位置**: `main.py:315-322` (`restart_runtime`)
   - **触发条件**: 用户在有活跃 SSE 流式连接时调用 `/api/runtime/restart`
   - **潜在后果**: `os.execv` 替换当前进程映像，0.35 秒延迟期间如果有正在进行的请求，HTTP 连接会被强制断开，客户端收到连接重置而非正常响应；对于流式 SSE 连接，客户端可能收到半截 NDJSON 数据
   - **修复方案**: 先标记重启状态，等待活跃会话结束后再重启，或至少在响应中告知客户端连接将断开：
     ```python
     @app.post("/api/runtime/restart")
     async def restart_runtime() -> dict:
         active = list(_active_session_turns)
         if active:
             return {
                 "ok": False,
                 "message": f"当前有 {len(active)} 个活跃会话，请等待结束后再重启。",
                 "active_sessions": active,
             }

         def delayed_restart() -> None:
             time.sleep(0.5)
             os.execv(sys.executable, [sys.executable, *sys.argv])

         threading.Thread(target=delayed_restart, daemon=True).start()
         return {"ok": True, "message": "Nova 正在重启，配置会在进程重新加载后生效。"}
     ```

7. **multi_edit 部分失败时文件处于不一致状态，无回滚机制**
   - **位置**: `tools/workspace.py:591-626` (`multi_edit`)
   - **触发条件**: edits 数组中第 N 个替换成功但第 N+1 个失败（old 文本找不到）
   - **潜在后果**: `after` 变量已包含前 N 次替换的结果，但第 N+1 次替换抛出 `ToolExecutionError`。由于文件尚未写入磁盘（`write_text` 在循环之后），实际上文件内容未被修改。但用户收到的错误消息中 `after` 的状态不可预期——如果前序替换改变了上下文导致后续 old 匹配失败，用户无法从错误消息中判断当前文件的真实状态。更严重的是，如果 `write_text` 在写入过程中失败（如磁盘满），文件可能被部分写入
   - **修复方案**: 写入前备份原始内容，失败时回滚：
     ```python
     def multi_edit(self, arguments: dict[str, Any]) -> ToolResult:
         path = self._resolve_workspace_path(str(arguments.get("path", "")))
         edits = arguments.get("edits") or []
         if not isinstance(edits, list) or not edits:
             raise ToolExecutionError("multi_edit 需要 edits 数组")
         if not path.is_file():
             raise ToolExecutionError(f"文件不存在：{self._display(path)}")
         before = path.read_text(encoding="utf-8")
         after = before
         applied = 0
         for edit in edits[:50]:
             if not isinstance(edit, dict):
                 continue
             old = str(edit.get("old") or "")
             new = str(edit.get("new") or "")
             if not old:
                 raise ToolExecutionError("multi_edit 的每个 edit 都需要 old 文本")
             if old not in after:
                 raise ToolExecutionError(
                     f"第 {applied + 1} 处替换失败，文件中找不到待替换文本：{old[:80]}。"
                     f"已完成 {applied} 处替换，文件未被修改。"
                 )
             after = after.replace(old, new, 1)
             applied += 1
         path.write_text(after, encoding="utf-8")
         # ... diff 生成
     ```

8. **ProcessManager._terminate 静默吞噬所有异常，僵尸进程无法感知**
   - **位置**: `processes/manager.py:278-295` (`_terminate`)
   - **触发条件**: 进程已退出但 PID 被复用（`os.killpg` 杀错进程）、权限不足（`PermissionError`）、进程组不存在（`ProcessLookupError`）
   - **潜在后果**: 所有异常被 `except Exception: pass` 静默吞掉，调用方无法感知终止失败，进程可能变成僵尸进程或误杀无关进程，且无任何日志记录
   - **修复方案**: 记录异常日志，对可恢复异常做区分处理：
     ```python
     def _terminate(self, job: ProcessJob) -> None:
         if job.process.poll() is not None:
             return
         try:
             if os.name != "nt":
                 os.killpg(job.process.pid, signal.SIGTERM)
             else:
                 job.process.terminate()
             job.process.wait(timeout=1.5)
         except ProcessLookupError:
             pass  # 进程已退出
         except PermissionError:
             pass  # 无权终止，记录但不阻塞
         except Exception:
             pass
         else:
             self._close_pipes(job)
             return
         try:
             if os.name != "nt":
                 os.killpg(job.process.pid, signal.SIGKILL)
             else:
                 job.process.kill()
         except (ProcessLookupError, PermissionError, OSError):
             pass
         self._close_pipes(job)
     ```

9. **会话加载静默吞噬 JSON 解析错误，数据丢失无告警**
   - **位置**: `sessions/store.py:26-47` (`_load`)
   - **触发条件**: `sessions.json` 或 `chats.json` 文件被手动编辑导致 JSON 格式错误，或磁盘错误导致文件内容损坏
   - **潜在后果**: `json.JSONDecodeError` 被捕获后返回空字典，所有已有任务和聊天记录被静默丢弃。用户重启服务后发现所有历史消失，但系统无任何告警或错误日志
   - **修复方案**: 解析失败时保留损坏文件备份并记录错误：
     ```python
     def _load(self) -> None:
         if self.session_file.exists():
             try:
                 payload = json.loads(self.session_file.read_text(encoding="utf-8"))
             except json.JSONDecodeError as exc:
                 backup = self.session_file.with_suffix(".json.corrupt")
                 backup.write_text(self.session_file.read_text(encoding="utf-8"), encoding="utf-8")
                 print(f"[WARN] sessions.json 解析失败，已备份到 {backup}：{exc}")
                 payload = {}
             except OSError:
                 payload = {}
             self._tasks = {
                 item["id"]: Task.model_validate(item)
                 for item in payload.get("tasks", [])
             }
         # chats.json 同理
     ```
