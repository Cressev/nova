/**
 * Workspace Browser 视图状态层（对齐 dsh ui-workspace stores.ts 的持久化语义）。
 *
 * 状态分五块：
 * - groupBy / orderBy：全局面板模式（workspace 分组 / flat 平铺；updated 活跃提升 / manual 纯手动）。
 * - groupExpansion：按分组 key 显式记录的展开/折叠；缺省视为展开（App 侧 `!== false` 判断）。
 * - sessionOrderByAccount：每个 account（workspace 分组、未分组桶或 flat 列表）的会话顺序。
 * - sessionUpdatedAtByAccount：每个 account 上次观察到的 updatedAt 时间戳（ms），
 *   是“一次性活跃提升”的账本：只有比账本新的会话才会被提到顶部，避免 updated 模式下
 *   列表随时间戳持续跳动（dsh 的 activity promotion 语义）。
 *
 * 关键设计点：
 * 1. state 必须整体不可变替换，workspaceViewSnapshot 在无变化时返回同一引用，
 *    才能安全接入 useSyncExternalStore（引用变化即触发重渲染）。
 * 2. 排序策略不在本文件：sessionTree 派生时计算新 account 快照，经
 *    queueWorkspaceAccountSync 在微任务里回写；写入前做深比较，无变化不通知，
 *    保证 render → sync → render 收敛到不动点，不产生死循环。
 * 3. 回收策略：account 中的顺序若已不引用任何现存会话 id（例如整个 workspace 的
 *    会话都被删除），同步时整体丢弃，防止 localStorage 无限增长；归档会话仍占位，
 *    反归档后位置可恢复（对齐 dsh accounting slots 语义）。
 * 4. 持久化键 nova.workspace.view.v1；localStorage 不可用（隐私模式 / Node 测试）
 *    时静默降级为纯内存态，不阻断会话。
 */

/** 会话列表分组模式：按 workspace 分节，或单一平铺列表。 */
export type WorkspaceGroupBy = "workspace" | "flat"
/** 会话排序模式：manual 只认手动顺序；updated 在手动顺序上叠加一次性活跃提升。 */
export type WorkspaceOrderBy = "manual" | "updated"

/** sessionTree 派生后回写的一个同步快照。 */
export interface WorkspaceAccountSync {
  /** 本轮活跃模式下计算出的 account → 会话顺序。 */
  sessionOrderByAccount: Record<string, string[]>
  /** account → 会话 id → 上次观察到的 updatedAt（ms）。 */
  sessionUpdatedAtByAccount: Record<string, Record<string, number>>
  /** 本轮活跃模式实际观察到的 account key（未观察到的旧 key 走存活判定后再决定去留）。 */
  retainKeys: readonly string[]
  /** 当前会话全量 id 集合（含归档），用于判定旧 account 是否完全死亡。 */
  liveSessionIds: ReadonlySet<string>
}

export interface WorkspaceViewState {
  groupBy: WorkspaceGroupBy
  orderBy: WorkspaceOrderBy
  groupExpansion: Record<string, boolean>
  sessionOrderByAccount: Record<string, string[]>
  sessionUpdatedAtByAccount: Record<string, Record<string, number>>
  workspaceOrder: string[]
}

const STORAGE_KEY = "nova.workspace.view.v1"
const DEFAULT_STATE: WorkspaceViewState = {
  groupBy: "workspace",
  orderBy: "updated",
  groupExpansion: {},
  sessionOrderByAccount: {},
  sessionUpdatedAtByAccount: {},
  workspaceOrder: [],
}

/** 宽容反序列化：坏数据一律回落默认值，不抛错（对齐 dsh persist 的容错姿态）。 */
function loadState(): WorkspaceViewState {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null") as Partial<WorkspaceViewState> | null
    const orderValid = (value: unknown): value is Record<string, string[]> =>
      typeof value === "object" && value !== null && Object.values(value).every(
        (list) => Array.isArray(list) && list.every((id) => typeof id === "string"),
      )
    const stampsValid = (value: unknown): value is Record<string, Record<string, number>> =>
      typeof value === "object" && value !== null && Object.values(value).every(
        (map) => typeof map === "object" && map !== null && Object.values(map).every((n) => typeof n === "number"),
      )
    return {
      groupBy: raw?.groupBy === "flat" ? "flat" : "workspace",
      orderBy: raw?.orderBy === "manual" ? "manual" : "updated",
      groupExpansion: raw?.groupExpansion && typeof raw.groupExpansion === "object" ? raw.groupExpansion : {},
      sessionOrderByAccount: orderValid(raw?.sessionOrderByAccount) ? raw.sessionOrderByAccount : {},
      sessionUpdatedAtByAccount: stampsValid(raw?.sessionUpdatedAtByAccount) ? raw.sessionUpdatedAtByAccount : {},
      workspaceOrder: Array.isArray(raw?.workspaceOrder) ? raw.workspaceOrder.filter((id): id is string => typeof id === "string") : [],
    }
  } catch {
    return freshState()
  }
}

function freshState(): WorkspaceViewState {
  return {
    ...DEFAULT_STATE,
    groupExpansion: {},
    sessionOrderByAccount: {},
    sessionUpdatedAtByAccount: {},
  }
}

let state = loadState()
const listeners = new Set<() => void>()

/** 切到 updated 后下一轮派生需要全量按时间重排一次（对齐 dsh switchedToUpdated）。 */
let fullResortHint = false
/** 微任务去重：一轮渲染内多次派生只回写最后一次。 */
let pendingSync: WorkspaceAccountSync | null = null
let syncScheduled = false

function persist() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)) } catch { /* 隐私模式或配额不足不阻断会话 */ }
  listeners.forEach((listener) => listener())
}

/** 深比较两个 account 快照是否等值（顺序数组逐位 + 时间戳逐项）。 */
function accountMapsEqual(
  a: Record<string, string[]>, b: Record<string, string[]>,
  ta: Record<string, Record<string, number>>, tb: Record<string, Record<string, number>>,
): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)])
  for (const key of keys) {
    const left = a[key], right = b[key]
    if (left === undefined || right === undefined) return false
    if (left.length !== right.length || left.some((id, index) => id !== right[index])) return false
    const tLeft = ta[key] ?? {}, tRight = tb[key] ?? {}
    const ids = new Set([...Object.keys(tLeft), ...Object.keys(tRight)])
    for (const id of ids) if (tLeft[id] !== tRight[id]) return false
  }
  return true
}

/* ------------------------------ 读侧 ------------------------------ */

export function workspaceViewSnapshot(): WorkspaceViewState { return state }

export function subscribeWorkspaceView(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function workspaceOrderSnapshot(): string[] { return state.workspaceOrder }
export function setWorkspaceOrder(order: string[]) { state = { ...state, workspaceOrder: [...new Set(order)] }; persist() }

export function workspaceAccountOrder(accountKey: string): string[] {
  return state.sessionOrderByAccount[accountKey] || []
}

/** sessionTree 派生用的只读账本（App 不直接消费）。 */
export function workspaceUpdatedAtSnapshot(): Record<string, Record<string, number>> {
  return state.sessionUpdatedAtByAccount
}

/** 一次性消费“切到 updated 需全量重排”提示；每轮派生只生效一次。 */
export function consumeWorkspaceFullResort(): boolean {
  if (!fullResortHint) return false
  fullResortHint = false
  return true
}

/* ------------------------------ 写侧 ------------------------------ */

export function setWorkspaceGroupBy(groupBy: WorkspaceGroupBy) {
  if (state.groupBy !== groupBy) { state = { ...state, groupBy }; persist() }
}

export function setWorkspaceOrderBy(orderBy: WorkspaceOrderBy) {
  if (state.orderBy !== orderBy) {
    state = { ...state, orderBy }
    // 切回 updated 时立即全量按活跃度重排一次，让用户看到模式切换生效；
    // 之后回到“仅提升有变化的会话”的稳定语义。
    if (orderBy === "updated") fullResortHint = true
    persist()
  }
}

/** 分组折叠开关。缺省（undefined）视为展开，因此第一次点击是折叠。 */
export function toggleWorkspaceGroup(groupKey: string) {
  setWorkspaceGroupExpansion(groupKey, !(state.groupExpansion[groupKey] ?? true))
}

export function setWorkspaceGroupExpansion(groupKey: string, expanded: boolean) {
  if (state.groupExpansion[groupKey] !== expanded) {
    state = { ...state, groupExpansion: { ...state.groupExpansion, [groupKey]: expanded } }
    persist()
  }
}

/** 手动排序入口（拖拽重排接这里）；重复 id 自动去重。 */
export function setWorkspaceAccountOrder(accountKey: string, ids: string[]) {
  state = {
    ...state,
    sessionOrderByAccount: { ...state.sessionOrderByAccount, [accountKey]: [...new Set(ids)] },
  }
  persist()
}

/**
 * 微任务里回写 sessionTree 派生出的 account 快照。
 * 不在渲染路径上同步写 state：外部 store 在 render 期间被通知会触发 React 警告，
 * 延迟到微任务既保证本轮渲染可读旧值，又在 paint 前完成收敛。
 */
export function queueWorkspaceAccountSync(sync: WorkspaceAccountSync) {
  pendingSync = sync
  if (syncScheduled) return
  syncScheduled = true
  queueMicrotask(() => {
    syncScheduled = false
    const next = pendingSync
    pendingSync = null
    if (!next) return
    applyAccountSync(next)
  })
}

function applyAccountSync(sync: WorkspaceAccountSync) {
  const observed = new Set(sync.retainKeys)
  const mergedOrder: Record<string, string[]> = {}
  const mergedStamps: Record<string, Record<string, number>> = {}
  // 先并入选定的快照，再按存活判定保留未观察到的旧 account
  //（另一模式的 account 或暂无可见会话的 workspace）。
  const keys = new Set<string>([...Object.keys(state.sessionOrderByAccount), ...observed])
  for (const key of keys) {
    const fresh = observed.has(key)
    if (fresh) {
      mergedOrder[key] = sync.sessionOrderByAccount[key]
      mergedStamps[key] = sync.sessionUpdatedAtByAccount[key]
      continue
    }
    const stale = state.sessionOrderByAccount[key]
    const alive = stale !== undefined && stale.some((id) => sync.liveSessionIds.has(id))
    if (!alive) continue // 完全死亡的 account：整体回收
    mergedOrder[key] = stale
    mergedStamps[key] = state.sessionUpdatedAtByAccount[key] ?? {}
  }
  if (accountMapsEqual(mergedOrder, state.sessionOrderByAccount, mergedStamps, state.sessionUpdatedAtByAccount)) {
    return // 不动点：不写不通知，阻断 render → sync 循环
  }
  state = {
    ...state,
    sessionOrderByAccount: mergedOrder,
    sessionUpdatedAtByAccount: mergedStamps,
  }
  persist()
}

/** 测试隔离：恢复默认态（会写穿到 localStorage，测试里配合 fake storage 使用）。 */
export function resetWorkspaceViewForTests() {
  state = freshState()
  fullResortHint = false
  pendingSync = null
  persist()
}
