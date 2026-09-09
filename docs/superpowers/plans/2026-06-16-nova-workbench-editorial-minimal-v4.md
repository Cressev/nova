# Nova Workbench Editorial Minimal v4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a standalone static HTML prototype for Nova that keeps the VSCode-like workbench structure but adopts an editorial minimal, line-driven visual style.

**Architecture:** Reuse the proven prototype shell structure from earlier iterations, then replace the visual system with warm off-white surfaces, ultra-light dividers, restrained typography, and non-card content layouts. Verification happens through a temporary local HTTP server plus Playwright screenshots.

**Tech Stack:** Static HTML, CSS, inline JavaScript, Python `http.server`, Playwright CLI

---

### Task 1: Document the design boundary

**Files:**
- Create: `docs/superpowers/specs/2026-06-16-nova-workbench-editorial-minimal-v4-design.md`
- Modify: `user-queries.md`
- Modify: `TODOList.md`

- [ ] **Step 1: Confirm the approved direction is `Editorial Minimal`**

Use the already approved design direction:

```text
Structure: VSCode-like
Style: editorial minimal
Keep: activity bar icons, session single-line list, workspace file tree, editor tabs
Avoid: green palette, heavy glass, cardified history, letter-only icons
```

- [ ] **Step 2: Record the user request in durable files**

Ensure the newest request is appended:

```text
[Image #1] 能参考这个风格嘛，线条极简风
```

- [ ] **Step 3: Write the short spec**

Capture the visual rules, layout rules, required prototype states, and verification standard in the spec file.

### Task 2: Build the standalone v4 prototype

**Files:**
- Create: `output/prototypes/nova-workbench-editorial-minimal-v4.html`
- Reference: `output/prototypes/nova-workbench-vscode-prototype-v2.html`
- Reference: `output/prototypes/nova-workbench-glass-prototype-v3.html`
- Reference: `output/prototypes/img_v3_0212n_b5c877cf-d096-4865-87e2-b7b7794be71g.jpg`

- [ ] **Step 1: Start from the known workbench structure**

Keep these sections in the HTML:

```html
<aside class="activity"></aside>
<section class="sidebar"></section>
<main class="editor"></main>
<section class="bottom"></section>
<aside class="inspector"></aside>
```

- [ ] **Step 2: Replace the visual system**

Implement CSS tokens similar to:

```css
:root {
  --bg: #f7f4ee;
  --panel: #fbf9f3;
  --line: rgba(61, 55, 47, 0.16);
  --line-strong: rgba(61, 55, 47, 0.28);
  --text: #1f1c19;
  --muted: #6e675f;
  --accent: #2c2a27;
}
```

- [ ] **Step 3: Make sessions a one-line list and workspace a real tree**

Represent sessions as rows, not cards:

```html
<div class="session-row">
  <span class="session-dot"></span>
  <span class="session-title">修复 runtime cancel 竞态</span>
  <time>11:42</time>
</div>
```

Represent workspace with nested tree rows:

```html
<div class="tree-row depth-0 open">personal-dev-agent</div>
<div class="tree-row depth-1 open">src</div>
<div class="tree-row depth-2">runtime</div>
```

- [ ] **Step 4: Make tabs, diff, stats, and settings feel document-like**

Use thin tabs, table-like stats, and bordered code blocks rather than filled cards.

- [ ] **Step 5: Add minimal interaction**

Use inline JavaScript to switch:

```js
setActivity('sessions')
setActivity('workspace')
setEditorTab('git')
setEditorTab('stats')
```

### Task 3: Verify in a real browser

**Files:**
- Create: `output/playwright/nova-workbench-v4-editorial-sessions.png`
- Create: `output/playwright/nova-workbench-v4-editorial-workspace.png`
- Create: `output/playwright/nova-workbench-v4-editorial-git-or-stats.png`

- [ ] **Step 1: Start a temporary local server**

Run:

```bash
python3 -m http.server 8791 --bind 127.0.0.1
```

from:

```bash
output/prototypes
```

- [ ] **Step 2: Open the prototype with Playwright**

Run:

```bash
bash "$PWCLI" open http://127.0.0.1:8791/nova-workbench-editorial-minimal-v4.html
```

- [ ] **Step 3: Capture required states**

Take screenshots for:

```text
Sessions
Workspace tree
Git Diff or Stats
```

- [ ] **Step 4: Check the console**

Confirm there are no browser console errors or warnings before closing.

### Task 4: Update logs and deliver

**Files:**
- Modify: `TODOList.md`
- Modify: `log.md`
- Modify: `user-queries.md`

- [ ] **Step 1: Mark the v4 checklist complete**

Close out the `2026/06/16/14:41:00` task block with completed checkboxes and final notes.

- [ ] **Step 2: Append a short log entry**

Record the prototype path, visual direction, verification method, and screenshot paths.

- [ ] **Step 3: Report the result**

Tell the user exactly where the prototype lives and that product runtime/source files were not changed.
