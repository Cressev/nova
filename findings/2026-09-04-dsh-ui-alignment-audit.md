# DSH UI 对齐审计清单（交互级）

> 目的：把「和 dsh 一模一样」从口号变成可逐项验收的清单。
> 方法：**真实操作 dsh GUI（点击每一类行/按钮/tab）+ 源码核对**（`packages/client/ui-*`），不是只截图。
> 验收标准：**两边做同一个操作，对比结果是否一致**（DOM 结构 + 视觉 + 行为），截图对比仅作辅助。
> 状态标记：✅ 已对齐 ｜ 🟡 部分对齐 ｜ ❌ 缺失 ｜ ➖ 多余（dsh 没有，Nova 有）｜ ❓ 待用户决策
> 更新约定：每修完一项把标记改成 ✅ 并附提交号。本文档是对齐工作的**唯一事实源**。

---

## 0. 全局结论（本轮审计的新发现）

之前多轮对齐失败的根本原因：只对比了**静态截图**，从未点击过 dsh。本轮真实交互后发现的核心事实：

1. **轨迹页有右侧详情面板**（381px `<aside>`）：点击任意轨迹行展开，**未选中时宽为 0**（截图永远看不到）。工具行有 Summary/Payload/Result/Schema/Timing 五个 tab，消息行有 Summary/Preview/Raw(/Source)。⭐ 这是之前完全缺失的最大交互。
2. **轨迹工具条的 Duration/Turns/Calls 不是"时间线模式切换"**——Turns/Calls 是**全部分组折叠开关**（⊟ 图标；点击 Turns 后 26 行 → 11 行，剩 Turn 组头 +「…N steps · M tool calls」摘要行）；Duration 旁还有一个「实际时间」switch。
3. **轨迹搜索不是过滤**：输入 bash 后行数不变（26→26），是**索引跳转/高亮**（3 秒节流建索引）。
4. 轨迹表是**原生 `<table>`**（COLGROUP+TBODY，行=tr 隐式 row role），顶部有「Load earlier history」分页行。
5. 对话页助手消息下方有**四个操作图标：复制/赞/踩/分享(分支)**，消息之间有细分隔线。
6. 侧栏会话行 hover 出现「…」菜单：**重命名 / 分叉会话 / 归档会话**（不是删除 ✕）。
7. 侧栏工作区操作是**搜索会话 / 视图选项 / 添加工作区**三个按钮 + 底部「设置」（展开：通用设置/模型/插件/Agent 预设/打开配置文件 + 标准模式/Full access/中文/浅色/深色快捷行）。
8. 会话头面包屑支持**子代理链**：「nova审查 / 5 个子代理 ▾」可展开；模式标签实例是「PTC 模式」。
9. 工具行标题形态（nova审查会话实测）：「Code Read executor.py part 2」「Read src/…」(行内嵌文件跳转按钮)「Think <推理摘要>」——**Think 行是独立行型**。
10. 对话流顶部有「加载更早」分页按钮。
11. 统计行完整格式（实测）：`5 轮 · 97 步 | LLM 11m35s · 工具调用 24.1s | 首 token 平均 5.8s · 463 tok/s | 缓存命中 78% | 输入 13.9M tok`——五段。
12. ⚠️ 争议项：实测 dsh 轨迹页 **composer 常驻**（textarea y=518 可见，轨迹区以 `--dsh-trajectory-bottom-clearance` 让位）；但用户明确说「轨迹页面不需要有对话框」→ 见 §5 决策项。

---

## 1. 应用骨架（所有状态共用）

| # | 项 | dsh 实测 | Nova 现状 | 标记 |
|---|---|---|---|---|
| 1.1 | 布局 | 侧栏 279px(#f9fafb 无右边框) + 主列 | 同 | ✅ |
| 1.2 | 主列画布 | 极淡冷色径向渐变 + 白 | 已实现 | ✅ |
| 1.3 | 侧栏折叠 | 「收起侧边栏」按钮，折叠成图标栏 | 有按钮但是**死按钮**（无 onClick；body.sidebar-collapsed CSS 为孤儿规则），26/09/04 实测点击 279→279 | ❌ 假对齐 |
| 1.4 | 侧栏组操作 | 搜索会话/视图选项/添加工作区（三按钮） | 只有搜索 | 🟡 |
| 1.5 | 会话行 hover | 「…」菜单（重命名/分叉/归档）+ tooltip(路径+创建时间) | ✕ 删除钮 | ❌ |
| 1.6 | 会话树 | 工作区文件夹树，组可折叠，含「未分组」 | 有树、无折叠交互 | 🟡 |
| 1.7 | 设置菜单 | 通用设置/模型/插件/Agent预设/打开配置文件 + 快捷偏好行 | settings dialog 另一套 | ❌ |
| 1.8 | 版本徽章 | 品牌行黑底等宽白字 | 同 | ✅ |
| 1.9 | 新会话按钮 | ⊕+文字，白底描边 33px | 已对齐 | ✅ |
| 1.10 | 会话搜索 | 点击「搜索会话」展开输入框，按名称过滤 | 已改为按钮展开式（searchOpen toggle） | ✅ |

## 2. 空态（hero）

| # | 项 | dsh 实测 | Nova 现状 | 标记 |
|---|---|---|---|---|
| 2.1 | 构图 | hero(36px logo+28px 标题+预览徽章)+chips+composer 组团悬于上中部 | 已对齐(390=390) | ✅ |
| 2.2 | 无 header/tabs | 空态纯画布 | 同 | ✅ |
| 2.3 | chips | 白底描边 pill：folder+工作区名+▾ / 盾+模式+▾，**▾ 可点开菜单** | 外观同款，但 ▾ 是**死 chevron**（实测点击不开菜单），26/09/04 | 🟡 假对齐 |
| 2.4 | composer 质感 | 半透明+blur+双层阴影 | 同 | ✅ |

## 3. 会话态（对话 tab）

| # | 项 | dsh 实测 | Nova 现状 | 标记 |
|---|---|---|---|---|
| 3.1 | 会话头 | 面包屑(含子代理链▾)+模式+后台任务▾+Session log⤓+tabs | 无子代理链/后台任务 | 🟡 |
| 3.2 | 分页 | 消息流顶部「加载更早」 | 无 | ❌ |
| 3.3 | 用户消息 | 右对齐淡蓝气泡 22px | 同 | ✅ |
| 3.4 | 助手消息 | 裸文本 + **下方操作行：复制/赞/踩/分享** + 消息间细分隔线 | 只有复制 | 🟡 |
| 3.5 | 工具行家族 | Read(内嵌文件跳转钮)/Bash/Think/Code Read 前缀行；点击展开详情 | 行型有、无 Think 行型/文件跳转钮 | 🟡 |
| 3.6 | 运行态 | 蓝色流式状态行(Deep diving…Ns)+停止键 | 有状态文字 | 🟡 |
| 3.7 | takeover | 审批/提问卡停靠 composer 上方 | 同 | ✅ |
| 3.8 | 统计行 | 五段完整(含首token/tok/s/缓存/输入tokens) | 三段(缺后端指标) | 🟡 |
| 3.9 | 排队消息 | 队列控制条 | 有 | ✅ |

## 4. 轨迹态（轨迹 tab）⭐ 本轮审计重点

| # | 项 | dsh 实测 | Nova 现状 | 标记 |
|---|---|---|---|---|
| 4.1 | 工具条 | Duration / 实际时间(switch) / ⊟Turns / ⊟Calls + 搜索框(放大镜) | 32px 工具条+switch+折叠开关+计数 | ✅ |
| 4.2 | ⊟Turns/Calls | 全部分组折叠开关(26→11 行，剩组头+…N steps·M calls) | 15→0 行+摘要行+再点还原 | ✅ |
| 4.3 | 时间线 | sequence(默认：每记录 1 单位无缝拼接)+duration(真实时长+空闲压缩)+actual(墙钟) 三模式；turn 边界竖线；滚轮缩放/右键拖平移/拖选聚焦/点击色块跳转表格行 | 全部落地（26/09/04 重写 projectSpans 三模式投影） | ✅ |
| 4.4 | 表格 | 原生 table；kindTag 徽章行；Turn=首行左上 8px 小标签+发丝线（无组头按钮行） | 已按源码移植（4cf9477+563f890+bdf4d00：tt-* 结构/turnLabel/折叠摘要/滚动 pane+底部让位） | ✅ |
| 4.5 | 分页 | 顶部「Load earlier history」行 | 无 | ❌ |
| 4.6 | **行选中** | 点击行→右选中(bg rgba(38,49,72,.1))+右侧详情面板展开 | 同款选中色+381px 面板 | ✅ |
| 4.7 | **详情面板** | 381px aside：头部(类型+Turn·Step+×)；TOOL 行 tab=Summary/Payload(JSON树)/Result(pre)/Schema/Timing)；消息行 tab=Summary/Preview/Raw；**SYSTEM 行 tab=System Prompt(全文)+Tools(工具目录)** | 全部落地(Duration 按 session timestamps 差值回退；SYSTEM 行 26/09/06 补齐 prompt/tools 两 tab) | ✅ |
| 4.8 | 搜索 | 索引跳转/高亮(3s 节流)，不过滤行数 | 高亮匹配行+计数(15 行不变，7 处命中)；跳转未做 | ✅ |
| 4.9 | composer | dsh 常驻且悬浮(账本通底延伸至 composer 下，clearance 让末行滚出) | 同款悬浮：780×96 y=505(参照 780×94 y=507)，账本 463≈474，padding-bottom 140 | ✅ |

## 5. 决策待定项（需用户拍板）

| # | 问题 | dsh 事实 | 用户指示 | 建议 |
|---|---|---|---|---|
| 5.1 | 轨迹页 composer 去留 | **常驻悬浮**（textarea 可见，源码 `bottom-clearance` 专为它设计） | 用户确认：常驻，此前难受的是尺寸/位置 → 已按 dsh 几何对齐 | ✅ 已结 |
| 5.2 | 统计行缺的后 4 段指标 | 需 provider 侧记录 TTFT/解码时长/缓存命中/输入 tokens | — | 后端补埋点后前端接（工作量在 provider 层） |

## 6. 验收方式（逐项通用）

1. 同一操作在 3080(dsh) 与 8765(Nova) 各执行一次；
2. 对比：DOM 结构（角色/层次）、视觉（截图并排）、行为结果（行数/展开态/数据）；
3. 每项以本清单编号回填 ✅+提交号；
4. 视觉模型打分只作辅助，**不作为验收依据**。

---

## 附：本轮审计的证据命令（可复现）

- 详情面板：`agent-browser eval` 点击 `.vWegcq_table tbody tr` TOOL 行 → `aside` 381px，tabs Summary/Payload/Result/Schema/Timing；消息行 → Summary/Preview/Raw；× 关闭后 aside 移除。
- 折叠开关：点击 `[role=toolbar]` Turns 按钮 → 行数 26→11，组头「Turn N #N」+「…N steps · M tool calls」。
- 搜索行为：输入 bash → 行数 26 不变（非过滤）。
- 会话 hover：mouseover treeitem → 「会话"nova-1"的操作」按钮 → menu=重命名/分叉会话/归档会话。
- 设置：snapshot → 通用设置/模型/插件/Agent 预设/打开配置文件/关闭 + 标准模式/Full access/中文/浅色/深色。
- 源码：`ui-trajectory/src/client/TrajectoryTable.tsx`（detail panel 2670-2720 行、tabs 895-925 行）、`views.module.css`（bottom-clearance）、`TrajectoryTimeline.tsx`（拖拽/缩放）。


## 7. 剩余缺口清单（2026-09-04 15:30 全量实况复核后）

近期已补齐（不再列为缺口）：控件体系（MenuSelect/SVG 按钮/工具条 toggle·switch·搜索）、轨迹表格结构（原生 table/kindTag/turnLabel/折叠摘要/详情分节/滚动 pane+底部让位）、Think 披露行、会话搜索展开式、时间线三模式无缝拼接+缩放+点击跳转（4.3 ✅）、SYSTEM 提示词行置顶（system.prompt 事件+老会话虚拟行兜底）、reasoning 不再渲染为 sys 行。

### P0 假对齐（控件在但不工作，损害信任）
- **7.1** 折叠侧栏：死按钮（无 handler），dsh 折叠成 56px 图标栏（§1.3）
- **7.2** 空态 chips ▾：死 chevron，dsh 点开工作区/权限菜单（§2.3）
- **7.3** 会话行 hover：无任何操作按钮（实测 item 内 0 button），dsh=「…」菜单（重命名/分叉/归档）+tooltip（§1.5）

### P1 可见缺失（用户可感知）
- **7.4** 会话头右侧：无 Session log⤓、无子代理链面包屑▾、无后台任务▾（实测 Nova 会话头 0 按钮）（§3.1）
- **7.5** 助手消息操作行：只有复制；dsh=复制/赞/踩/分享+消息间细分隔线（§3.4）
- **7.6** 「加载更早」分页：对话流与轨迹表两处都无（§3.2/§4.5）
- **7.7** 轨迹行型不全：dsh kinds={user,tool,subtool,message,context}，Nova 只有 {user,tool,message}——缺 subtool（子代理调用）与 context（注入）行型
- **7.8** 设置菜单：Nova 是 dialog 另一套；dsh=菜单（通用设置/模型/插件/Agent预设/打开配置文件+快捷偏好行）（§1.7）

### P2 依赖后端/低频/细节
- **7.9** 统计行 3/5 段：缺 首 token 平均/tok 速率/缓存命中/输入 tokens（需 provider 埋点，§5.2）→ **26/09/09 部分补齐**：provider usage 提取（非流式 response.usage + 流式 include_usage 尾块）→ `tokens.usage` 事件 → 统计行 `tokens ↑prompt ↓completion` 真实值（实测 11612/51）；首 token 速率/缓存命中仍缺。
- **7.10** Read 工具行内嵌文件跳转钮（§3.5）
- **7.11** 侧栏组操作：缺 视图选项/添加工作区 两钮（§1.4）
- **7.12** 会话树工作区分组折叠交互（§1.6）
- **7.13** 轨迹搜索索引跳转（现在只高亮+计数，dsh 跳转到匹配行）
- **7.14** 时间线缩放/框选已做（wheel/drag/pan），与 dsh 拖拽缩放的交互细节未逐一手感对比——待核对项

### 7.A 能力层差距批次（26/09/09 补齐记录）
对 2026-09-09 全景盘点（dsh 51 包 vs Nova 22 模块）的落地批次：
- **模型协作**：goal 续跑驱动（`SessionRunner._drive_goal_rounds`：turn 后 active 目标自动注入续跑轮，`goal.round`/`goal.snapshot` 事件，进程重启经事件快照恢复）✅
  - **26/09/14 修正**：09/09 版有假——驱动读 orchestrator 缓存实例而模型写每轮新建的 runtime 自有 tools，两实例永不相见，续跑从未真正触发（当日 e2e 只有 1 个 turn.started，被我误标 ✅）。修复：`CodexLikeAgentRuntime` 支持注入 tools 实例 + `_agent_runtime_for_session(session_id)` 会话级工厂（chat.py 的 SessionRunner 全走它），模型/驱动/审批续跑共享同一实例。复验：turn.started=2、goal.round+goal.snapshot 各 1 落库、续跑注入消息可见。todo（文件持久 .nova/agent-todos.json，先前已有）✅；schedule（到期注入下一轮 turn 开头，先前已有）✅；plan_submit 计划审批（复用 user_question 挂起管线，data.plan 区分，批准/驳回续跑）✅；feedback API（POST /api/chat/sessions/{id}/feedback，👍/👎+评论落事件）✅。
- **会话工具状态**：`_workspace_tools(session_id)` 会话级缓存（goal/todo/schedule 跨请求存活）+ `_hydrate_session_tool_state` 事件恢复 ✅。
- **智能编排**：workflow_run 并行 fan-out（≤6 子代理线程池，失败隔离聚合 JSON 报告）✅
  - **26/09/14 补验**：09/09 冒烟走的是旁路（直连 WorkspaceTools → 本地占位 runner），"2/2 完成"是空心胜利；且 `_subagent_runner` 20s 超时会让真模型必然降级占位。修复：超时放宽到 100s；经服务端 chat 链路复验（runtime 注入全局打过补丁的 manager），两 worker 输出真模型答案（12×12=144 / 巴黎）。**26/09/14 二次对齐（script 模式落地，三项差距关闭）**：workflow_run 新增 script 参数——模型写 JS 协调脚本，跑在 node:vm 受限上下文（无 fs/net/timer/require）+ 只读 seatbelt 内；钩子 agent(prompt,{label,schema,provider,model})/pipeline/parallel/phase/log/args 与 dsh 同语义（失败 agent→null、阶段抛错→该 item null、误用钩子→杀脚本）；schema 走对象根 JSON Schema 子集校验（type/properties/required/additionalProperties/items/enum/const/oneOf）；逐 agent provider/model 覆盖走 registry 预设（无 key 显式失败拒绝降级）。并发≤6、单 agent 110s、总 480s、工具预算 600s。e2e 实证：calc=144、schema 返 {"capital":"Paris"}、deepseek 无 key → null+错误记录、模型正确汇报三值。产物：workflow/host.js、workflow/orchestrator.py、subagents/model_runner.py。
  - 顺带修了一个真 bug：`_direct_tool_calls_from_user` 的 "ls" 子串匹配会把 `additionalProperties:false` 里的 "fa**ls**e" 命中，快速路径劫持成 glob 直达+无工具回答（两次 e2e 被 Hijack 的根因）；改整词 \bls\b。
- **检索**：session_search 跨会话关键词检索（挂 session_store）✅。
- **运行时底座**：token-meter（7.9 部分）✅；多 provider（registry 预设，PATCH 热切 base_url/model/api_key_env 三元组，设置工具条 provider-select）✅；PTY 持久终端（pty_start/write/read/list/kill，openpty+TIOCSWINSZ+滚动 256KB 缓冲，OS 沙箱内，permission=shell 走完整审批门）✅。
  - **26/09/14 三次对齐（协议双栈 + 设置面板）**：① 双协议——registry 预设带 protocol 字段（openai chat/completions + anthropic Messages），AnthropicProvider 与 BigModelProvider 同形接口（system 顶层提升、tools name/description/input_schema、content blocks 归一化、thinking→reasoning_delta、usage input/output→prompt/completion），preset 切换按协议换 provider 实例，runtime key 文件随身携带不丢；预设新增 anthropic 官方 + zai-anthropic（BigModel 的 Anthropic 兼容端点）。② 设置面板——原 #open-settings 是死按钮（无 onClick，用户"点开都没用"的根因），新建 SettingsDialog：preset（协议提示）/base_url/model/api key/权限/沙箱/审批/网络/轮数/上下文窗口 11 字段全可配可存（PATCH config + POST secrets）。e2e：对话框切 anthropic 保存生效、无 key 显式报错、切回 bigmodel key 不丢（key_set: True）。
  - **26/09/14 四次对齐（设置改全局 + 视觉对齐 dsh）**：① 全局单源——runtime-config 读写收敛到 ~/.nova/config/runtime-config.json，移除项目级 .nova/config 覆盖链（用户："设置是全局的"），对话框只留模型设置（预设/URL/模型/密钥）。② 视觉对齐——第一版对话框硬编码深色 fallback（--bg-elevated 等不存在的变量）造成深色块与全站浅色割裂；按 dsh SettingsRoot/Modal 规范重写：dsw token 浅色、r20 面板、mask+blur、行式「标题+描述+右控件」、即时生效无保存按钮（flash 提示）、Esc/遮罩关闭、URL/模型等宽+ellipsis。三轮视觉评审迭代后达标。
- **仍缺**：e2b 云沙箱（POC 不搬）、acp 程序化接入、attachment 对话附件、credentials 三层分离、identity、feedback 前端按钮（7.5 关联）、subtool/context 轨迹行型（7.7）。


## 8. 布局不变量自检清单（2026-09-06 起强制：每次改轨迹页后必跑）

> 背景：连续三轮用户点名我自查漏掉的布局回归（滚动容器丢失→行漫过 composer；900px 居中窄列→滚动条离右缘 50px；详情体无让位→内容从 composer 底下穿出）。根因=只验证"刚改的那一处"，不验证布局不变量；且曾中途注意到差异（"dsh 轨迹根容器满宽 1000"）却没跟进修复。此清单为强制项。

改动后逐条在 8765 实测（agent-browser eval），任何一条不符即为回归：
- **8.1 满宽**：`.tt-tablePane` right == window.innerWidth（当前 1280），left == 侧栏右缘（279）。禁止任何 max-width/margin auto 出现在轨迹链路上。
- **8.1b 对话页满宽**：`.scroll-body` right == innerWidth 且 left == 279；`.messages` 780 居中且 **overflow 必须为 visible**（滚动只许在 .scroll-body）。26/09/07 新增：对话页滚动条悬在内容列右缘（1170）就是违反此条。
- **8.2 滚动分层**：唯一纵向滚动容器 = `.tt-tablePane`（overflow-y auto）；`.trajectory-ledger-wrap`/`.trace-view`/`.main-col`/body 全部不滚动（scrollHeight==clientHeight）。
- **8.3 底部让位×2**：`.tt-tablePane` 与 `.trajectory-details-body` 的 padding-bottom 都必须是 140px；各自滚到底后最后一个子元素 bottom ≤ composer top（实测 487 ≤ 505）。
- **8.4 贴边**：工具条/时间线/表格左缘 == 侧栏右缘（279），无居中窄列、无左右留白列。
- **8.5 详情面板**：打开时 pane 收窄、关闭时 pane right 恢复 1280；`.trajectory-details` 宽度 clamp(320,38%,440)。
- **8.6 每项记录**：跑完在本节末追加一行（日期+提交+六项结果），作为审计证据。

（26/09/06 `8c9f483` 首跑：8.1 ✅ 1280/279；8.2 ✅ 仅 pane 滚；8.3 ✅ 140px+487≤505；8.4 ✅ 279 贴边；8.5 ✅ 关闭后 1280。）

（26/09/07 对话页重构首跑：8.1b ✅ sb 279→1280，messages 382→1162 居中不滚，气泡 1156≤1170 全内，scrollWidth==clientWidth。）


## 9. 权限/安全对齐（26/09/09 全对齐完成）

| 层 | dsh | Nova | 状态 |
|---|---|---|---|
| bash 执行 | 每条经 sandbox-exec/bwrap/ACL 内核级 confine，runner 失败 fail-closed | sandbox.py 同语义移植：macOS SBPL（deny file-write* + subpath 白名单）/Linux bwrap/他平台拒绝；非 danger_full_access 全走 confine | ✅ |
| 审批审计 | approval/asked·decided·policy 三事件可回放 | permission.asked/approved/denied/policy 落会话事件流（asked 用独立 id 防 upsert 合并） | ✅ |
| ask 模式 | 走审批 answerer 链 | gate 放行到 executor 审批流（pending→批准续跑） | ✅ |
| 默认模式 | read-only fail-safe，显式 opt-in 放开 | sandbox_mode 默认 read_only；用户已有 runtime-config 显式覆盖保留 | ✅ |

实测记录：workspace_write 区内写 ok、区外写内核拒（Operation not permitted）；read_only gate 双层拦截；258 unittest 绿。
