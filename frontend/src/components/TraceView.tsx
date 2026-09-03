import { useEffect, useMemo, useRef, useState } from "react"
import type { ChatMessage, TraceEvent } from "../types"
import { api } from "../lib/api"

/* ============================================================================
   轨迹视图 —— 对齐 dsh ui-trajectory 的形态：
   ① 顶部 Chrome-Network 式 tile 时间线（Input/Model/Tools 三泳道，
      按会话时间轴分 bin 着色，红=失败工具）；
   ② 工具条（Duration/Turns/Calls 维度切换 + 搜索过滤）；
   ③ 下方 turn 分组事件流（脊线 + 圆点 + 角色徽章 + 单行摘要，
      Tool 行等宽字体 "bash {…} → 输出"，Turn N 分组头内嵌脊线）。
   数据源：runtime-state timeline（messages + events 按 sequence 融合）。
   ========================================================================== */

interface FusedRecord {
  key: string
  kind: "user" | "message" | "tool" | "system"
  text: string
  tool: string | null
  ts: number
  turn: number
  failed: boolean
  durationMs: number | null
}

interface TurnGroup {
  turn: number
  startedAt: number
  records: FusedRecord[]
}

function monospaceToolText(tool: string, args: Record<string, unknown>, output: string | null): string {
  const argsPreview = Object.keys(args || {}).length > 0
    ? JSON.stringify(args).slice(0, 120)
    : ""
  const out = output && output.trim() ? output.split("\n").filter(Boolean).slice(-1)[0]?.slice(0, 60) : ""
  const head = `${tool}${argsPreview ? ` ${argsPreview}` : ""}`
  return out ? `${head} → ${out}` : head
}

function foldRecords(messages: ChatMessage[], events: TraceEvent[]): { turns: TurnGroup[]; totalMs: number } {
  // 融合：按时间排序；turn.started 开新组；user 消息也开新组（dsh 语义：用户输入开启轮次）
  interface Seed {
    ts: number
    seed: () => void
  }
  const seeds: Seed[] = []
  const msgById = new Map(messages.map((m) => [m.id, m]))
  for (const m of messages) {
    const ts = m.created_at ? Date.parse(m.created_at) : NaN
    if (Number.isNaN(ts)) continue
    seeds.push({
      ts,
      seed: () => {
        if (m.role === "user") {
          pushRecord({ kind: "user", text: m.content || "", tool: null, ts, failed: false, durationMs: null })
          openTurn()
        } else if (m.role === "assistant") {
          const text = (m.content || "").trim()
          pushRecord({ kind: "message", text: text === "" ? "(tool call only)" : text, tool: null, ts, failed: false, durationMs: null })
        }
      },
    })
  }
  for (const e of events) {
    const ts = e.created_at ? Date.parse(e.created_at) : NaN
    if (Number.isNaN(ts)) continue
    seeds.push({
      ts,
      seed: () => {
        const type = String(e.event_type || "")
        if (type === "turn.started") {
          openTurn()
          return
        }
        if (type.startsWith("turn.") || type === "status" || type === "memory.compacted" || type.startsWith("hook.")) {
          return
        }
        if (type === "permission.requested") {
          pushRecord({ kind: "system", text: `permission · ${e.tool || "工具"} 待确认`, tool: e.tool || null, ts, failed: false, durationMs: null })
          return
        }
        if (type === "user.question") {
          pushRecord({ kind: "system", text: "ask · 等待用户回答", tool: "ask_user_question", ts, failed: false, durationMs: null })
          return
        }
        if (type.startsWith("tool.")) {
          const durationMs = typeof (e as TraceEvent & { duration_ms?: number }).duration_ms === "number"
            ? (e as TraceEvent & { duration_ms?: number }).duration_ms!
            : null
          const args = (e.arguments || {}) as Record<string, unknown>
          const output = typeof e.output === "string" ? e.output : null
          pushRecord({
            kind: "tool",
            text: monospaceToolText(String(e.tool || "tool"), args, output),
            tool: String(e.tool || "tool"),
            ts,
            failed: e.status === "failed" || e.status === "error",
            durationMs,
          })
        }
      },
    })
  }
  seeds.sort((a, b) => a.ts - b.ts)

  const turnsAll: TurnGroup[] = []
  let current: TurnGroup | null = null
  let seq = 0
  function openTurn() {
    current = { turn: turnsAll.length + 1, startedAt: NaN, records: [] }
    turnsAll.push(current)
  }
  function pushRecord(partial: Omit<FusedRecord, "key" | "turn">) {
    if (!current) openTurn()
    const record: FusedRecord = { ...partial, key: `r-${seq++}`, turn: current!.turn }
    current!.records.push(record)
    if (Number.isNaN(current!.startedAt)) current!.startedAt = record.ts
  }
  void msgById
  for (const s of seeds) s.seed()
  // 空轮次（只有 turn.started，无任何记录）不参与渲染
  const turns = turnsAll.filter((t) => t.records.length > 0)
  const all = turns.flatMap((t) => t.records)
  const totalMs = all.length >= 2 ? all[all.length - 1].ts - all[0].ts : 0
  return { turns, totalMs }
}

/* ---- 泳道 tile 时间线（Chrome Network 概览形态） ---- */
type TimelineMode = "duration" | "turns" | "calls"

const KIND_LANE: Record<FusedRecord["kind"], "input" | "model" | "tools"> = {
  user: "input",
  message: "model",
  tool: "tools",
  system: "tools",
}

function TimelineLanes({ turns, mode, totalMs }: { turns: TurnGroup[]; mode: TimelineMode; totalMs: number }) {
  const BINS = 48
  const records = useMemo(() => turns.flatMap((t) => t.records), [turns])
  const start = records.length ? records[0].ts : 0
  const end = records.length ? records[records.length - 1].ts : 1
  const span = Math.max(1, end - start)

  const lanes: Array<Array<{ color: string; count: number }>> = [
    Array.from({ length: BINS }, () => ({ color: "", count: 0 })),
    Array.from({ length: BINS }, () => ({ color: "", count: 0 })),
    Array.from({ length: BINS }, () => ({ color: "", count: 0 })),
  ]
  for (const record of records) {
    const lane = KIND_LANE[record.kind] === "input" ? 0 : KIND_LANE[record.kind] === "model" ? 1 : 2
    const bin = Math.min(BINS - 1, Math.floor(((record.ts - start) / span) * BINS))
    const cell = lanes[lane][bin]
    cell.count += 1
    if (record.failed) cell.color = "#e5484d"
    else if (lane === 0) cell.color = cell.color || "#22c55e"
    else if (lane === 1) cell.color = cell.color || "#a78bfa"
    else cell.color = cell.color || "#f59e0b"
  }
  if (mode === "turns") {
    for (const turn of turns) {
      const bin = Math.min(BINS - 1, Math.floor(((turn.startedAt - start) / span) * BINS))
      lanes[0][bin].color = "#3b82f6"
    }
  }
  if (mode === "calls") {
    for (let i = 0; i < 3; i += 1) {
      for (let bin = 0; bin < BINS; bin += 1) {
        lanes[i][bin].color = lanes[i][bin].count > 0 ? lanes[i][bin].color : ""
      }
    }
  }

  return (
    <div className="trajectory-timeline" aria-label="轨迹时间线">
      <div className="trajectory-lane-labels" aria-hidden="true">
        <span>Input</span>
        <span>Model</span>
        <span>Tools</span>
      </div>
      <div className="trajectory-lanes">
        {lanes.map((lane, laneIndex) => (
          <div className="trajectory-lane" key={laneIndex}>
            {lane.map((cell, bin) => (
              <span
                key={bin}
                className="trajectory-tile"
                data-filled={cell.count > 0 || undefined}
                style={cell.color ? { background: cell.color } : undefined}
                title={cell.count > 0 ? `${cell.count} 条` : undefined}
              />
            ))}
          </div>
        ))}
      </div>
      <span className="trajectory-total" aria-hidden="true">
        {mode === "duration"
          ? totalMs >= 60_000
            ? `${Math.round(totalMs / 60_000)}m`
            : `${Math.round(totalMs / 1000)}s`
          : `${turns.length} 轮`}
      </span>
    </div>
  )
}

/* ---- 事件流行单元 ---- */
const KIND_BADGE: Record<FusedRecord["kind"], string> = {
  user: "USER",
  message: "ASSISTANT",
  tool: "TOOL",
  system: "SYSTEM",
}

function TraceRow({ record }: { record: FusedRecord }) {
  return (
    <div className={`trajectory-row kind-${record.kind}${record.failed ? " failed" : ""}`}>
      <span className={`trajectory-badge badge-${record.kind}`}>{KIND_BADGE[record.kind]}</span>
      <span className={`trajectory-text${record.kind === "tool" ? " mono" : ""}`}>{record.text}</span>
      {record.durationMs !== null && record.durationMs > 0 ? (
        <span className="trajectory-row-time">{(record.durationMs / 1000).toFixed(1)}s</span>
      ) : null}
    </div>
  )
}

export function TraceView({ sessionId }: { sessionId: string }) {
  const [turns, setTurns] = useState<TurnGroup[] | null>(null)
  const [totalMs, setTotalMs] = useState(0)
  const [error, setError] = useState("")
  const [query, setQuery] = useState("")
  const [mode, setMode] = useState<TimelineMode>("duration")
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const reload = useRef(0)

  useEffect(() => {
    const gen = ++reload.current
    setTurns(null)
    setError("")
    Promise.all([
      api<ChatMessage[] | { messages?: ChatMessage[] }>(`/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`),
      api<{ items?: TraceEvent[] }>(`/api/chat/sessions/${encodeURIComponent(sessionId)}/trace`),
    ])
      .then(([msgData, traceData]) => {
        if (gen !== reload.current) return
        // messages 端点返回裸数组；trace 返回 { items }
        const messages = Array.isArray(msgData) ? msgData : (msgData.messages || [])
        const folded = foldRecords(messages, traceData.items || [])
        setTurns(folded.turns)
        setTotalMs(folded.totalMs)
      })
      .catch((e: unknown) => {
        if (gen !== reload.current) return
        setError(String(e instanceof Error ? e.message : e))
      })
  }, [sessionId])

  const filtered = useMemo(() => {
    if (!turns) return null
    if (!query.trim()) return turns
    const q = query.trim().toLowerCase()
    return turns
      .map((turn) => ({ ...turn, records: turn.records.filter((r) => r.text.toLowerCase().includes(q) || (r.tool || "").toLowerCase().includes(q)) }))
      .filter((turn) => turn.records.length > 0)
  }, [turns, query])

  if (error) return <div className="trace-view"><p className="trace-loading">轨迹加载失败：{error}</p></div>
  if (!filtered) return <div className="trace-view"><p className="trace-loading">加载中…</p></div>

  return (
    <div className="trace-view trajectory-view">
      <div className="trajectory-toolbar">
        <div className="trajectory-modes">
          {([["duration", "Duration"], ["turns", "Turns"], ["calls", "Calls"]] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={`trajectory-mode${mode === value ? " active" : ""}`}
              onClick={() => setMode(value)}
            >{label}</button>
          ))}
        </div>
        <input
          className="trajectory-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索"
        />
      </div>
      <TimelineLanes turns={filtered} mode={mode} totalMs={totalMs} />
      <div className="trajectory-ledger">
        {filtered.length === 0 ? <p className="trace-loading">无匹配记录</p> : null}
        {filtered.map((turn) => {
          const isCollapsed = collapsed.has(turn.turn)
          return (
            <div className="trajectory-turn" key={turn.turn}>
              <button
                type="button"
                className="trajectory-turn-header"
                aria-expanded={!isCollapsed}
                onClick={() => {
                  setCollapsed((prev) => {
                    const next = new Set(prev)
                    if (next.has(turn.turn)) next.delete(turn.turn)
                    else next.add(turn.turn)
                    return next
                  })
                }}
              >
                <span className="trajectory-turn-label">Turn {turn.turn}</span>
                <span className="trajectory-turn-meta">{turn.records.length} 条记录</span>
                <span className="trajectory-turn-chevron" aria-hidden="true">{isCollapsed ? "▸" : "▾"}</span>
              </button>
              {!isCollapsed ? turn.records.map((record) => <TraceRow key={record.key} record={record} />) : null}
            </div>
          )
        })}
      </div>
    </div>
  )
}
