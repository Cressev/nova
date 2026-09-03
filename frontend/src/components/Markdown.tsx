import { useEffect, useMemo, useRef, useState } from "react"
import type { ReactNode } from "react"

/**
 * 轻量 Markdown 渲染（app.js renderMarkdown 的 React 等价物）。
 * 直接复用既有 HTML 管线（工具标签清扫 + 围栏代码 + 列表 + 标题 + 行内标记），
 * 输出经清洗后的受限 HTML，挂到受控容器上。
 */
function sanitize(raw: string): string {
  // 防护：历史数据漏网的 <tool_call> XML 绝不进入 markdown 管线（后端已清扫，前端双保险）。
  return String(raw || "").replace(/<tool_calls?>[\s\S]*?(?:<\/tool_calls?>|$)/g, "").trim()
}

function esc(t: string): string {
  return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
}

function inline(t: string): string {
  return esc(t)
    .replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
}

export function renderMarkdownHtml(raw: string): string {
  const text = sanitize(raw)
  try {
    const lines = text.split("\n")
    const out: string[] = []
    let inCode = false
    let codeBuf: string[] = []
    let listBuf: string[] = []
    const flushList = () => {
      if (listBuf.length) {
        out.push('<ul class="md-list">' + listBuf.map((i) => `<li>${inline(i)}</li>`).join("") + "</ul>")
        listBuf = []
      }
    }
    for (const line of lines) {
      const fence = line.match(/^```(\w*)/)
      if (fence) {
        if (inCode) {
          out.push(`<pre class="md-pre"><code>${esc(codeBuf.join("\n"))}</code></pre>`)
          codeBuf = []
          inCode = false
        } else {
          flushList()
          inCode = true
        }
        continue
      }
      if (inCode) {
        codeBuf.push(line)
        continue
      }
      const heading = line.match(/^(#{1,4})\s+(.*)/)
      if (heading) {
        flushList()
        const level = heading[1].length
        out.push(`<h${level + 2} class="md-h">${inline(heading[2])}</h${level + 2}>`)
        continue
      }
      const li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/)
      if (li) {
        listBuf.push(li[1])
        continue
      }
      if (!line.trim()) {
        flushList()
        continue
      }
      flushList()
      out.push(`<p class="md-p">${inline(line)}</p>`)
    }
    if (inCode && codeBuf.length) {
      out.push(`<pre class="md-pre"><code>${esc(codeBuf.join("\n"))}</code></pre>`)
    }
    flushList()
    return out.join("")
  } catch {
    return esc(text)
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
