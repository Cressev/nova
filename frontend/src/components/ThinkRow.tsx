import { useEffect, useRef, useState } from "react"

/* ============================================================================
   Think 披露行 —— 对齐 dsh ReasoningRow + DisclosureRow（交互级审计）：
   24px 单行：[16px 思考图标] gap6 [标题 Think] [2px 分隔点] [省略摘要]；
   运行态扫光动画 + 摘要跟随末行；点击整行展开/收起，展开体 22px 缩进、
   三级灰文本、pre-wrap。无卡片、无边框、无背景。
   ========================================================================== */

const THINK_ICON = '<path d="M7 1.8a5.2 5.2 0 0 1 5.2 5.2c0 1.9-1 3.1-1.9 4.1-.5.6-.9 1.1-1.1 1.7l-.2.6H5l-.2-.6c-.2-.6-.6-1.1-1.1-1.7C2.8 10.1 1.8 8.9 1.8 7A5.2 5.2 0 0 1 7 1.8Z" stroke="currentColor" stroke-width="1.2" fill="none"/><path d="M5.4 16.4h3.2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>'

function firstLine(text: string): string {
  const i = text.indexOf("\n")
  return i === -1 ? text : text.slice(0, i)
}

function latestLine(text: string): string {
  const visible = text.trimEnd()
  const i = visible.lastIndexOf("\n")
  return i === -1 ? visible : visible.slice(i + 1)
}

export function ThinkRow({ text, running }: { text: string; running: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const summaryRef = useRef<HTMLSpanElement>(null)
  const summary = running ? latestLine(text) : firstLine(text)

  useEffect(() => {
    // 运行态摘要滚动到末行（dsh data-follow-end 语义）
    const el = summaryRef.current
    if (el && running) el.scrollLeft = el.scrollWidth - el.clientWidth
  }, [running, summary])

  return (
    <div className="think-row" data-state={running ? "running" : "ok"}>
      <div
        className="think-disclosure"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setExpanded((v) => !v) } }}
      >
        <span className="think-leading" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" dangerouslySetInnerHTML={{ __html: THINK_ICON }} />
        </span>
        <span className="think-title">Think</span>
        {!expanded ? (
          <>
            <span className="think-sep" aria-hidden="true" />
            <span ref={summaryRef} className="think-summary" data-follow-end={running || undefined}>{summary}</span>
          </>
        ) : null}
        <svg className="think-chevron" width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <path d={expanded ? "M3 5.2 7 9.2 11 5.2" : "M3 8.8 7 4.8 11 8.8"} stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
      {expanded ? <div className="think-body">{text}</div> : null}
    </div>
  )
}
