import { useEffect, useMemo, useRef, useState } from "react"
import type { ReactNode } from "react"
import { Marked } from "marked"
import markedKatex from "marked-katex-extension"

import "katex/dist/katex.min.css"

/**
 * Markdown 渲染（dsh 语义：GFM 全语法 + KaTeX 公式）。
 *
 * marked 负责 GFM（表格/删除线/任务列表/围栏代码/引用/嵌套列表），
 * marked-katex-extension 负责 $...$ / $$...$$ 公式；
 * 原始 HTML 一律转义不执行（模型输出不可信，等价旧管线的受限 HTML 语义）。
 */

function sanitize(raw: string): string {
  // 防护：历史数据漏网的 <tool_call> XML 绝不进入 markdown 管线（后端已清扫，前端双保险）。
  return String(raw || "").replace(/<tool_calls?>[\s\S]*?(?:<\/tool_calls?>|$)/g, "").trim()
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
}

const marked = new Marked({ gfm: true, breaks: true })
marked.use(
  markedKatex({
    throwOnError: false,
    nonStandard: true,
  }),
)
marked.use({
  renderer: {
    // 原始 HTML 块/行内 HTML 一律转义显示，不进入 DOM（防注入）。
    html(token) {
      return escapeHtml((token as { text?: string }).text ?? "")
    },
  },
})

export function renderMarkdownHtml(raw: string): string {
  const text = sanitize(raw)
  try {
    const parsed = marked.parse(text, { async: false })
    return typeof parsed === "string" ? parsed : escapeHtml(text)
  } catch {
    return escapeHtml(text)
  }
}

export function Markdown({ content, className }: { content: string; className?: string }) {
  const html = useMemo(() => renderMarkdownHtml(content), [content])
  return <div className={className} dangerouslySetInnerHTML={{ __html: html }} />
}

/** 复制按钮（⧉→✓ 反馈 1.2s），dsh MessageIconActions 的 copy 位。 */
export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | null>(null)
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current) }, [])
  return (
    <button
      type="button"
      className="message-copy"
      aria-label="复制内容"
      title="复制内容"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setCopied(true)
          if (timer.current) window.clearTimeout(timer.current)
          timer.current = window.setTimeout(() => setCopied(false), 1200)
        } catch {
          /* 剪贴板不可用时静默 */
        }
      }}
    >
      {copied ? "✓" : "⧉"}
    </button>
  )
}

/** 折叠容器：点击头部行切换（dsh disclosure 行为）。 */
export function Disclosure({
  summary,
  children,
  expanded: controlled,
  onToggle,
  className,
}: {
  summary: ReactNode
  children: ReactNode
  expanded?: boolean
  onToggle?: (open: boolean) => void
  className?: string
}) {
  const [innerOpen, setInnerOpen] = useState(false)
  const open = controlled ?? innerOpen
  return (
    <div className={className}>
      <div
        className="tool-row"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => {
          const next = !open
          if (controlled === undefined) setInnerOpen(next)
          onToggle?.(next)
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault()
            const next = !open
            if (controlled === undefined) setInnerOpen(next)
            onToggle?.(next)
          }
        }}
      >
        {summary}
      </div>
      {!open ? null : <div className="tool-body-wrap">{children}</div>}
    </div>
  )
}
