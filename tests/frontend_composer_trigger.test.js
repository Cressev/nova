#!/usr/bin/env node
/**
 * 对话框 / 与 $ 触发弹窗纯逻辑单测（frontend/src/lib/composerTrigger.ts）。
 * 做法：esbuild 打包成 ESM 后在 Node 断言——重点是交互边界与后端
 * triggers.py 一致：路径不触发、候选为空不弹、前缀过滤、token 写回。
 */
const fs = require("fs")
const os = require("os")
const path = require("path")
const { execSync } = require("child_process")
const assert = require("assert/strict")
const { pathToFileURL } = require("url")

const root = path.resolve(__dirname, "..")
const esbuild = path.join(root, "frontend", "node_modules", ".bin", "esbuild")
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "nova-composer-trigger-"))

const entry = path.join(tmp, "entry.ts")
fs.writeFileSync(entry, `export * from ${JSON.stringify(path.join(root, "frontend/src/lib/composerTrigger.ts"))}`)
const out = path.join(tmp, "bundle.mjs")
execSync(`"${esbuild}" --bundle --format=esm --outfile=${JSON.stringify(out)} ${JSON.stringify(entry)}`, { stdio: "pipe" })

async function main() {
const { detectComposerTrigger, filterTriggerCandidates, triggerToken } = await import(pathToFileURL(out))

const COMMANDS = [
  { name: "/help", description: "帮助", hint: "" },
  { name: "/skills", description: "技能", hint: "" },
  { name: "/skill", description: "触发技能", hint: "<技能名>" },
]
const SKILLS = [
  { name: "review-code", description: "审代码" },
  { name: "web-access", description: "联网" },
]

let passed = 0
function check(name, fn) {
  fn()
  passed += 1
  console.log(`ok - ${name}`)
}

check("slash 触发：首字符 / 且光标在 token 内", () => {
  assert.equal(detectComposerTrigger("/he", 3).kind, "slash")
  assert.equal(detectComposerTrigger("/he", 3).query, "he")
  assert.equal(detectComposerTrigger("/", 1).query, "")
})

check("路径不触发：/Users/... 在输入 / 后即无候选，含分隔符直接 null", () => {
  assert.equal(detectComposerTrigger("/Users/liam/project", 19), null)
  assert.equal(filterTriggerCandidates("slash", "users", COMMANDS, SKILLS).length, 0)
})

check("dollar 触发：仅真实技能前缀；$HOME 等路径不触发", () => {
  assert.equal(detectComposerTrigger("$rev", 4).kind, "skill")
  assert.equal(detectComposerTrigger("$HOME/project", 13), null)
  assert.equal(filterTriggerCandidates("skill", "home", COMMANDS, SKILLS).length, 0)
})

check("行中触发符不触发（与后端 leading-only 一致）", () => {
  assert.equal(detectComposerTrigger("看 /help", 7), null)
  assert.equal(detectComposerTrigger("echo $rev", 9), null)
})

check("光标离开 token（已输入空格参数）不弹菜单", () => {
  assert.equal(detectComposerTrigger("/skill review", 14), null)
})

check("前缀过滤大小写不敏感，命中排序保持注册顺序", () => {
  assert.deepEqual(filterTriggerCandidates("slash", "sk", COMMANDS, SKILLS).map((c) => c.name), ["/skills", "/skill"])
  assert.deepEqual(filterTriggerCandidates("slash", "SK", COMMANDS, SKILLS).map((c) => c.name), ["/skills", "/skill"])
})

check("token 写回：命令与技能带尾随空格", () => {
  assert.equal(triggerToken("slash", "/help"), "/help ")
  assert.equal(triggerToken("skill", "review-code"), "$review-code ")
})

console.log(`\n${passed} 项全部通过`)
}

main().catch((error) => { console.error(error); process.exit(1) })
