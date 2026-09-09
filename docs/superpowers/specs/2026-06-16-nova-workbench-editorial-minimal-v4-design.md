# Nova Workbench Editorial Minimal v4 设计

## 目标

在不修改 Nova 产品源码的前提下，产出一个独立静态原型，验证新的工作台视觉方向是否成立。

这个方向保留用户已经确认的 VSCode-like 信息架构：

- 最左侧 Activity Bar
- 左侧 Primary Sidebar
- 中央多标签 Editor Area
- 底部 Bottom Panel
- 右侧 Inspector

但视觉语言从 v2 的深色 IDE 和 v3 的亮色毛玻璃，收束到更克制的 `Editorial Minimal`。

## 为什么要改

现有几版原型已经解决了结构问题，但还没有解决“高级感”的来源。

用户给出的参考图说明，这一轮真正需要的不是更重的材质感，而是更安静的结构感。也就是说，界面层次应该主要由这些元素建立：

- 超细描边
- 大留白
- 暖白背景
- 极少量强调色
- 文档式、手册式分区

所以 v4 不再把“毛玻璃”作为主语言，只保留极轻的明暗层次。

## 设计原则

### 1. 结构像 IDE，气质像精密文档

工作方式仍然是 IDE 式工作台，不退回成单页展示页。但每个区域都要摆脱厚重面板和组件堆叠，转而使用细线和空白定义边界。

### 2. 视觉重点从卡片改为边框与节奏

Sessions 不再做卡片历史。Workspace 不做装饰化列表。Stats 不做仪表盘卡片墙。核心信息尽量以：

- 单行列表
- 文件树
- 表格
- diff 区块
- 极细标签页

来呈现。

### 3. 色彩退后，信息前置

主色只用于激活状态、少量数字和极少数状态点。大面积铺色和大块渐变都不应该再出现。

### 4. 参考图的线条感要落到真实工作台里

参考图的价值不在于复刻页面内容，而在于复刻它的控制力：线条轻、留白准、分区稳、几乎没有多余装饰。v4 需要把这种控制力映射到 Session、Workspace、Git Diff、Stats、Settings 这些真实模块上。

## 布局定义

### Activity Bar

- 保留左侧最窄功能栏
- 使用可辨识线性图标
- 默认不显示拥挤文字
- 激活项只用细线和轻底色提示

### Primary Sidebar

- `Sessions`：单行列表，像历史命令，不使用卡片
- `Workspace`：真正的文件树，层级关系明确
- `Git / Stats / Hooks / Settings`：根据模块切换不同侧栏内容，不共享一套假数据容器

### Editor Area

- 多标签页保留
- 标签页使用细边线和轻分段，不使用厚实块状 tab
- 默认展示 Session、Git Diff、Stats、Settings 等标签

### Bottom Panel

- 承担 Output、Approvals、Trace、Terminal
- 视觉弱于主编辑区
- 采用 inspection strip 的轻量结构

### Inspector

- 弱化存在感
- 主要展示当前会话元数据、模型、token、hook 状态

## 视觉语言

### 配色

- 主背景：偏暖白
- 边线：浅灰细线
- 文本：深灰到中灰
- 强调：单一深色点缀，避免高饱和

### 质感

- 可以保留非常轻的底纹或斜纹
- 不做明显模糊玻璃
- 不做大面积阴影

### 组件

- 以细边框输入框、细分段 tabs、表格、列表、代码框为主
- 避免圆润过度和厚按钮

## 页面内容要求

v4 至少要覆盖这些可见状态：

1. `Sessions` 侧栏状态
2. `Workspace` 文件树状态
3. `Git Diff` 标签页状态
4. `Stats` 标签页状态

## 验证标准

- 原型文件位于 `output/prototypes/`
- 不修改 `src/` 或 `static/` 产品源码
- 使用本地 HTTP 服务打开，而不是 `file://`
- Playwright 验证控制台无错误
- 至少保存 3 张截图，覆盖 Session、Workspace、Git Diff 或 Stats
