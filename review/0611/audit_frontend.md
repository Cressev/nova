# 前端代码审查报告

## 发现的问题

1. **[XSS: 命令面板 command.name 和 command.description 未转义]**
   - **位置**: `static/js/app.js:3571-3574` / `renderCommandPalette()`
   - **维度**: 安全
   - **触发条件**: `/api/commands` 接口返回的 `name` 或 `description` 包含恶意 HTML/JS（如 MITM 攻击、后端被注入、第三方插件篡改命令描述）
   - **潜在后果**: 攻击者可通过命令面板注入任意 HTML 和 JavaScript，窃取用户输入（包括 API Key）、劫持对话、冒充用户执行工具调用
   - **修复方案**:
     ```javascript
     // 修复前 (第 3571-3574 行)
     item.innerHTML = `
       <strong>${command.name}${hint}</strong>
       <span>${command.description}</span>
     `;

     // 修复后
     item.innerHTML = `
       <strong>${escapeHtml(command.name)}${hint}</strong>
       <span>${escapeHtml(command.description)}</span>
     `;
     ```

2. **[XSS: 运行模式 label 和 description 未转义]**
   - **位置**: `static/js/app.js:628-631` / `renderWorkspace()`
   - **维度**: 安全
   - **触发条件**: `/api/workspace/status` 接口返回的 `modes[].label` 或 `modes[].description` 包含恶意 HTML/JS
   - **潜在后果**: 左侧栏运行模式区域注入任意 HTML/JS，持续存在于页面生命周期内，可窃取用户会话数据
   - **修复方案**:
     ```javascript
     // 修复前 (第 628-631 行)
     item.innerHTML = `
       <strong>${mode.label}</strong>
       <span>${mode.description}</span>
     `;

     // 修复后
     item.innerHTML = `
       <strong>${escapeHtml(mode.label)}</strong>
       <span>${escapeHtml(mode.description)}</span>
     `;
     ```

3. **[XSS: 会话列表 session.title 未转义]**
   - **位置**: `static/js/app.js:1822-1825` / `renderSessionItem()`
   - **维度**: 安全
   - **触发条件**: 用户创建包含 `<script>` 或事件处理器的会话标题（如 `<img onerror=alert(1)>`），或 API 返回被篡改的会话数据
   - **潜在后果**: XSS 注入会持久存在于会话列表中，每次加载页面或切换会话时触发；可通过 localStorage 记忆的会话列表跨刷新持续攻击
   - **修复方案**:
     ```javascript
     // 修复前 (第 1822-1825 行)
     item.innerHTML = `
       <span class="session-main">
         <strong>${shortText(session.title)}</strong>
         <small>${shortText(workspaceDisplayName(session.workspace), 28)}</small>
         <span>${formatTime(session.updated_at)}</span>
       </span>
       ...
     `;

     // 修复后
     item.innerHTML = `
       <span class="session-main">
         <strong>${escapeHtml(shortText(session.title))}</strong>
         <small>${escapeHtml(shortText(workspaceDisplayName(session.workspace), 28))}</small>
         <span>${escapeHtml(formatTime(session.updated_at))}</span>
       </span>
       ...
     `;
     ```

4. **[API 客户端 GET 请求携带 Content-Type: application/json]**
   - **位置**: `static/js/api/client.js:4`
   - **维度**: 逻辑
   - **触发条件**: 所有 GET 请求均携带 `Content-Type: application/json` 头
   - **潜在后果**: 某些 HTTP 代理、CDN 或 WAF 可能拒绝带 Content-Type 的 GET 请求；不符合 HTTP 规范（RFC 9110: GET 请求不应有消息体语义）
   - **修复方案**:
     ```javascript
     export async function api(path, options = {}) {
       const isBodyMethod = options.method && !["GET", "HEAD"].includes(options.method.toUpperCase());
       const headers = isBodyMethod
         ? { "Content-Type": "application/json", ...options.headers }
         : { ...options.headers };
       const response = await fetch(path, { headers, ...options });
       if (!response.ok) {
         const text = await response.text();
         throw new Error(text || response.statusText);
       }
       return response.json();
     }
     ```

5. **[escapeHtml 将换行转为 <br>，在 <pre> 标签中产生双重换行]**
   - **位置**: `static/js/app.js:2495-2500` / `escapeHtml()`
   - **维度**: 逻辑
   - **触发条件**: 任何在 `<pre>` 元素中使用 `escapeHtml()` 的位置（如工具输出 `finishToolEvent` 第 2283 行、进程输出 `inspectProcess` 第 1204 行等）
   - **潜在后果**: 工具输出、diff 预览、进程日志中的换行符被渲染为 `<br>`，而 `<pre>` 本身已保留换行，导致每行之间多出一个空行，破坏代码和日志的格式化显示
   - **修复方案**:
     ```javascript
     export function escapeHtml(value) {
       const div = document.createElement("div");
       div.textContent = value;
       return div.innerHTML;
     }

     // 在需要可视换行的非 <pre> 场景（如 message-content），单独处理：
     function escapeHtmlWithBreaks(value) {
       const div = document.createElement("div");
       div.textContent = value;
       return div.innerHTML.replaceAll("\n", "<br>");
     }
     ```

6. **[流式消费 JSON 解析静默吞错]**
   - **位置**: `static/js/runtime/stream.js:13-17`
   - **维度**: 鲁棒性
   - **触发条件**: 服务端返回的 SSE 行不是合法 JSON（如服务端错误页面、网络中间层注入的 HTML 错误页、缓冲区分割错误）
   - **潜在后果**: 非 JSON 行被静默忽略，用户不会收到任何错误反馈；如果整个流都是非 JSON（如服务端 500 返回 HTML），消息将显示为空白，无任何提示
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

     // 在返回前检查
     if (parseErrors > 0 && ok) {
       // 至少记录一条警告，帮助排查服务端异常
       console.warn(`[stream] ${parseErrors} non-JSON lines skipped`);
     }
     return { ok, rest };
     ```

7. **[deleteSession 未编码 sessionId 用于 URL 路径]**
   - **位置**: `static/js/app.js:1838`
   - **维度**: 安全/鲁棒性
   - **触发条件**: sessionId 包含 `/`、`?`、`#` 等 URL 特殊字符（虽然正常 UUID 不会，但作为防御性编程应考虑）
   - **潜在后果**: 如果 sessionId 包含路径分隔符，DELETE 请求可能发送到错误的 API 端点
   - **修复方案**:
     ```javascript
     // 修复前 (第 1838 行)
     const response = await fetch(`/api/chat/sessions/${sessionId}`, { method: "DELETE" });

     // 修复后
     const response = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
     ```

8. **[renderPermissions 的 value 未转义]**
   - **位置**: `static/js/app.js:693` / `renderPermissions()`
   - **维度**: 安全
   - **触发条件**: 权限数据中 `approval_policy`、`permission_mode` 等字段包含恶意 HTML
   - **潜在后果**: 在权限面板中注入 HTML/JS
   - **修复方案**:
     ```javascript
     // 修复前 (第 693 行)
     item.innerHTML = `<span>${label}</span><strong>${value}</strong>`;

     // 修复后
     item.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value ?? "-"))}</strong>`;
     ```
