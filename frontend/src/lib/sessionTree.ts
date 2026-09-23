/**
 * 会话树派生层（对齐 dsh ui-workspace tree.ts + WorkspaceBrowser 排序策略的最小移植）。
 *
 * 职责：把后端会话列表 + 视图状态投影成侧栏渲染所需的分组结构，
 * 并在派生时顺带对账每个 account 的顺序（含“一次性活跃提升”），
 * 经 queueWorkspaceAccountSync 异步回写 store。
 *
 * 关键设计点：
 * 1. orderBy=manual：完全按持久化顺序排；未知会话按后端到达序追加到尾部。
 * 2. orderBy=updated：不是每次全量按时间重排（列表会持续跳动），而是
 *    “手动顺序 + 活跃提升”：只有 updatedAt 比账本（sessionUpdatedAtByAccount）
 *    新的会话被提升到顶部一次，提升后记账，之后保持稳定。
 *    首次观察某 account（无持久化顺序）或切换到 updated 模式时才全量按时间排。
 * 3. account=每个 workspace 分组 / 未分组桶 / flat 列表；归档会话不参与渲染，
 *    但保留 account 记账位，反归档后可恢复原位置（dsh accounting slots 语义）。
 * 4. 派生是渲染路径上的读操作：对账结果只经微任务回写，且 store 侧做了
 *    不动点比较，保证第二次派生与第一次等值、不会循环触发重渲染。
 */

import type { ChatSession } from "../types"
import { projectName, workspaceGroupKey } from "./api"
import {
  consumeWorkspaceFullResort,
  queueWorkspaceAccountSync,
  workspaceUpdatedAtSnapshot,
  type WorkspaceAccountSync,
} from "./workspaceViewStore"

export const UNGROUPED_KEY = "__ungrouped__"
export const FLAT_SESSION_ORDER_KEY = "__flat_session_order__"

export interface SessionTreeGroup {
  key: string
  name: string
  workspace: string | null
  sessions: ChatSession[]
  ungrouped: boolean
}

/** updatedAt 缺失时回落 created_at；再缺失记 0，保证比较器总有确定值。 */
function updatedAtMs(session: ChatSession): number {
  const parsed = Date.parse(session.updated_at || session.created_at || "")
  return Number.isNaN(parsed) ? 0 : parsed
}

/** 时间倒序比较器；id 升序兜底，保证全序确定（同组 id 唯一）。 */
export function compareSessionsByRecency(a: ChatSession, b: ChatSession): number {
  const delta = updatedAtMs(b) - updatedAtMs(a)
  return delta !== 0 ? delta : (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)
}

/** 渲染可见性：归档不显示；空白标题（未落正式标题的新会话）仅选中时显示。 */
function sessionVisible(session: ChatSession, selectedId: string | null): boolean {
  if (session.archived) return false
  // 当前版本没有 subagent 来源字段；保留 fork 子会话为普通行。
  return Boolean(session.id) && (!session.title || session.title !== "新会话" || session.id === selectedId)
}

function accountKeyOf(session: ChatSession): string {
  return session.workspace ? workspaceGroupKey(session.workspace) : UNGROUPED_KEY
}

/**
 * 用持久化顺序对账当前会话集合（dsh reconciledSessionOrder 语义）：
 * 已消失的 id 静默剔除，新出现的 id 按到达序追加到尾部。
 */
export function reconcileSessionOrder(sessionIds: readonly string[], stored: readonly string[] | undefined): string[] {
  if (stored === undefined) return [...sessionIds]
  const present = new Set(sessionIds)
  const ordered: string[] = []
  const included = new Set<string>()
  for (const id of stored) {
    if (!present.has(id) || included.has(id)) continue
    ordered.push(id)
    included.add(id)
  }
  for (const id of sessionIds) {
    if (!included.has(id)) ordered.push(id)
  }
  return ordered
}

export interface SessionOrderAccountInput {
  /** account 内的会话（含归档——记账不丢位）。 */
  sessions: ChatSession[]
  /** 该 account 上次持久化的顺序；undefined 表示首次观察。 */
  previousOrder: readonly string[] | undefined
  /** 该 account 上次观察到的时间戳账本。 */
  previousUpdatedAt: Readonly<Record<string, number>>
  orderBy: "updated" | "manual"
  /** 切换到 updated 后的一次性全量重排提示。 */
  fullSort: boolean
}

export interface SessionOrderAccountResult {
  order: string[]
  updatedAt: Record<string, number>
}

/**
 * 对账单个 account 并应用活跃提升策略（dsh nextSessionOrderAccount 语义）。
 * - manual：仅对账（新会话按到达序入尾）。
 * - updated：首次观察 / fullSort 时全量按时间排；否则把 updatedAt 比账本新的
 *   会话按时间序提升到顶部，其余保持既有顺序。
 */
export function nextSessionOrderAccount({
  sessions, previousOrder, previousUpdatedAt, orderBy, fullSort,
}: SessionOrderAccountInput): SessionOrderAccountResult {
  const sessionIds = sessions.map((session) => session.id)
  let order = reconcileSessionOrder(sessionIds, previousOrder)
  if ((previousOrder === undefined || fullSort) && orderBy === "updated") {
    order = [...sessions].sort(compareSessionsByRecency).map((session) => session.id)
  } else if (orderBy === "updated") {
    const promoted = sessions
      .filter((session) => {
        const seen = previousUpdatedAt[session.id]
        return seen === undefined || updatedAtMs(session) > seen
      })
      .sort(compareSessionsByRecency)
      .map((session) => session.id)
    if (promoted.length > 0) {
      const promotedIds = new Set(promoted)
      order = [...promoted, ...order.filter((id) => !promotedIds.has(id))]
    }
  }
  const updatedAt: Record<string, number> = {}
  for (const session of sessions) updatedAt[session.id] = updatedAtMs(session)
  return { order, updatedAt }
}

/**
 * 对当前活跃模式的全部 account 做一轮对账，产出待回写快照。
 * sessions 为空（列表加载中的瞬态）时返回 undefined，跳过回写，
 * 避免把持久化顺序误判为全部死亡而清空。
 */
export function computeAccountSync(
  sessions: readonly ChatSession[],
  groupBy: "workspace" | "flat",
  orderBy: "updated" | "manual",
  storedOrder: Readonly<Record<string, string[]>>,
): WorkspaceAccountSync | undefined {
  if (sessions.length === 0) return undefined
  // fullSort 提示每轮只消费一次，作用于所有 account。
  const fullSort = consumeWorkspaceFullResort()
  const stamps = workspaceUpdatedAtSnapshot()
  const buckets = new Map<string, ChatSession[]>()
  if (groupBy === "flat") {
    buckets.set(FLAT_SESSION_ORDER_KEY, [...sessions])
  } else {
    for (const session of sessions) {
      const key = accountKeyOf(session)
      const bucket = buckets.get(key)
      if (bucket) bucket.push(session)
      else buckets.set(key, [session])
    }
  }
  const sessionOrderByAccount: Record<string, string[]> = {}
  const sessionUpdatedAtByAccount: Record<string, Record<string, number>> = {}
  for (const [key, bucket] of buckets) {
    const result = nextSessionOrderAccount({
      sessions: bucket,
      previousOrder: storedOrder[key],
      previousUpdatedAt: stamps[key] ?? {},
      orderBy,
      fullSort,
    })
    sessionOrderByAccount[key] = result.order
    sessionUpdatedAtByAccount[key] = result.updatedAt
  }
  return {
    sessionOrderByAccount,
    sessionUpdatedAtByAccount,
    retainKeys: [...buckets.keys()],
    liveSessionIds: new Set(sessions.map((session) => session.id)),
  }
}

/**
 * 把会话列表 + 视图状态投影成侧栏分组。
 * 签名与 App.tsx 现有调用保持一致：storedOrder 来自 view.sessionOrderByAccount，
 * 派生内部会先做一轮对账（新会话入尾 / 活跃提升），所以渲染用的是对账后的顺序。
 */
export function deriveSessionGroups(
  sessions: ChatSession[],
  selectedId: string | null,
  query: string,
  groupBy: "workspace" | "flat",
  orderBy: "updated" | "manual",
  storedOrder: Record<string, string[]>,
): SessionTreeGroup[] {
  const sync = computeAccountSync(sessions, groupBy, orderBy, storedOrder)
  if (sync) queueWorkspaceAccountSync(sync)
  const orders = sync?.sessionOrderByAccount ?? storedOrder
  const needle = query.trim().toLowerCase()
  const visible = sessions
    .filter((session) => sessionVisible(session, selectedId))
    .filter((session) => !needle || String(session.title || "").toLowerCase().includes(needle))

  /** 按 account 顺序排列组内会话；顺序账之外的到达者保持后端序追加尾部。 */
  const orderSessions = (list: ChatSession[], accountKey: string): ChatSession[] => {
    const byId = new Map(list.map((session) => [session.id, session]))
    const ordered: ChatSession[] = []
    const included = new Set<string>()
    for (const id of orders[accountKey] ?? []) {
      const session = byId.get(id)
      if (session && !included.has(id)) { ordered.push(session); included.add(id) }
    }
    for (const session of list) {
      if (!included.has(session.id)) ordered.push(session)
    }
    return ordered
  }

  if (groupBy === "flat") {
    return [{
      key: FLAT_SESSION_ORDER_KEY,
      name: "全部会话",
      workspace: null,
      sessions: orderSessions(visible, FLAT_SESSION_ORDER_KEY),
      ungrouped: false,
    }]
  }

  const map = new Map<string, ChatSession[]>()
  for (const session of visible) {
    const key = accountKeyOf(session)
    const bucket = map.get(key)
    if (bucket) bucket.push(session)
    else map.set(key, [session])
  }
  return [...map.entries()].map(([key, list]) => ({
    key,
    name: key === UNGROUPED_KEY ? "未分组" : projectName(list[0]?.workspace || key),
    workspace: key === UNGROUPED_KEY ? null : list[0]?.workspace || null,
    sessions: orderSessions(list, key),
    ungrouped: key === UNGROUPED_KEY,
  }))
}
