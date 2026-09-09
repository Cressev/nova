# 逻辑与Bug 审查报告

## 发现的问题

1. **[流式对话 TOCTOU 竞态条件：并发请求可同时进入同一 session 的 turn]**
   - **位置**: `main.py:1130-1142` / `stream_chat_message`
   - **触发条件**: 两个请求几乎同时对同一 `session_id` 发起 `stream_chat_message`，第一个请求通过 `is_active` 检查后、调用 `mark_active` 前，第二个请求也通过了 `is_active` 检查。
   - **潜在后果**: 同一 session 并发执行两个 agent turn，导致消息乱序、状态混乱、`answer_parts` 交叉写入错误 session。
   - **修复方案**:
     ```python
     # 将 is_active + mark_active 合并为原子操作
     if not agent_sessions.try_mark_active(session_id):
         queued = ChatMessage(
             session_id=session_id,
             role=ChatRole.USER,
             content=payload.content,
         )
         store.add_chat_message(queued)
         agent_sessions.enqueue_message(session_id, queued)
         return JSONResponse(
             status_code=202,
             content={"ok": True, "status": "queued", "message": queued.model_dump(mode="json")},
         )
     ```
     需要在 `AgentSessionService` 中新增 `try_mark_active` 方法，用锁保护 `is_active` + `mark_active` 为原子操作。

2. **[`_normalize_runtime_config` 对非数字字符串抛出未捕获 ValueError]**
   - **位置**: `main.py:221-222` / `_normalize_runtime_config`
   - **触发条件**: 用户通过 `/api/runtime/config` PATCH 接口提交 `max_tool_rounds` 或 `context_window_tokens` 为非数字字符串（如 `"abc"`）。
   - **潜在后果**: `int("abc")` 抛出 `ValueError`，导致 500 Internal Server Error，配置接口完全不可用。
   - **修复方案**:
     ```python
     try:
         config["max_tool_rounds"] = max(1, min(int(config["max_tool_rounds"]), 12))
     except (TypeError, ValueError):
         config["max_tool_rounds"] = _RUNTIME_BASE_CONFIG["max_tool_rounds"]
     try:
         config["context_window_tokens"] = max(8192, min(int(config["context_window_tokens"]), 1000000))
     except (TypeError, ValueError):
         config["context_window_tokens"] = _RUNTIME_BASE_CONFIG["context_window_tokens"]
     ```

3. **[审批工具调用时硬编码 `workspace_write` 权限，绕过实际权限模式]**
   - **位置**: `main.py:610-615` / `approve_tool_call`
   - **触发条件**: 用户在 `ask` 模式下审批一个工具调用。
   - **潜在后果**: 审批通过后创建的 `WorkspaceTools` 使用 `permission_mode="workspace_write"` 而非当前实际权限模式，意味着审批后的工具调用获得了超出预期的权限。如果用户期望审批后仍受 `ask` 模式约束，实际行为会越权。
   - **修复方案**:
     ```python
     tools = WorkspaceTools(
         workspace_manager.current_root,
         permission_mode=settings.permission_mode,
         sandbox_mode=settings.sandbox_mode,
         network_access=settings.network_access,
     )
     ```

4. **[`_drain_background` 与 reader 线程竞态，可能丢失尾部输出]**
   - **位置**: `manager.py:246-257` / `_drain_background`
   - **触发条件**: 后台进程快速退出，reader 线程尚未将缓冲区内容写入 `output_queue`。
   - **潜在后果**: `job.process.poll() is not None` 为 True 且 `output_queue.empty()` 也为 True（reader 线程还在缓冲），循环提前退出，进程尾部输出丢失。
   - **修复方案**:
     ```python
     def _drain_background(self, job: ProcessJob, output_queue: queue.Queue[tuple[str, str | None]]) -> None:
         while job.process.poll() is None or not output_queue.empty():
             try:
                 stream, chunk = output_queue.get(timeout=0.1)
             except queue.Empty:
                 continue
             if chunk:
                 self._append(job, stream, chunk)
         # 等待 reader 线程完成，确保所有缓冲数据已写入队列
         for pipe_attr in ("stdout", "stderr"):
             pipe = getattr(job.process, pipe_attr, None)
             if pipe is not None:
                 try:
                     pipe.close()
                 except Exception:
                     pass
         # 再次排空队列中可能残留的数据
         while not output_queue.empty():
             try:
                 stream, chunk = output_queue.get_nowait()
                 if chunk:
                     self._append(job, stream, chunk)
             except queue.Empty:
                 break
         with self._lock:
             terminal_status = job.status if job.status in {"killed", "cancelled", "timeout"} else None
         self._finish(job, terminal_status or ("completed" if job.process.returncode == 0 else "failed"))
     ```

5. **[TaskStore 文件写入非原子化，进程崩溃可导致数据全丢]**
   - **位置**: `store.py:60-63` (`_save`) 和 `store.py:85-88` (`_save_chats`)
   - **触发条件**: 写入过程中进程被 kill、断电、磁盘满。
   - **潜在后果**: JSON 文件写入一半后中断，文件内容损坏。下次启动时 `_load` 捕获 `JSONDecodeError` 返回空结构，所有历史会话和任务数据丢失。
   - **修复方案**:
     ```python
     import tempfile

     def _atomic_write_json(path: Path, payload: dict) -> None:
         path.parent.mkdir(parents=True, exist_ok=True)
         fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
         try:
             with os.fdopen(fd, "w", encoding="utf-8") as f:
                 json.dump(payload, f, ensure_ascii=False, indent=2)
             os.replace(tmp_path, path)
         except Exception:
             try:
                 os.unlink(tmp_path)
             except OSError:
                 pass
             raise

     def _save(self) -> None:
         payload = {
             "tasks": [
                 task.model_dump(mode="json")
                 for task in sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)
             ]
         }
         _atomic_write_json(self.session_file, payload)

     def _save_chats(self) -> None:
         payload = {
             "sessions": [
                 session.model_dump(mode="json")
                 for session in sorted(self._chat_sessions.values(), key=lambda item: item.updated_at, reverse=True)
             ],
             "messages": {
                 sid: [m.model_dump(mode="json") for m in msgs]
                 for sid, msgs in self._chat_messages.items()
             },
             "events": {
                 sid: [e.model_dump(mode="json") for e in evts]
                 for sid, evts in self._chat_events.items()
             },
         }
         _atomic_write_json(self.chat_file, payload)
     ```

6. **[`restart_runtime` 在 Windows 上使用 `os.execv` 行为未定义]**
   - **位置**: `main.py:319` / `restart_runtime`
   - **触发条件**: 在 Windows 平台上调用 `/api/runtime/restart`。
   - **潜在后果**: Windows 上 `os.execv` 不替换进程而是 spawn 新进程但不正确释放原进程资源，可能导致端口占用、僵尸进程或直接崩溃。
   - **修复方案**:
     ```python
     def delayed_restart() -> None:
         time.sleep(0.35)
         if os.name == "nt":
             import multiprocessing
             multiprocessing.Process(target=lambda: os.execv(sys.executable, [sys.executable, *sys.argv])).start()
             sys.exit(0)
         else:
             os.execv(sys.executable, [sys.executable, *sys.argv])
     ```

7. **[`_subagent_runner` 在已运行的事件循环中调用 `asyncio.run()` 会抛 RuntimeError]**
   - **位置**: `main.py:161` / `_subagent_runner`
   - **触发条件**: `subagent_manager.default_runner` 从一个已有事件循环的同步上下文中被调用（例如在 FastAPI 的线程池中直接调用而非通过独立线程）。
   - **潜在后果**: `asyncio.run()` 在已有运行中的事件循环时抛出 `RuntimeError: This event loop is already running`，子 Agent 完全无法执行。
   - **修复方案**:
     ```python
     try:
         loop = asyncio.get_running_loop()
     except RuntimeError:
         loop = None

     if loop and loop.is_running():
         import concurrent.futures
         with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
             return pool.submit(lambda: asyncio.run(asyncio.wait_for(collect(), timeout=20.0))).result()
     else:
         return asyncio.run(asyncio.wait_for(collect(), timeout=20.0))
     ```
