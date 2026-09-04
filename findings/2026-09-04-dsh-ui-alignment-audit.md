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
| 1.3 | 侧栏折叠 | 「收起侧边栏」按钮，折叠成图标栏 | 无 | ❌ |
| 1.4 | 侧栏组操作 | 搜索会话/视图选项/添加工作区（三按钮） | 只有搜索 | 🟡 |
| 1.5 | 会话行 hover | 「…」菜单（重命名/分叉/归档）+ tooltip(路径+创建时间) | ✕ 删除钮 | ❌ |
| 1.6 | 会话树 | 工作区文件夹树，组可折叠，含「未分组」 | 有树、无折叠交互 | 🟡 |
| 1.7 | 设置菜单 | 通用设置/模型/插件/Agent预设/打开配置文件 + 快捷偏好行 | settings dialog 另一套 | ❌ |
| 1.8 | 版本徽章 | 品牌行黑底等宽白字 | 同 | ✅ |
| 1.9 | 新会话按钮 | ⊕+文字，白底描边 33px | 已对齐 | ✅ |
| 1.10 | 会话搜索 | 点击「搜索会话」展开输入框，按名称过滤 | 常驻输入框 | 🟡 |

## 2. 空态（hero）

| # | 项 | dsh 实测 | Nova 现状 | 标记 |
|---|---|---|---|---|
| 2.1 | 构图 | hero(36px logo+28px 标题+预览徽章)+chips+composer 组团悬于上中部 | 已对齐(390=390) | ✅ |
| 2.2 | 无 header/tabs | 空态纯画布 | 同 | ✅ |
| 2.3 | chips | 白底描边 pill：folder+工作区名+▾ / 盾+模式+▾ | 同 | ✅ |
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
| 4.1 | 工具条 | Duration / 实际时间(switch) / ⊟Turns / ⊟Calls + 搜索框(放大镜) | 三个假模式按钮 | ❌ |
| 4.2 | ⊟Turns/Calls | 全部分组折叠开关(26→11 行，剩组头+…N steps·M calls) | 无 | ❌ |
| 4.3 | 时间线 | 三泳道 tile(Input绿/Model紫/Tools橙/错误红)+竖排标签+网格+拖拽缩放 | 泳道有、无交互 | 🟡 |
| 4.4 | 表格 | 原生 table；行=TOOL/ASSISTANT/USER/System 徽章行+等宽工具行；Turn 组头行「Turn N #seq」 | div 列表 | 🟡 |
| 4.5 | 分页 | 顶部「Load earlier history」行 | 无 | ❌ |
| 4.6 | **行选中** | 点击行→右选中(bg rgba(38,49,72,.1))+右侧详情面板展开 | **无** | ❌ |
| 4.7 | **详情面板** | 381px aside：头部(类型+Turn·Step+×)；TOOL 行 tab=Summary/Payload(JSON树)/Result(pre)/Schema/Timing；消息行 tab=Summary/Preview/Raw(/Source)；Summary=dt/dd(Status/Started/Duration/Timing source/Hierarchy) | **完全缺失** | ❌ |
| 4.8 | 搜索 | 索引跳转/高亮(3s 节流)，不过滤行数 | 过滤行数(行为错误) | ❌ |
| 4.9 | composer | dsh 常驻(轨迹区让位) | 常驻 | ❓ 见 §5 |

## 5. 决策待定项（需用户拍板）

| # | 问题 | dsh 事实 | 用户指示 | 建议 |
|---|---|---|---|---|
| 5.1 | 轨迹页 composer 去留 | **常驻**（textarea 可见，源码 `bottom-clearance` 专为它设计） | 「轨迹页面不需要有对话框」 | 按用户指示隐藏；清单记为「用户覆盖 dsh」 |
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
