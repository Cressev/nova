#!/usr/bin/env node
/**
 * 侧边栏对齐（dsh D2）单测：relativeTime 六桶分桶 + relativeTimeAgo 措辞。
 * 做法：esbuild 打包 frontend/src/lib/api.ts，在 Node 内断言时间分桶语义。
 * 注意：require 与 top-level await 混用会触发 ERR_AMBIGUOUS_MODULE_SYNTAX，
 * 因此 import 包在 async main() 里（与 frontend_composer_trigger.test.js 同款）。
 */
const fs = require("fs")
const os = require("os")
const path = require("path")
const { execSync } = require("child_process")
const assert = require("assert/strict")

const root = path.resolve(__dirname, "..")
const esbuild = path.join(root, "frontend", "node_modules", ".bin", "esbuild")
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "nova-sidebar-"))
const entry = path.join(tmp, "entry.ts")
fs.writeFileSync(entry, `export * from ${JSON.stringify(path.join(root, "frontend/src/lib/api.ts"))}`)
const outfile = path.join(tmp, "bundle.mjs")
execSync(`${JSON.stringify(esbuild)} ${JSON.stringify(entry)} --bundle --format=esm --outfile=${JSON.stringify(outfile)} --log-level=silent`, { stdio: "pipe" })

async function main() {
  const mod = await import(`${"file://"}${outfile}`)
  const DAY = 86_400_000
  const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString()
  const cases = [
    [5_000, "刚刚"],
    [5 * 60_000, "5分钟"],
    [3 * 3_600_000, "3小时"],
    [4 * DAY, "4天"],
    [45 * DAY, "1个月"],
    [90 * DAY, "3个月"],
    [400 * DAY, "1年"],
    [2 * 365 * DAY, "2年"],
  ]
  let failures = 0
  for (const [ago, expected] of cases) {
    const actual = mod.relativeTime(iso(ago))
    if (actual !== expected) { console.error(`FAIL - relativeTime(${ago}ms) = ${actual}，期望 ${expected}`); failures += 1 }
    else console.log(`ok - relativeTime ${expected}`)
  }
  // 9/23 形态（裸日期桶）绝不能再出现：30~365 天之间的任意值都必须落在"个月"
  for (const days of [31, 60, 200, 364]) {
    const actual = mod.relativeTime(iso(days * DAY))
    if (!/^\d+个月$/.test(actual)) { console.error(`FAIL - ${days}天 → ${actual}，期望 N个月`); failures += 1 }
    else console.log(`ok - ${days}天 → ${actual}`)
  }
  // ago 措辞
  assert.equal(mod.relativeTimeAgo(iso(4 * DAY)), "4天前")
  assert.equal(mod.relativeTimeAgo(iso(5_000)), "刚刚")
  console.log("ok - relativeTimeAgo 措辞")
  // 空值与非法值
  assert.equal(mod.relativeTime(null), "")
  assert.equal(mod.relativeTime("not-a-date"), "")
  console.log("ok - 空值容错")

  if (failures > 0) { console.error(`\n${failures} 项失败`); process.exit(1) }
  console.log("\n全部通过")
}

main().catch((error) => { console.error(error); process.exit(1) })
