/* 划词评论问答（旁路线程）——前端交互层。
 *
 * 交互流（26/09/15 设计定稿）：
 *  1. 在 assistant 消息上划词 → 选区上方浮条（解释/提问/复制）
 *  2. 解释：一键立即建线程，AI 流式作答
 *  3. 提问：浮条下方输入条 → 右侧评论栏线程（引用块+问题+流式回答）
 *  4. 线程底部输入框可继续追问
 *  5. 被评论文字常驻高亮；点高亮↔点引用块双向定位
 *
 * 锚点：W3C TextQuoteSelector（quote+prefix+suffix+occurrence），
 * 渲染时在消息 markdown 容器里做字符串匹配重定位高亮。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { api } from "../lib/api"

export interface CommentEntryData {
  id: string
  anchor_id: string
  role: "user" | "assistant"
  content: string
  created_at: string
}

export interface CommentAnchorData {
  id: string
  session_id: string
  message_id: string
  quote: string
  prefix: string
  suffix: string
  occurrence: number
  created_at: string
}

export interface CommentThread {
  anchor: CommentAnchorData
  entries: CommentEntryData[]
}

/* ---- TextQuoteSelector 定位 ---- */

/** 在容器纯文本里按 quote+前后文找回 [start,end)；失配返回 null。 */
export function locateQuote(
  container: HTMLElement,
  quote: string,
  prefix: string,
  suffix: string,
  occurrence: number,
): [number, number] | null {
  const text = container.textContent || ""
  let matches: number[] = []
  let from = 0
  for (;;) {
    const idx = text.indexOf(quote, from)
    if (idx === -1) break
    matches.push(idx)
    from = idx + 1
  }
  if (matches.length === 0) return null
  let start: number
  if (occurrence < matches.length) {
    start = matches[occurrence]
  } else {
    // occurrence 越界（消息被编辑过）：用前后文挑最优匹配
    let best = matches[0]
    let bestScore = -1
    for (const m of matches) {
      const pre = text.slice(Math.max(0, m - prefix.length), m)
      const post = text.slice(m + quote.length, m + quote.length + suffix.length)
      let score = 0
      for (let i = 0; i < Math.min(pre.length, prefix.length); i++) {
        if (pre[pre.length - 1 - i] === prefix[prefix.length - 1 - i]) score++
      }
      for (let i = 0; i < Math.min(post.length, suffix.length); i++) {
        if (post[i] === suffix[i]) score++
      }
      if (score > bestScore) { bestScore = score; best = m }
    }
    start = best
  }
  return [start, start + quote.length]
}

/** 把容器内的字符区间 [start,end) 转成 DOM Range（跨文本节点）。 */
function charRangeToDomRange(container: HTMLElement, start: number, end: number): Range | null {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  let offset = 0
  let startNode: Text | null = null
  let startOffset = 0
  let endNode: Text | null = null
  let endOffset = 0
  let node: Text | null
  while ((node = walker.nextNode() as Text | null)) {
    const len = node.data.length
    if (startNode === null && offset + len >= start) {
      startNode = node
      startOffset = start - offset
    }
    if (offset + len >= end) {
      endNode = node
      endOffset = end - offset
      break
    }
    offset += len
  }
  if (!startNode || !endNode) return null
  const range = document.createRange()
  try {
    range.setStart(startNode, Math.max(0, Math.min(startOffset, startNode.data.length)))
    range.setEnd(endNode, Math.max(0, Math.min(endOffset, endNode.data.length)))
  } catch {
    return null
  }
  return range
}

/** 用 CSS 高亮（mark 元素包裹）渲染一个锚点；返回清理函数。 */
function highlightRange(container: HTMLElement, range: Range, color: string, onClick?: () => void): () => void {
  const marks: HTMLElement[] = []
  try {
    const fragments = range.extractContents()
    const frag = document.createDocumentFragment()
    fragments.childNodes.forEach((child) => {
      const mark = document.createElement("mark")
      mark.className = "inline-comment-highlight"
      mark.style.background = color
      if (onClick) mark.addEventListener("click", onClick)
      mark.appendChild(child)
      frag.appendChild(mark)
      marks.push(mark)
    })
    range.insertNode(frag)
  } catch {
    return () => {}
  }
  return () => {
    for (const mark of marks) {
      const parent = mark.parentNode
      if (!parent) continue
      while (mark.firstChild) parent.insertBefore(mark.firstChild, mark)
      parent.removeChild(mark)
      parent.normalize()
    }
  }
}

/* ---- 划词浮条 ---- */

interface SelectionToolbarState {
  rect: { top: number; left: number }
  quote: string
  prefix: string
  suffix: string
  occurrence: number
  messageEl: HTMLElement
  messageId: string
}

export function SelectionToolbar({
  state,
  onExplain,
  onAsk,
  onClose,
  asking,
}: {
  state: SelectionToolbarState
  onExplain: () => void
  onAsk: () => void
  onClose: () => void
  asking: boolean
}) {
  const [draft, setDraft] = useState("")
  const inputRef = useRef<HTMLInputElement | null>(null)
  useEffect(() => { if (asking) inputRef.current?.focus() }, [asking])
  if (!asking) {
    return (
      <div className="inline-comment-toolbar" style={{ top: state.rect.top, left: state.rect.left }} onMouseDown={(e) => e.preventDefault()}>
        <button type="button" onClick={onExplain}>解释</button>
        <button type="button" className="sep" onClick={onAsk}>提问</button>
        <button type="button" className="sep" onClick={() => { void navigator.clipboard?.writeText(state.quote); onClose() }}>复制</button>
      </div>
    )
  }
  return (
    <div className="inline-comment-toolbar asking" style={{ top: state.rect.top, left: state.rect.left }} onMouseDown={(e) => e.preventDefault()}>
      <input
        ref={inputRef}
        value={draft}
        placeholder="问点什么…"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && draft.trim()) { onAsk(); setDraft("") }
          if (e.key === "Escape") { onClose(); setDraft("") }
        }}
        data-inline-comment-draft={draft}
      />
      <button type="button" className="send" onClick={() => { if (draft.trim()) { onAsk(); setDraft("") } }}>↑</button>
    </div>
  )
}

/* 工具条输入的问题通过 DOM data 属性传递（受控 state 在 unmount 时丢失） */
export function readToolbarDraft(): string {
  const el = document.querySelector<HTMLInputElement>("[data-inline-comment-draft]")
  return el?.value.trim() || ""
}

/* ---- 右侧评论栏（飞书文档评论形态） ---- */

function ThreadView({ thread, streamingState, busy, onAsk, onDelete, onFocusAnchor }: {
  thread: CommentThread
  streamingState: { text: string; reasoning: string } | undefined
  busy: boolean
  onAsk: (anchorId: string, question: string) => void
  onDelete: (anchorId: string) => void
  onFocusAnchor: (anchorId: string) => void
}) {
  const [draft, setDraft] = useState("")
  const bodyRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight })
  }, [thread.entries.length, streamingState?.text, streamingState?.reasoning])
  return (
    <section className="inline-comment-thread" data-anchor-id={thread.anchor.id}>
      <header className="inline-comment-thread-head">
        <button type="button" className="inline-comment-quote" onClick={() => onFocusAnchor(thread.anchor.id)} title="定位到原文">
          「{thread.anchor.quote.slice(0, 80)}{thread.anchor.quote.length > 80 ? "…" : ""}」
        </button>
        <button type="button" className="inline-comment-thread-delete" aria-label="删除线程" title="删除线程" onClick={() => onDelete(thread.anchor.id)}>×</button>
      </header>
      <div className="inline-comment-thread-body" ref={bodyRef}>
        {thread.entries.map((entry) => (
          <div key={entry.id} className={`inline-comment-bubble ${entry.role}`}>
            <div className="inline-comment-role">{entry.role === "user" ? "我" : "Nova"}</div>
            <div className="inline-comment-text">{entry.content}</div>
          </div>
        ))}
        {streamingState !== undefined ? (
          <div className="inline-comment-bubble assistant streaming">
            <div className="inline-comment-role">Nova</div>
            <div className="inline-comment-text">
              {streamingState.reasoning ? (
                <div className="inline-comment-reasoning">{streamingState.reasoning}</div>
              ) : null}
              {streamingState.text === "" && !streamingState.reasoning ? (
                <span className="inline-comment-loading">思考中…</span>
              ) : streamingState.text}
            </div>
          </div>
        ) : null}
        {busy && streamingState === undefined ? <div className="inline-comment-loading">思考中…</div> : null}
      </div>
      <div className="inline-comment-ask">
        <input
          value={draft}
          placeholder="继续追问…"
          disabled={busy}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && draft.trim() && !busy) {
              onAsk(thread.anchor.id, draft.trim())
              setDraft("")
            }
          }}
        />
        <button
          type="button"
          className="inline-comment-ask-send"
          aria-label="发送"
          title="发送"
          disabled={busy || !draft.trim()}
          onClick={() => {
            if (draft.trim() && !busy) {
              onAsk(thread.anchor.id, draft.trim())
              setDraft("")
            }
          }}
        ><svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 12.5V3.5M3.8 7.7L8 3.5l4.2 4.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg></button>
      </div>
    </section>
  )
}

export function CommentPanel({ open, threads, activeAnchorId, streamingMap, busyAnchors, onClose, onAsk, onDelete, onFocusAnchor }: {
  open: boolean
  threads: CommentThread[]
  activeAnchorId: string | null
  streamingMap: Map<string, { text: string; reasoning: string }>
  busyAnchors: Set<string>
  onClose: () => void
  onAsk: (anchorId: string, question: string) => void
  onDelete: (anchorId: string) => void
  onFocusAnchor: (anchorId: string) => void
}) {
  if (!open) return null
  return (
    <aside className="inline-comment-panel" id="inline-comment-panel">
      <header className="inline-comment-panel-head">
        <span>评论</span>
        <button type="button" aria-label="收起评论栏" title="收起评论栏" onClick={onClose}>×</button>
      </header>
      <div className="inline-comment-panel-body">
        {threads.length === 0 ? (
          <div className="inline-comment-empty">选中回复中的文字，点“解释”或“提问”开始旁路问答</div>
        ) : (
          threads
            .map((thread) => (
              <ThreadView
                key={thread.anchor.id}
                thread={thread}
                streamingState={streamingMap.get(thread.anchor.id)}
                busy={busyAnchors.has(thread.anchor.id)}
                onAsk={onAsk}
                onDelete={onDelete}
                onFocusAnchor={onFocusAnchor}
            />
            ))
        )}
      </div>
    </aside>
  )
}

/* ---- 总控 hook：划词检测 + API + 流式 + 高亮管理 ---- */

export function useInlineComments(sessionId: string | null, version: number) {
  const [threads, setThreads] = useState<CommentThread[]>([])
  const [panelOpen, setPanelOpen] = useState(false)
  const [activeAnchorId, setActiveAnchorId] = useState<string | null>(null)
  const [toolbar, setToolbar] = useState<SelectionToolbarState | null>(null)
  const [asking, setAsking] = useState(false)
  const [streamingMap, setStreamingMap] = useState<Map<string, { text: string; reasoning: string }>>(new Map())
  const [busyAnchors, setBusyAnchors] = useState<Set<string>>(new Set())
  const cleanupFns = useRef<Array<() => void>>([])
  const lastVersion = useRef(version)

  const refreshThreads = useCallback(async (sid: string) => {
    try {
      const data = await api<{ threads: CommentThread[] }>(`/api/chat/sessions/${encodeURIComponent(sid)}/comments`)
      setThreads(data.threads || [])
    } catch { /* 静默：恢复失败不阻塞聊天 */ }
  }, [])

  // 会话切换/新消息 → 重拉线程 + 重渲染高亮
  useEffect(() => {
    cleanupFns.current.forEach((fn) => fn())
    cleanupFns.current = []
    if (!sessionId) { setThreads([]); setPanelOpen(false); return }
    void refreshThreads(sessionId)
  }, [sessionId, refreshThreads])

  // threads 变化 → 渲染高亮（等 DOM 稳定后执行）
  useEffect(() => {
    if (lastVersion.current !== version) { lastVersion.current = version }
    cleanupFns.current.forEach((fn) => fn())
    cleanupFns.current = []
    if (!sessionId) return
    const timer = window.setTimeout(() => {
      for (const thread of threads) {
        const messageEl = document.querySelector<HTMLElement>(`[data-message-id="${thread.anchor.message_id}"] .message-content`)
        if (!messageEl) continue
        const located = locateQuote(messageEl, thread.anchor.quote, thread.anchor.prefix, thread.anchor.suffix, thread.anchor.occurrence)
        if (!located) continue
        const range = charRangeToDomRange(messageEl, located[0], located[1])
        if (!range) continue
        const anchorId = thread.anchor.id
        const cleanup = highlightRange(messageEl, range, "rgba(250, 204, 21, 0.35)", () => {
          setActiveAnchorId(anchorId)
          setPanelOpen(true)
        })
        cleanupFns.current.push(cleanup)
      }
    }, 60)
    return () => window.clearTimeout(timer)
  }, [threads, sessionId, version])

  // 划词检测（mouseup 时检查选区是否落在 assistant 消息内）
  useEffect(() => {
    const onMouseUp = (e: MouseEvent) => {
      if ((e.target as HTMLElement)?.closest?.(".inline-comment-toolbar, .inline-comment-panel")) return
      window.setTimeout(() => {
        const selection = window.getSelection()
        if (!selection || selection.isCollapsed) { setToolbar(null); setAsking(false); return }
        const text = selection.toString().trim()
        if (!text || text.length > 2000) { setToolbar(null); return }
        const range = selection.getRangeAt(0)
        const containerEl = range.commonAncestorContainer instanceof HTMLElement ? range.commonAncestorContainer : (range.commonAncestorContainer.parentElement as HTMLElement | null)
        const messageEl = containerEl?.closest?.(".message.assistant") as HTMLElement | null
        const contentEl = containerEl?.closest?.(".message-content") as HTMLElement | null
        const article = containerEl?.closest?.("[data-message-id]") as HTMLElement | null
        if (!messageEl || !contentEl || !article) { setToolbar(null); return }
        const messageId = article.getAttribute("data-message-id") || ""
        if (!messageId) { setToolbar(null); return }
        const rect = range.getBoundingClientRect()
        // prefix/suffix：消息全文相对选区前后各取 32 字
        const fullText = contentEl.textContent || ""
        const quoteIndex = fullText.indexOf(text)
        const occurrence = quoteIndex >= 0 ? 0 : 0
        const start = fullText.indexOf(text)
        const prefix = start > 0 ? fullText.slice(Math.max(0, start - 32), start) : ""
        const suffix = fullText.slice(start + text.length, start + text.length + 32)
        setToolbar({
          // 选区下方居中（避开 macOS/输入法的系统选中菜单，它们通常在选区上方）
          rect: { top: rect.bottom, left: rect.left + rect.width / 2 },
          quote: text,
          prefix,
          suffix,
          occurrence,
          messageEl: contentEl,
          messageId,
        })
      }, 10)
    }
    document.addEventListener("mouseup", onMouseUp)
    return () => document.removeEventListener("mouseup", onMouseUp)
  }, [])

  const askStream = useCallback(async (sid: string, body: Record<string, unknown>) => {
    let anchorId: string = typeof body.anchor_id === "string" ? body.anchor_id : ""
    const question = typeof body.question === "string" ? body.question : ""
    const parts: string[] = []
    const reasoningParts: string[] = []
    const markBusy = (aid: string, on: boolean) => {
      setBusyAnchors((cur) => {
        const next = new Set(cur)
        if (on) next.add(aid); else next.delete(aid)
        return next
      })
    }
    const setStream = (aid: string, patch: Partial<{ text: string; reasoning: string }>) => {
      setStreamingMap((cur) => {
        const next = new Map(cur)
        const prev = next.get(aid) || { text: "", reasoning: "" }
        next.set(aid, { ...prev, ...patch })
        return next
      })
    }
    const clearStream = (aid: string) => {
      setStreamingMap((cur) => {
        const next = new Map(cur)
        next.delete(aid)
        return next
      })
    }
    markBusy(anchorId, true)
    // 乐观插入：发送瞬间本地可见用户问题（不等 refreshThreads）
    if (question) {
      setThreads((ts) => {
        if (anchorId) {
          return ts.map((t) => t.anchor.id === anchorId
            ? { ...t, entries: [...t.entries, { id: `local_${Date.now()}`, anchor_id: anchorId, role: "user" as const, content: question, created_at: new Date().toISOString() }] }
            : t)
        }
        return ts
      })
    }
    try {
      const resp = await fetch(`/api/chat/sessions/${encodeURIComponent(sid)}/comments/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n")
        buffer = lines.pop() || ""
        for (const line of lines) {
          if (!line.trim()) continue
          try {
            const evt = JSON.parse(line)
            if (evt.type === "thread_started") {
              anchorId = String(evt.anchor?.id || "")
              const newAnchor = evt.anchor
              setThreads((ts) => {
                if (ts.some((t) => t.anchor.id === anchorId)) return ts
                const thread: CommentThread = {
                  anchor: newAnchor,
                  entries: question
                    ? [{ id: `local_${Date.now()}`, anchor_id: anchorId, role: "user" as const, content: question, created_at: new Date().toISOString() }]
                    : [],
                }
                return [...ts, thread]
              })
              setStream(String(anchorId), { text: "", reasoning: "" })
            } else if (evt.type === "reasoning_delta") {
              reasoningParts.push(evt.text || "")
              if (anchorId) setStream(anchorId, { reasoning: reasoningParts.join("") })
            } else if (evt.type === "delta") {
              parts.push(evt.text || "")
              if (anchorId) setStream(anchorId, { text: parts.join("") })
            } else if (evt.type === "done") {
              if (anchorId) setStream(anchorId, { text: evt.entry?.content || parts.join("") })
            } else if (evt.type === "error") {
              if (anchorId) setStream(anchorId, { text: `出错了：${evt.message}` })
            }
          } catch { /* 忽略坏行 */ }
        }
      }
    } catch (exc) {
      if (anchorId) setStream(anchorId, { text: `请求失败：${String(exc)}` })
    } finally {
      markBusy(anchorId, false)
      if (sid) void refreshThreads(sid)
      if (anchorId) window.setTimeout(() => clearStream(anchorId), 150)
    }
  }, [refreshThreads])

  const explain = useCallback(() => {
    if (!sessionId || !toolbar) return
    setPanelOpen(true)
    const body = {
      message_id: toolbar.messageId,
      quote: toolbar.quote,
      prefix: toolbar.prefix,
      suffix: toolbar.suffix,
      occurrence: toolbar.occurrence,
      question: "解释这段内容",
    }
    setToolbar(null)
    void askStream(sessionId, body)
  }, [sessionId, toolbar, askStream])

  const ask = useCallback(() => {
    if (!sessionId || !toolbar) return
    setPanelOpen(true)
    const question = readToolbarDraft()
    if (!question) { setAsking(true); return }
    const body = {
      message_id: toolbar.messageId,
      quote: toolbar.quote,
      prefix: toolbar.prefix,
      suffix: toolbar.suffix,
      occurrence: toolbar.occurrence,
      question,
    }
    setToolbar(null)
    setAsking(false)
    void askStream(sessionId, body)
  }, [sessionId, toolbar, askStream])

  const followUp = useCallback((anchorId: string, question: string) => {
    if (!sessionId) return
    void askStream(sessionId, { anchor_id: anchorId, question })
  }, [sessionId, askStream])

  const removeThread = useCallback(async (anchorId: string) => {
    if (!sessionId) return
    try {
      await api(`/api/chat/sessions/${encodeURIComponent(sessionId)}/anchors/${encodeURIComponent(anchorId)}`, { method: "DELETE" })
      setThreads((ts) => ts.filter((t) => t.anchor.id !== anchorId))
    } catch { /* 删除失败保留线程 */ }
  }, [sessionId])

  const focusAnchor = useCallback((anchorId: string) => {
    setActiveAnchorId(anchorId)
    setPanelOpen(true)
    const thread = threads.find((t) => t.anchor.id === anchorId)
    if (!thread) return
    const messageEl = document.querySelector<HTMLElement>(`[data-message-id="${thread.anchor.message_id}"]`)
    messageEl?.scrollIntoView({ block: "center", behavior: "smooth" })
  }, [threads])

  const closeToolbar = useCallback(() => {
    setToolbar(null)
    setAsking(false)
  }, [])

  return {
    threads, panelOpen, setPanelOpen, activeAnchorId, setActiveAnchorId,
    toolbar, asking, setAsking, setToolbarClosed: closeToolbar, streamingMap, busyAnchors,
    explain, ask, followUp, removeThread, focusAnchor,
  }
}
