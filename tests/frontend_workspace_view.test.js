#!/usr/bin/env node
/**
 * Workspace Browser 状态层单测（frontend/src/lib/workspaceViewStore.ts + sessionTree.ts）。
 * 做法：esbuild 把两个 TS 模块连同依赖打成一份 ESM bundle（保证 store 单实例），
 * 在 Node 里直接断言派生与持久化语义；fake localStorage 需在模块加载前就位。
 */
const fs = require("fs")
const os = require("os")
const path = require("path")
const { execSync } = require("child_process")
const assert = require("assert/strict")
const { pathToFileURL } = require("url")

const root = path.resolve(__dirname, "..")
const esbuild = path.join(root, "frontend", "node_modules", ".bin", "esbuild")
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "nova-workspace-view-"))

// store 在 import 时读 localStorage；Node 无该全局，先注入内存版。
const backing = new Map()
globalThis.localStorage = {
  getItem: (key) => (backing.has(key) ? backing.get(key) : null),
  setItem: (key, value) => { backing.set(key, String(value)) },
  removeItem: (key) => { backing.delete(key) },
}

const entry = path.join(tmp, "entry.ts")
fs.writeFileSync(entry, [
  `export * as store from ${JSON.stringify(path.join(root, "frontend/src/lib/workspaceViewStore.ts"))}`,
  `export * as tree from ${JSON.stringify(path.join(root, "frontend/src/lib/sessionTree.ts"))}`,
].join("\n"))
execSync(`"${esbuild}" "${entry}" --bundle --format=esm --target=es2022 --outfile="${path.join(tmp, "bundle.mjs")}"`, { stdio: "pipe" })

const bundleUrl = pathToFileURL(path.join(tmp, "bundle.mjs")).href
let loadCount = 0
async function loadModules() {
  // query 使每次加载都是新模块实例，用于模拟刷新后从 localStorage 恢复。
  loadCount += 1
  return import(`${bundleUrl}?instance=${loadCount}`)
}

/** 微任务 + 宏任务各等一拍，确保 queueWorkspaceAccountSync 的回写落地。 */
const flushSync = async () => { await new Promise((resolve) => setTimeout(resolve, 0)) }

const failures = []
let current = ""
function test(name, fn) {
  current = name
  try {
    fn()
    console.log(`ok - ${name}`)
  } catch (error) {
    failures.push(name)
    console.error(`FAIL - ${name}\n  ${error && error.stack ? error.stack : error}`)
  }
}
async function testAsync(name, fn) {
  current = name
  try {
    await fn()
    console.log(`ok - ${name}`)
  } catch (error) {
    failures.push(name)
    console.error(`FAIL - ${name}\n  ${error && error.stack ? error.stack : error}`)
  }
}

const WS_A = "/Users/liam/Code/codex/nova"
const WS_B = "/Users/liam/Code/codex/zotero-note-md"
const KEY_A = WS_A
const KEY_B = WS_B

function session(id, overrides = {}) {
  return {
    id,
    title: `会话 ${id}`,
    workspace: WS_A,
    created_at: "2026-09-20T10:00:00Z",
    updated_at: "2026-09-20T10:00:00Z",
    ...overrides,
  }
}

const ids = (list) => list.map((item) => item.id)

;(async () => {
  const { store, tree } = await loadModules()

  test("默认视图状态：workspace 分组 + updated 排序 + 空 account", () => {
    const snapshot = store.workspaceViewSnapshot()
    assert.equal(snapshot.groupBy, "workspace")
    assert.equal(snapshot.orderBy, "updated")
    assert.deepEqual(snapshot.groupExpansion, {})
    assert.deepEqual(snapshot.sessionOrderByAccount, {})
    assert.deepEqual(snapshot.sessionUpdatedAtByAccount, {})
  })

  test("App 契约导出齐全（useSyncExternalStore 三件套 + 动作）", () => {
    for (const name of [
      "subscribeWorkspaceView", "workspaceViewSnapshot", "setWorkspaceGroupBy",
      "setWorkspaceOrderBy", "toggleWorkspaceGroup", "setWorkspaceGroupExpansion",
      "setWorkspaceAccountOrder", "workspaceAccountOrder", "resetWorkspaceViewForTests",
      "queueWorkspaceAccountSync", "workspaceUpdatedAtSnapshot", "consumeWorkspaceFullResort",
    ]) {
      assert.equal(typeof store[name], "function", `store.${name} 应为函数`)
    }
    for (const name of ["deriveSessionGroups", "reconcileSessionOrder", "nextSessionOrderAccount", "computeAccountSync"]) {
      assert.equal(typeof tree[name], "function", `tree.${name} 应为函数`)
    }
    assert.equal(tree.UNGROUPED_KEY, "__ungrouped__")
    assert.equal(tree.FLAT_SESSION_ORDER_KEY, "__flat_session_order__")
  })

  const a1 = session("a1", { updated_at: "2026-09-20T10:00:00Z" })
  const a2 = session("a2", { updated_at: "2026-09-21T11:00:00Z" })
  const a3 = session("a3", { updated_at: "2026-09-22T09:00:00Z" })
  const b1 = session("b1", { workspace: WS_B, updated_at: "2026-09-19T08:00:00Z" })
  const loose = session("loose", { workspace: null, updated_at: "2026-09-18T08:00:00Z" })

  test("workspace 分组：按路径分节 + 未分组桶 + 归档过滤", () => {
    const archived = session("gone", { workspace: WS_B, archived: true })
    const groups = tree.deriveSessionGroups(
      [a3, a2, b1, loose, archived, a1], "a1", "", "workspace", "updated",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    assert.deepEqual(groups.map((group) => group.key), [KEY_A, KEY_B, tree.UNGROUPED_KEY])
    assert.equal(groups[0].name, "nova")
    assert.equal(groups[1].name, "zotero-note-md")
    assert.equal(groups[2].name, "未分组")
    assert.equal(groups[2].ungrouped, true)
    assert.ok(!ids(groups.flatMap((group) => group.sessions)).includes("gone"), "归档会话不应出现")
    // 首次观察 updated：组内按 updatedAt 倒序
    assert.deepEqual(ids(groups[0].sessions), ["a3", "a2", "a1"])
    assert.deepEqual(ids(groups[1].sessions), ["b1"])
    assert.deepEqual(ids(groups[2].sessions), ["loose"])
  })

  test("空白标题会话仅选中时可见", () => {
    const blank = session("blank", { title: "新会话" })
    const hidden = tree.deriveSessionGroups([blank, a1], null, "", "workspace", "updated", {})
    assert.ok(!ids(hidden[0].sessions).includes("blank"))
    const shown = tree.deriveSessionGroups([blank, a1], "blank", "", "workspace", "updated", {})
    assert.ok(ids(shown[0].sessions).includes("blank"))
  })

  test("flat 模式：单一“全部会话”组且含跨工作区会话", () => {
    const groups = tree.deriveSessionGroups(
      [a1, b1, loose], null, "", "flat", "updated",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    assert.equal(groups.length, 1)
    assert.equal(groups[0].key, tree.FLAT_SESSION_ORDER_KEY)
    assert.equal(groups[0].name, "全部会话")
    assert.deepEqual(ids(groups[0].sessions), ["a1", "b1", "loose"])
  })

  test("搜索按标题过滤，但不污染 account 记账", async () => {
    const before = JSON.stringify(store.workspaceViewSnapshot().sessionOrderByAccount)
    const groups = tree.deriveSessionGroups(
      [a1, a2, a3], null, "会话 a2", "workspace", "manual",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    await flushSync()
    assert.deepEqual(ids(groups[0].sessions), ["a2"])
    assert.equal(JSON.stringify(store.workspaceViewSnapshot().sessionOrderByAccount), before)
  })

  test("manual 模式：持久化顺序生效，新会话追加尾部、消失会话剔除", () => {
    store.setWorkspaceOrderBy("manual")
    store.setWorkspaceAccountOrder(KEY_A, ["a2", "a1"])
    const newcomer = session("a4", { updated_at: "2026-09-22T12:00:00Z" })
    const groups = tree.deriveSessionGroups(
      [a3, a1, a2, newcomer], null, "", "workspace", "manual",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    assert.deepEqual(ids(groups[0].sessions), ["a2", "a1", "a3", "a4"])
    const shrink = tree.deriveSessionGroups(
      [a1, a2], null, "", "workspace", "manual",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    assert.deepEqual(ids(shrink[0].sessions), ["a2", "a1"])
  })

  test("updated 模式活跃提升：仅比账本新的会话置顶一次，之后保持稳定", async () => {
    store.resetWorkspaceViewForTests()
    store.setWorkspaceOrderBy("updated")
    // 首次观察：全量按时间倒序 → [a3, a2, a1]
    let groups = tree.deriveSessionGroups(
      [a1, a2, a3], null, "", "workspace", "updated",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    assert.deepEqual(ids(groups[0].sessions), ["a3", "a2", "a1"])
    await flushSync()
    const persisted = store.workspaceAccountOrder(KEY_A)
    assert.deepEqual(persisted, ["a3", "a2", "a1"], "首次观察顺序应回写持久化")

    // a2 有新动静（updatedAt 更新）→ 提升到顶部；a3/a1 保持相对位置
    const a2Fresh = { ...a2, updated_at: "2026-09-22T10:30:00Z" }
    groups = tree.deriveSessionGroups(
      [a1, a2Fresh, a3], null, "", "workspace", "updated",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    assert.deepEqual(ids(groups[0].sessions), ["a2", "a3", "a1"], "只有 a2 应被提升")
    await flushSync()

    // 再派生一次（无任何变化）：顺序不再跳动（不是持续按时间重排）
    groups = tree.deriveSessionGroups(
      [a1, a2Fresh, a3], null, "", "workspace", "updated",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    assert.deepEqual(ids(groups[0].sessions), ["a2", "a3", "a1"], "无变化时顺序必须稳定")
    await flushSync()

    // a1 现在最新 → 只有 a1 提升，其余稳定
    const a1Fresh = { ...a1, updated_at: "2026-09-22T11:00:00Z" }
    groups = tree.deriveSessionGroups(
      [a1Fresh, a2Fresh, a3], null, "", "workspace", "updated",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    assert.deepEqual(ids(groups[0].sessions), ["a1", "a2", "a3"])
    await flushSync()
  })

  test("切换到 updated 触发一次全量重排（switchedToUpdated 语义）", async () => {
    store.resetWorkspaceViewForTests()
    store.setWorkspaceOrderBy("manual")
    store.setWorkspaceAccountOrder(KEY_A, ["a1", "a3", "a2"])
    store.setWorkspaceOrderBy("updated")
    const groups = tree.deriveSessionGroups(
      [a1, a2, a3], null, "", "workspace", "updated",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    assert.deepEqual(ids(groups[0].sessions), ["a3", "a2", "a1"], "切换后应全量按时间重排")
    await flushSync()
    // 此后回到稳定提升语义：无变化不再重排
    const again = tree.deriveSessionGroups(
      [a1, a2, a3], null, "", "workspace", "updated",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    assert.deepEqual(ids(again[0].sessions), ["a3", "a2", "a1"])
    await flushSync()
  })

  test("手动排序（拖拽入口）写入 account 并按 workspace 隔离", () => {
    store.resetWorkspaceViewForTests()
    store.setWorkspaceAccountOrder(KEY_A, ["a1", "a2"])
    store.setWorkspaceAccountOrder(KEY_B, ["b1"])
    assert.deepEqual(store.workspaceAccountOrder(KEY_A), ["a1", "a2"])
    assert.deepEqual(store.workspaceAccountOrder(KEY_B), ["b1"])
    assert.deepEqual(store.workspaceAccountOrder("/unknown"), [])
  })

  test("分组折叠：缺省展开，第一次点击折叠，再点展开", () => {
    store.resetWorkspaceViewForTests()
    assert.equal(store.workspaceViewSnapshot().groupExpansion[KEY_A], undefined)
    store.toggleWorkspaceGroup(KEY_A)
    assert.equal(store.workspaceViewSnapshot().groupExpansion[KEY_A], false)
    store.toggleWorkspaceGroup(KEY_A)
    assert.equal(store.workspaceViewSnapshot().groupExpansion[KEY_A], true)
    store.setWorkspaceGroupExpansion(KEY_A, false)
    assert.equal(store.workspaceViewSnapshot().groupExpansion[KEY_A], false)
  })

  test("subscribe 通知与引用稳定：无变化派生不换 snapshot 引用", async () => {
    store.resetWorkspaceViewForTests()
    let notified = 0
    const unsubscribe = store.subscribeWorkspaceView(() => { notified += 1 })
    store.setWorkspaceGroupBy("flat") // 1 次通知
    const groups = tree.deriveSessionGroups(
      [a1, a2], null, "", "flat", "updated",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    await flushSync() // 首次观察回写（1 次通知）
    assert.deepEqual(ids(groups[0].sessions), ["a2", "a1"])
    const stable = store.workspaceViewSnapshot()
    tree.deriveSessionGroups(
      [a1, a2], null, "", "flat", "updated",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    await flushSync() // 不动点：不应有任何写入或通知
    assert.equal(store.workspaceViewSnapshot(), stable, "无变化时 snapshot 必须同引用")
    assert.equal(notified, 2)
    unsubscribe()
    store.setWorkspaceGroupBy("workspace")
    assert.equal(notified, 2, "退订后不再通知")
  })

  test("死亡 account 回收：顺序里没有存活会话的 key 被清掉", async () => {
    store.resetWorkspaceViewForTests()
    store.setWorkspaceAccountOrder("/dead/workspace", ["x1", "x2"])
    store.setWorkspaceAccountOrder(KEY_A, ["a1"])
    tree.deriveSessionGroups(
      [a1, a2], null, "", "workspace", "updated",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    await flushSync()
    const accounts = store.workspaceViewSnapshot().sessionOrderByAccount
    assert.ok(!("/dead/workspace" in accounts), "死亡 account 应被回收")
    assert.ok(KEY_A in accounts)
  })

  test("flat 与 workspace 模式切换不互删对方的 account", async () => {
    store.resetWorkspaceViewForTests()
    tree.deriveSessionGroups([a1, b1], null, "", "workspace", "updated", {})
    await flushSync()
    assert.ok(KEY_A in store.workspaceViewSnapshot().sessionOrderByAccount)
    tree.deriveSessionGroups(
      [a1, b1], null, "", "flat", "updated",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    await flushSync()
    const accounts = store.workspaceViewSnapshot().sessionOrderByAccount
    assert.ok(tree.FLAT_SESSION_ORDER_KEY in accounts, "flat account 应建立")
    assert.ok(KEY_A in accounts, "workspace account 应因会话仍存活而保留")
  })

  test("空会话列表（加载中瞬态）不清空持久化 account", async () => {
    store.resetWorkspaceViewForTests()
    store.setWorkspaceAccountOrder(KEY_A, ["a1", "a2"])
    tree.deriveSessionGroups([], null, "", "workspace", "updated", store.workspaceViewSnapshot().sessionOrderByAccount)
    await flushSync()
    assert.deepEqual(store.workspaceAccountOrder(KEY_A), ["a1", "a2"])
  })

  test("归档会话保留记账位：反归档后顺序可恢复", async () => {
    store.resetWorkspaceViewForTests()
    tree.deriveSessionGroups([a1, a2, a3], null, "", "workspace", "manual", {})
    store.setWorkspaceAccountOrder(KEY_A, ["a3", "a1", "a2"])
    await flushSync()
    const archivedView = tree.deriveSessionGroups(
      [a1, { ...a3, archived: true }, a2], null, "", "workspace", "manual",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    assert.deepEqual(ids(archivedView[0].sessions), ["a3", "a1", "a2"].filter((id) => id !== "a3"))
    await flushSync()
    assert.ok(store.workspaceAccountOrder(KEY_A).includes("a3"), "归档会话仍应占记账位")
    const restored = tree.deriveSessionGroups(
      [a1, a3, a2], null, "", "workspace", "manual",
      store.workspaceViewSnapshot().sessionOrderByAccount,
    )
    assert.deepEqual(ids(restored[0].sessions), ["a3", "a1", "a2"], "反归档后恢复原位")
    await flushSync()
  })

  test("持久化往返：刷新（重新加载模块）后视图状态从 localStorage 恢复", async () => {
    store.resetWorkspaceViewForTests()
    store.setWorkspaceGroupBy("flat")
    store.setWorkspaceOrderBy("manual")
    store.toggleWorkspaceGroup(KEY_A)
    tree.deriveSessionGroups([a1, a2], null, "", "flat", "manual", store.workspaceViewSnapshot().sessionOrderByAccount)
    await flushSync()
    const reloaded = await loadModules()
    const snapshot = reloaded.store.workspaceViewSnapshot()
    assert.equal(snapshot.groupBy, "flat")
    assert.equal(snapshot.orderBy, "manual")
    assert.equal(snapshot.groupExpansion[KEY_A], false)
    assert.deepEqual(snapshot.sessionOrderByAccount[tree.FLAT_SESSION_ORDER_KEY], ["a1", "a2"])
    const groups = reloaded.tree.deriveSessionGroups(
      [a1, a2], null, "", "flat", "manual", snapshot.sessionOrderByAccount,
    )
    assert.deepEqual(ids(groups[0].sessions), ["a1", "a2"], "刷新后 manual 顺序不丢")
  })

  test("坏持久化数据容错：非法 JSON / 错误类型回落默认值", async () => {
    backing.set("nova.workspace.view.v1", "{not-json")
    const broken = await loadModules()
    const snapshot = broken.store.workspaceViewSnapshot()
    assert.equal(snapshot.groupBy, "workspace")
    assert.deepEqual(snapshot.sessionOrderByAccount, {})
    backing.set("nova.workspace.view.v1", JSON.stringify({ groupBy: "flat", orderBy: "weird", sessionOrderByAccount: { x: [1, 2] } }))
    const coerced = await loadModules()
    const coercedSnapshot = coerced.store.workspaceViewSnapshot()
    assert.equal(coercedSnapshot.groupBy, "flat")
    assert.equal(coercedSnapshot.orderBy, "updated", "非法 orderBy 应回落 updated")
    assert.deepEqual(coercedSnapshot.sessionOrderByAccount, {}, "非字符串数组顺序应被丢弃")
  })

  await finish()
})().catch(async (error) => {
  console.error(`FAIL - ${current}: ${error && error.stack ? error.stack : error}`)
  failures.push(current || "加载失败")
  await finish()
})

async function finish() {
  if (failures.length > 0) {
    console.error(`\n${failures.length} 个用例失败`)
    process.exit(1)
  }
  console.log("\n全部通过")
  process.exit(0)
}
