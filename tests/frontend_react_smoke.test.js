#!/usr/bin/env node
/**
 * React 前端冒烟测试：源码结构 + 构建产物 + 类型检查。
 * 旧 vanilla 静态测试（frontend_*.test.js 针对 static/js/app.js）已随迁移退役，
 * 核心交互断言由浏览器实测 + 后端 API 测试覆盖。
 */
const fs = require("fs")
const path = require("path")
const { execSync } = require("child_process")

const root = path.resolve(__dirname, "..")
const failures = []

function check(name, fn) {
  try {
    fn()
    console.log(`ok - ${name}`)
  } catch (error) {
    failures.push(name)
    console.error(`FAIL - ${name}: ${error.message}`)
  }
}

check("frontend 源码结构完整", () => {
  const required = [
    "frontend/package.json",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
    "frontend/index.html",
    "frontend/src/main.tsx",
    "frontend/src/App.tsx",
    "frontend/src/types.ts",
    "frontend/src/tokens.css",
    "frontend/src/app.css",
    "frontend/src/lib/api.ts",
    "frontend/src/components/Markdown.tsx",
    "frontend/src/components/ToolEvent.tsx",
    "frontend/src/components/Takeover.tsx",
  ]
  for (const rel of required) {
    if (!fs.existsSync(path.join(root, rel))) throw new Error(`缺少 ${rel}`)
  }
})

check("构建产物存在且为 Vite hash 形态", () => {
  const staticDir = path.join(root, "static")
  const index = fs.readFileSync(path.join(staticDir, "index.html"), "utf8")
  if (!index.includes('id="root"')) throw new Error("index.html 缺少 #root 挂载点")
  if (!/\/static\/assets\/index-[^"]+\.js/.test(index)) throw new Error("index.html 未引用 hash 产物")
  if (!/\/static\/assets\/index-[^"]+\.css/.test(index)) throw new Error("index.html 未引用样式产物")
})

check("React 源码包含核心交互锚点", () => {
  const app = fs.readFileSync(path.join(root, "frontend/src/App.tsx"), "utf8")
  const anchors = [
    "chat-form",              // composer
    "permission-select",      // 权限位
    "model-select",           // 模型位
    "takeover-dock",          // 审批/提问停靠
    "stats-line",             // 统计行
    "chat-header",            // 会话头
    "header-tab",             // 对话/轨迹标签页
    "empty-hero",             // 空态 hero
    "session-log-button",     // Session log 下载
    "isComposing",            // 中文输入法 Enter 拦截
    "assistant_delta",        // NDJSON 流式协议
    "permission_request",     // 审批流
    "user.question",          // 提问流
  ]
  for (const anchor of anchors) {
    if (!app.includes(anchor)) throw new Error(`App.tsx 缺少锚点 ${anchor}`)
  }
})

check("TypeScript 类型检查通过", () => {
  execSync("npx tsc --noEmit", { cwd: path.join(root, "frontend"), stdio: "pipe" })
})

if (failures.length > 0) {
  console.error(`\n${failures.length} 项失败`)
  process.exit(1)
}
console.log("\n全部通过")
