# 划词评论问答（旁路解释线程）设计方案

日期：2026-09-15。状态：已实施交付（26/09/15，含单测/API e2e/浏览器 e2e/持久化验证）。

## 功能定位

用户选中 assistant 回复中的任意一段文字，选区上方弹出浮动工具条（解释 / 提问 / 复制），
在右侧评论栏（飞书文档评论形态）与模型展开旁路问答线程。核心价值：解答对回复内容的
疑惑而不稀释主线上下文——评论数据不写入主会话 messages/events，主对话构建上下文时
完全不读评论数据；评论调用模型时把主线对话作为只读背景喂一次（用完即弃）。

## 用户拍板的四个决策（26/09/15）

1. 评论栏布局：右侧挤压式（主对话区变窄让位，可收起，飞书同款）。
2. "解释"按钮：一键立即生成线程（自动替用户提"解释这段内容"），零输入成本。
3. 上下文范围：复用主 agent 的预算裁剪机制——按 context window 预算给主对话背景，
   超预算从最老开始丢弃；评论 agent 不做 auto-compact（自动压缩/摘要）那套复杂逻辑。
4. 持久化：锚点、高亮、整条线程随会话存盘，跨重启保留。

## 交互流程

1. 在 assistant 消息上划词（选区须落在 .message.assistant .message-content 内）。
2. 选区 rect 上方居中弹浮动工具条：解释 | 提问 | 复制。浮条锚定选区而非鼠标
   （飞书/Notion/ChatGPT Canvas 均如此）。
3. 解释：立即建线程，AI 流式作答。
4. 提问：浮条下方长出小输入条，回车提交后评论栏出现线程：
   引用块（选中文字）+ 问题 + AI 打字机流式回答。
5. 线程底部常驻输入框可继续追问，AI 结合引用+主线背景接着答。
6. 被评论文字常驻浅黄高亮；点高亮→右栏滚到线程；点线程引用块→主区滚回原文。
7. 评论栏可整体收起，高亮保留，点高亮随时唤回。

## 锚点定位（关键技术点）

消息是 markdown 渲染的，重排后字符偏移会漂移，采用 W3C Web Annotation 的
TextQuoteSelector 方案（Hypothes.is 同款）：存 quote（选中原文）+ prefix/suffix
（前后各 32 字）+ occurrence（第几次出现）。渲染时字符串匹配重新定位，失配时用
前后文模糊匹配兜底。

## 数据模型（独立存储，与 chats.json 分开）

- CommentAnchor：id、session_id、message_id、quote、prefix、suffix、occurrence、created_at
- CommentEntry：id、anchor_id、role(user/assistant)、content、created_at

## API

- GET    /api/chat/sessions/{sid}/comments        全部线程（进会话恢复高亮）
- POST   /api/chat/sessions/{sid}/comments/stream SSE 流式；新提问带
        {message_id, quote, prefix, suffix, occurrence, question}；
        追问带 {anchor_id, question}
- DELETE /api/chat/sessions/{sid}/anchors/{aid}   删线程

## 模型上下文拼接（参考 OpenAI Canvas 逆向出的分层结构）

```
[系统] 你是对话旁路解释助手。用户选中了主对话里助手回复的一段内容提问。
       回答精准简洁，直接解决疑问，不要大段复述原文。
[主对话背景]（只读参考；按 context window 预算裁剪，超了丢最老，不做摘要压缩）
[被引用的完整消息]（message_id 全文，永远保留）
[用户选中的内容]「quote」（永远保留）
[本线程历史]（追问时带上）
[当前问题]
```

裁剪优先级：主对话背景最先被丢（从最老开始）；被引用消息全文、线程历史、当前问题
永远保留。

## 实施拆分（待开工）

- C1 后端：模型 + 存储 + 上下文构建器（复用预算裁剪）+ SSE API
- C2 前端：划词检测 + 浮条 + 锚点匹配渲染高亮
- C3 前端：右侧挤压式评论栏 + 线程 UI + 流式 + 追问
- C4 会话恢复 + 双向定位联动（点高亮/点引用块互滚）
- C5 单测 + e2e + 收尾

## 业界参照

- 飞书文档评论（划词→工具栏→右侧面板→线程）：
  https://www.feishu.cn/hc/zh-CN/articles/360033768533
- Notion 划词评论：https://www.notion.com/help/comments-mentions-and-reminders
- ChatGPT Canvas 高亮提问：https://help.openai.com/zh-hans-cn/articles/9930697
- OpenAI Canvas 选中文字 prompt 结构逆向（Selected Text + Surrounding Context 分层）：
  https://baoyu.io/blog/ai/reverse-engineering-openai-canvas-prompt-generation
