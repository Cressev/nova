import { useEffect, useRef, useState } from "react"
import { api, projectName } from "../lib/api"

interface PathStatus {
  path: string
  exists: boolean
  is_dir: boolean
  parent_exists: boolean
  can_select: boolean
  can_create: boolean
  reason: string
}

interface WorkspaceStatus {
  current_root?: string
  recent_projects?: string[]
  candidates?: string[]
  allowed_roots?: string[]
  completion?: { value: string; is_final: boolean; reason: string }
  query_status?: PathStatus
}

const FolderIcon = ({ active = false }: { active?: boolean }) => (
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M1.8 4.2A1.7 1.7 0 0 1 3.5 2.5h2.6l1.4 1.7h5A1.7 1.7 0 0 1 14.2 6v5.8a1.7 1.7 0 0 1-1.7 1.7H3.5a1.7 1.7 0 0 1-1.7-1.7V4.2Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
)

function WorkspaceRow({ path, current, busy, onPick }: { path: string; current: boolean; busy: boolean; onPick: (path: string) => void }) {
  return (
    <button
      type="button"
      className={`workspace-row${current ? " current" : ""}`}
      disabled={busy || current}
      onClick={() => onPick(path)}
      title={path}
    >
      <span className="workspace-row-icon"><FolderIcon active={current} /></span>
      <span className="workspace-row-text">
        <span className="workspace-row-name">{projectName(path) || path}</span>
        <span className="workspace-row-path">{path}</span>
      </span>
      {current ? (
        <svg className="workspace-row-check" width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8.5 6.3 12 13 4.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
      ) : (
        <svg className="workspace-row-arrow" width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>
      )}
    </button>
  )
}

/** 工作区切换/新建面板。
 *  切换只影响之后新建的会话（dsh 会话级 cwd 语义）；
 *  新建目录走 /api/workspace/folders（校验 allowed roots + 权限模式）。 */
export function WorkspacePicker({ current, onSwitched }: { current: string; onSwitched: (root: string) => void }) {
  const [status, setStatus] = useState<WorkspaceStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [draft, setDraft] = useState("")
  const [draftStatus, setDraftStatus] = useState<PathStatus | null>(null)
  const [draftCandidates, setDraftCandidates] = useState<string[]>([])
  const debounceRef = useRef<number | null>(null)
  const draftSeq = useRef(0)

  useEffect(() => {
    void api<WorkspaceStatus>("/api/workspaces").then(setStatus).catch(() => setStatus(null))
    return () => { if (debounceRef.current) window.clearTimeout(debounceRef.current) }
  }, [])

  // 路径输入实时校验（250ms 防抖，取 status(q) 的 query_status + 候选）
  useEffect(() => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current)
    const text = draft.trim()
    if (!text) { setDraftStatus(null); setDraftCandidates([]); return }
    const seq = ++draftSeq.current
    debounceRef.current = window.setTimeout(() => {
      void api<WorkspaceStatus>(`/api/workspaces?q=${encodeURIComponent(text)}`).then((next) => {
        if (seq !== draftSeq.current) return
        setDraftStatus(next.query_status || null)
        setDraftCandidates((next.candidates || []).filter((p) => p !== current).slice(0, 6))
      }).catch(() => { /* 校验失败不打断输入 */ })
    }, 250)
  }, [draft, current])

  const refresh = async (q?: string) => {
    const next = await api<WorkspaceStatus>(q ? `/api/workspaces?q=${encodeURIComponent(q)}` : "/api/workspaces")
    setStatus(next)
    return next
  }

  const pick = async (path: string) => {
    if (busy || path === current) return
    setBusy(true)
    setError("")
    try {
      const next = await api<WorkspaceStatus>("/api/workspace/select", { method: "POST", body: JSON.stringify({ path }) })
      setStatus(next)
      onSwitched(String(next.current_root || path))
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "切换失败")
    } finally {
      setBusy(false)
    }
  }

  // 智能主操作：目录已存在且合法 → 切换；父目录存在且在允许范围 → 新建并切换
  const applyDraft = async () => {
    const text = draft.trim()
    if (!text || busy || !draftStatus) return
    if (!draftStatus.can_select && !draftStatus.can_create) return
    setBusy(true)
    setError("")
    try {
      const path = draftStatus.can_select ? draftStatus.path : text
      const next = await api<WorkspaceStatus>(
        draftStatus.can_select ? "/api/workspace/select" : "/api/workspace/folders",
        { method: "POST", body: JSON.stringify({ path }) },
      )
      setStatus(next)
      setDraft("")
      setDraftStatus(null)
      setDraftCandidates([])
      onSwitched(String(next.current_root || path))
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "操作失败")
      try { await refresh() } catch { /* 忽略 */ }
    } finally {
      setBusy(false)
    }
  }

  const recents = (status?.recent_projects || []).filter((p) => p !== current).slice(0, 5)
  const candidates = (status?.candidates || []).filter((p) => p !== current && !recents.includes(p)).slice(0, 8)
  const actionable = draftStatus ? (draftStatus.can_select || draftStatus.can_create) : false
  const actionLabel = draftStatus?.can_select ? "切换到此目录" : draftStatus?.can_create ? "新建目录并切换" : "确认"
  const statusTone = !draftStatus ? "" : draftStatus.can_select || draftStatus.can_create ? "ok" : "bad"

  return (
    <div className="workspace-picker">
      <div className="workspace-picker-scroll">
        <div className="workspace-picker-label">当前工作区</div>
        {current ? <WorkspaceRow path={current} current busy={busy} onPick={pick} /> : null}

        {recents.length > 0 ? (
          <>
            <div className="workspace-picker-label">最近打开</div>
            {recents.map((path) => <WorkspaceRow key={path} path={path} current={false} busy={busy} onPick={pick} />)}
          </>
        ) : null}

        {candidates.length > 0 ? (
          <>
            <div className="workspace-picker-label">允许范围内的项目</div>
            {candidates.map((path) => <WorkspaceRow key={path} path={path} current={false} busy={busy} onPick={pick} />)}
          </>
        ) : null}

        <div className="workspace-picker-label">新建或按路径切换</div>
        <div className="workspace-create">
          <input
            className="workspace-create-input"
            value={draft}
            placeholder="输入目录路径，如 ~/Code/新项目"
            spellCheck={false}
            disabled={busy}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void applyDraft() } }}
          />
          <button
            type="button"
            className="workspace-create-button"
            disabled={busy || !actionable}
            onClick={() => void applyDraft()}
          >{busy ? "处理中…" : actionLabel}</button>
        </div>
        {draft.trim() && draftStatus ? (
          <div className={`workspace-create-status ${statusTone}`}>{draftStatus.reason}</div>
        ) : null}
        {draftCandidates.length > 0 && draft.trim() ? (
          <div className="workspace-create-candidates">
            {draftCandidates.map((p) => (
              <button key={p} type="button" title={p} onClick={() => setDraft(p)}>{p}</button>
            ))}
          </div>
        ) : null}
        {error ? <div className="workspace-picker-error">{error}</div> : null}
      </div>
      <div className="workspace-picker-hint">只影响之后新建的会话；已有会话保持原工作目录</div>
    </div>
  )
}
