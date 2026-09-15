import { useEffect, useRef, useState } from "react"
import { api, projectName } from "../lib/api"

interface WorkspaceStatus {
  current_root?: string
  recent_projects?: string[]
  candidates?: string[]
  allowed_roots?: string[]
}

export function Chevron({ size = 16, open = false, className = "" }: { size?: number; open?: boolean; className?: string }) {
  return (
    <svg
      className={`chevron-icon${open ? " open" : ""}${className ? ` ${className}` : ""}`}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <path d="M6 3.5 10.5 8 6 12.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/** 工作区切换面板：当前 + 最近项目 + 允许根下的候选目录。
 *  切换只影响之后新建的会话（dsh 会话级 cwd 语义：已有会话固定在自己的目录）。 */
export function WorkspacePicker({ current, onSwitched }: { current: string; onSwitched: (root: string) => void }) {
  const [status, setStatus] = useState<WorkspaceStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const loadedRef = useRef(false)

  useEffect(() => {
    if (loadedRef.current) return
    loadedRef.current = true
    void api<WorkspaceStatus>("/api/workspaces").then(setStatus).catch(() => setStatus(null))
  }, [])

  const pick = async (path: string) => {
    if (busy || path === current) return
    setBusy(true)
    setError("")
    try {
      const next = await api<WorkspaceStatus>("/api/workspace/select", {
        method: "POST",
        body: JSON.stringify({ path }),
      })
      onSwitched(String(next.current_root || path))
      setStatus(next)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "切换失败")
    } finally {
      setBusy(false)
    }
  }

  const recents = (status?.recent_projects || []).filter((p) => p !== current).slice(0, 6)
  const candidates = (status?.candidates || []).filter((p) => p !== current && !recents.includes(p)).slice(0, 10)

  return (
    <div className="workspace-picker">
      <div className="workspace-picker-section">
        <div className="workspace-picker-label">当前工作区</div>
        <button type="button" className="workspace-item current" disabled>
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M1.8 4.2A1.7 1.7 0 0 1 3.5 2.5h2.6l1.4 1.7h5A1.7 1.7 0 0 1 14.2 6v5.8a1.7 1.7 0 0 1-1.7 1.7H3.5a1.7 1.7 0 0 1-1.7-1.7V4.2Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
          <span className="workspace-item-name">{projectName(current) || current}</span>
          <span className="workspace-item-path">{current}</span>
          <svg className="workspace-item-check" width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8.5 6.3 12 13 4.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
        </button>
      </div>
      {recents.length > 0 ? (
        <div className="workspace-picker-section">
          <div className="workspace-picker-label">最近打开</div>
          {recents.map((path) => (
            <button key={path} type="button" className="workspace-item" disabled={busy} onClick={() => void pick(path)}>
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M1.8 4.2A1.7 1.7 0 0 1 3.5 2.5h2.6l1.4 1.7h5A1.7 1.7 0 0 1 14.2 6v5.8a1.7 1.7 0 0 1-1.7 1.7H3.5a1.7 1.7 0 0 1-1.7-1.7V4.2Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
              <span className="workspace-item-name">{projectName(path) || path}</span>
              <span className="workspace-item-path" title={path}>{path}</span>
            </button>
          ))}
        </div>
      ) : null}
      {candidates.length > 0 ? (
        <div className="workspace-picker-section">
          <div className="workspace-picker-label">允许范围内的项目目录</div>
          {candidates.map((path) => (
            <button key={path} type="button" className="workspace-item" disabled={busy} onClick={() => void pick(path)}>
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M1.8 4.2A1.7 1.7 0 0 1 3.5 2.5h2.6l1.4 1.7h5A1.7 1.7 0 0 1 14.2 6v5.8a1.7 1.7 0 0 1-1.7 1.7H3.5a1.7 1.7 0 0 1-1.7-1.7V4.2Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
              <span className="workspace-item-name">{projectName(path) || path}</span>
              <span className="workspace-item-path" title={path}>{path}</span>
            </button>
          ))}
        </div>
      ) : null}
      {recents.length === 0 && candidates.length === 0 && !status ? (
        <div className="workspace-picker-empty">加载中…</div>
      ) : null}
      {recents.length === 0 && candidates.length === 0 && status ? (
        <div className="workspace-picker-empty">允许范围（NOVA_ALLOWED_WORKSPACE_ROOTS）内没有其它项目目录</div>
      ) : null}
      {error ? <div className="workspace-picker-error">{error}</div> : null}
      <div className="workspace-picker-hint">切换只影响之后新建的会话；已有会话固定在各自创建时的工作目录</div>
    </div>
  )
}
