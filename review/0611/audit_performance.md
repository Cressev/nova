# 性能 审查报告

## 发现的问题

1. **逐字符读取管道导致 I/O 性能极差**
   - **位置**: `src/nova_gateway/processes/manager.py:232`
   - **触发条件**: 每次执行前台 shell 命令时都会触发
   - **潜在后果**: `pipe.read(1)` 逐字节读取，每次系统调用只返回 1 字节，产生大量 syscall 开销。对于高输出命令，吞吐量可下降 100 倍以上。
   - **修复方案**: 使用带缓冲的读取替代逐字符读取
     ```python
     def read(self) -> None:
         while True:
             chunk = pipe.read(self.chunk_size)
             if not chunk:
                 break
             output_queue.put((stream, chunk))
     ```

2. **每次消息写入都全量序列化并落盘整个聊天历史**
   - **位置**: `src/nova_gateway/sessions/store.py:184-195` (`add_chat_message`)
   - **触发条件**: 每次用户发送消息或收到模型回复时触发
   - **潜在后果**: `_save_chats()` 将所有 session、所有 message、所有 event 序列化为 JSON 并写磁盘。随着会话增长，单次写入的序列化和 I/O 成本线性增加，长会话下可导致数百毫秒延迟。
   - **修复方案**: 仅追加新消息到文件，而非每次全量重写
     ```python
     def add_chat_message(self, message: ChatMessage) -> ChatMessage:
         with self._lock:
             if message.session_id not in self._chat_sessions:
                 raise KeyError(message.session_id)
             self._chat_messages.setdefault(message.session_id, []).append(message)
             session = self._chat_sessions[message.session_id]
             self._chat_sessions[session_id] = session.model_copy(
                 update={"updated_at": utc_now()}
             )
             # 仅追加新消息，不全量重写
             self._append_chat_message_to_file(message)
             self._update_session_in_file(session)
             return message
     ```

3. **每次工具执行都重建完整工具规格字典**
   - **位置**: `src/nova_gateway/tools/executor.py:659-660` (`_tool_spec_data`)
   - **触发条件**: 每次工具执行（包括 start 和 done 事件）都会调用
   - **潜在后果**: `_tool_spec_data` 内部调用 `self.tools.list_specs()`，该方法遍历所有 `TOOL_SPECS` 并且每次创建新的 `McpManager` 实例读取 MCP 配置。一次工具执行会产生 2 次调用（start + done），多轮对话中累积开销显著。
   - **修复方案**: 在 `ToolExecutor.__init__` 中缓存 specs 字典
     ```python
     def __init__(self, tools, *, hooks=None, process_manager=None):
         self.tools = tools
         self.hooks = hooks or ToolHookRunner(cwd=tools.project_root)
         self.process_manager = process_manager or ProcessManager()
         self._spec_cache: dict[str, dict] = {}
         self._build_spec_cache()

     def _build_spec_cache(self):
         dynamic = {item["name"]: item for item in self.tools.list_specs()}
         for name, spec in TOOL_SPECS.items():
             if name in dynamic:
                 continue
             dynamic[name] = {
                 "name": spec.name, "description": spec.description,
                 "permission": spec.permission, "risk": spec.risk,
                 "category": spec.category, "schema": spec.schema,
                 "supports_parallel": spec.supports_parallel,
                 "interrupt_behavior": spec.interrupt_behavior,
             }
         self._spec_cache = dynamic
     ```

4. **`supports_parallel` 每次为 MCP 工具重建完整 specs 列表**
   - **位置**: `src/nova_gateway/tools/workspace.py:374-376`
   - **触发条件**: 每次并行工具调用判断时触发
   - **潜在后果**: 对 MCP 工具调用 `self.list_specs()`，该方法每次创建新的 `McpManager` 并读取配置文件，O(n) 遍历生成列表后线性搜索。在多工具并行场景下重复开销大。
   - **修复方案**: 缓存 `list_specs()` 结果或改用 set 查找
     ```python
     def supports_parallel(self, name: str) -> bool:
         if name.startswith("mcp__"):
             specs = {item["name"]: item for item in self.list_specs()}
             return bool(specs.get(name, {}).get("supports_parallel"))
         spec = TOOL_SPECS.get(name)
         return bool(spec and spec.supports_parallel and spec.read_only)
     ```

5. **`list_specs` 每次创建新的 `McpManager` 实例读取磁盘配置**
   - **位置**: `src/nova_gateway/tools/workspace.py:367`
   - **触发条件**: 每次调用 `list_specs()` 时触发
   - **潜在后果**: `McpManager(self.project_root)` 在每次调用时实例化，需要读取并解析 MCP 配置文件。`list_specs` 被 `supports_parallel`、`_tool_spec_data`、`_permission_for` 等多处高频调用，导致大量冗余磁盘 I/O。
   - **修复方案**: 在 `WorkspaceTools.__init__` 中创建并缓存 `McpManager`
     ```python
     def __init__(self, project_root, *, permission_mode="workspace_write", sandbox_mode=None, network_access=False):
         self.project_root = project_root.resolve()
         self.permission_mode = permission_mode
         self.sandbox_mode = sandbox_mode or ("read_only" if permission_mode == "read_only" else "workspace_write")
         self.network_access = network_access
         self._mcp_manager = McpManager(self.project_root)
         self._specs_cache: list[dict] | None = None

     def list_specs(self) -> list[dict[str, Any]]:
         if self._specs_cache is not None:
             return self._specs_cache
         local_specs = [...]
         self._specs_cache = [*local_specs, *self._mcp_manager.list_tool_specs()]
         return self._specs_cache
     ```

6. **系统提示构建时每次从磁盘读取多个记忆/人格文件**
   - **位置**: `src/nova_gateway/memory/project.py:31-53` (`context()`)
   - **触发条件**: 每次 Agent 决策轮次调用 `_system_prompt()` 时触发
   - **潜在后果**: `context()` 遍历 agent 指令源、人格文件、记忆文件，每个都从磁盘读取。在多轮工具调用循环中（最多 6-12 轮），每轮都重复读取相同的文件。
   - **修复方案**: 添加带 TTL 的文件内容缓存
     ```python
     def __init__(self, project_root, *, global_agent_file=None, max_chars=8000):
         # ... existing init ...
         self._context_cache: tuple[float, str] | None = None
         self._context_cache_ttl = 5.0  # 秒

     def context(self) -> str:
         import time
         now = time.monotonic()
         if self._context_cache and (now - self._context_cache[0]) < self._context_cache_ttl:
             return self._context_cache[1]
         # ... existing logic ...
         result = "\n\n".join(parts)
         self._context_cache = (now, result)
         return result
     ```

7. **`_chat_timeline_items` 每次全量加载并排序所有消息和事件**
   - **位置**: `src/nova_gateway/main.py:1031-1038`
   - **触发条件**: 每次请求 `/api/chat/sessions/{id}/timeline` 或 `/api/chat/sessions/{id}/runtime-state` 时触发
   - **潜在后果**: 加载会话的全部消息和事件到内存，合并后排序。长会话下（数百条消息），每次请求都做 O(n log n) 排序和 O(n) 内存分配。
   - **修复方案**: 在 store 层维护已排序的 timeline，或仅返回最近 N 条
     ```python
     def _chat_timeline_items(session_id: str, limit: int = 200) -> list[dict]:
         items: list[dict] = []
         for message in store.list_chat_messages(session_id):
             items.append({"kind": "message", "created_at": message.created_at, "item": message.model_dump(mode="json")})
         for event in store.list_chat_events(session_id):
             items.append({"kind": "event", "created_at": event.created_at, "item": event.model_dump(mode="json")})
         items.sort(key=lambda item: item["created_at"])
         items = items[-limit:]  # 只返回最近的条目
         return [{"kind": item["kind"], "item": item["item"]} for item in items]
     ```

8. **`BigModelProvider` 每次 API 调用都创建新的 OpenAI 客户端**
   - **位置**: `src/nova_gateway/providers/bigmodel.py:94-101` (`_openai_client`)
   - **触发条件**: 每次 `complete`、`stream`、`complete_with_tools` 调用时触发
   - **潜在后果**: 每次创建 `AsyncOpenAI` 实例会新建 HTTP 连接池，无法复用 TCP 连接。在多轮工具调用中，每轮至少调用一次模型 API，每次都重新建立连接。
   - **修复方案**: 缓存客户端实例
     ```python
     def __init__(self, *, base_url=None, model=None, api_key_env="BIGMODEL_API_KEY", api_key_file=None, client_factory=None):
         # ... existing init ...
         self._client: Any | None = None
         self._client_api_key: str | None = None

     def _openai_client(self, api_key: str) -> Any:
         if self._client is not None and self._client_api_key == api_key:
             return self._client
         if self._client_factory is not None:
             client = self._client_factory(api_key)
         else:
             try:
                 from openai import AsyncOpenAI
             except ImportError as exc:
                 raise ProviderError("缺少 openai SDK，请安装依赖后重启 Nova。") from exc
             client = AsyncOpenAI(base_url=self.base_url, api_key=api_key, timeout=60)
         self._client = client
         self._client_api_key = api_key
         return client
     ```
