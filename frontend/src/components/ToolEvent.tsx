import type { ReactNode } from "react"
import type { ToolCallData, ToolRowState } from "../types"
import { Disclosure } from "./Markdown"

/* ---- dsh ToolRow 行模型（tool-call-model.ts 对齐）----
   变体 → 标题/摘要键/展开块；error 摘要 = 失败首行。 */
const TOOL_ROW_VARIANTS: Record<string, string> = {
  bash: "bash", pwsh: "bash",
  read: "read", read_image: "read", web_fetch: "read", skill: "read",
  web_search: "search", grep: "search", glob: "search", session_search: "search",
  write: "write", edit: "edit",
}
const TOOL_VARIANT_TITLES: Record<string, string> = {
  search: "Search", read: "Read", bash: "Bash", write: "Write", edit: "Edit", others: "Tool call",
}
const TOOL_ROW_TITLES: Record<string, string> = {
  grep: "Grep", glob: "Glob",
  web_search: "Search", web_fetch: "Fetch",
  pwsh: "Pwsh",
  ask_user_question: "Ask",
}
const TOOL_SUMMARY_KEYS: Record<string, string[]> = {
  bash: ["description", "command"],
  read: ["path", "file_path", "url"],
  search: ["query", "pattern", "url"],
  write: ["path", "file_path"],
  edit: ["path", "file_path"],
  others: [],
}

export function classifyToolVariant(tool: string | null | undefined): string {
  return TOOL_ROW_VARIANTS[String(tool || "")] || "others"
}

function firstLine(text: string): string {
  const nl = String(text || "").indexOf("\n")
  return nl === -1 ? String(text || "") : String(text || "").slice(0, nl)
}

export function deriveToolSummary(tool: string | null | undefined, args: Record<string, unknown>, annotation?: string | null): string {
  if (typeof annotation === "string" && annotation !== "") return firstLine(annotation)
  const keys = TOOL_SUMMARY_KEYS[classifyToolVariant(tool)] || []
  for (const key of keys) {
    const value = args[key]
    if (typeof value === "string" && value !== "") return firstLine(value)
  }
  for (const value of Object.values(args)) {
    if (typeof value === "string" && value !== "") return firstLine(value)
  }
  return ""
}

/* ---- DSH ToolRow 图标（从 dsh GUI 实测提取的 SVG 路径） ---- */
const TERMINAL_ICON = `<path transform="translate(0.6689 1.073)" d="M11.4818 5.57813C11.4818 4.45301 11.4807 3.66237 11.4075 3.05908C11.3359 2.46953 11.2024 2.13852 10.9939 1.89441C10.9247 1.81341 10.8493 1.73801 10.7683 1.66882C10.5242 1.46033 10.1932 1.32686 9.60364 1.25525C9.00034 1.18198 8.20974 1.18091 7.0846 1.18091L5.57813 1.18091C4.45301 1.18091 3.66238 1.18198 3.05908 1.25525C2.46953 1.32686 2.13852 1.46033 1.89441 1.66882C1.81341 1.73801 1.73801 1.81341 1.66882 1.89441C1.46033 2.13852 1.32686 2.46953 1.25525 3.05908C1.18198 3.66238 1.18091 4.45301 1.18091 5.57813L1.18091 6.2771C1.18091 7.40218 1.18197 8.19288 1.25525 8.79614C1.32687 9.38553 1.46036 9.71674 1.66882 9.96082C1.73797 10.0417 1.81347 10.1173 1.89441 10.1864C2.13851 10.3948 2.46965 10.5275 3.05908 10.5991C3.66238 10.6724 4.45298 10.6735 5.57813 10.6735L7.0846 10.6735C8.20977 10.6735 9.00033 10.6724 9.60364 10.5991C10.1931 10.5275 10.5242 10.3948 10.7683 10.1864C10.8493 10.1173 10.9247 10.0417 10.9939 9.96082C11.2024 9.71674 11.3358 9.38553 11.4075 8.79614C11.4808 8.19288 11.4818 7.40218 11.4818 6.2771L11.4818 5.57813Z" fill="currentColor"/>`
const DOC_ICON = '<rect x="2.2" y="1.2" width="9.6" height="11.6" rx="2" stroke="currentColor" stroke-width="1.2" fill="none"/><path d="M4.8 5.2h4.4M4.8 7.4h4.4M4.8 9.6h2.8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" fill="none"/>'
const CHEVRON_ICON = `<path d="M11.8486 5.5L11.4238 5.92383L8.69727 8.65137C8.44157 8.90706 8.21562 9.13382 8.01172 9.29785C7.79912 9.46883 7.55595 9.61756 7.25 9.66602C7.08435 9.69222 6.91565 9.69222 6.75 9.66602C6.44405 9.61756 6.20088 9.46883 5.98828 9.29785C5.78438 9.13382 5.55843 8.90706 5.30273 8.65137L2.57617 5.92383L2.15137 5.5L3 4.65137L3.42383 5.07617L6.15137 7.80273C6.42595 8.07732 6.59876 8.24849 6.74023 8.3623C6.87291 8.46904 6.92272 8.47813 6.9375 8.48047C6.97895 8.48703 7.02105 8.48703 7.0625 8.48047C7.07728 8.47813 7.12709 8.46904 7.25977 8.3623C7.40124 8.24849 7.57405 8.07732 7.84863 7.80273L10.5762 5.07617L11 4.65137L11.8486 5.5Z" fill="currentColor"/>`

function ToolLeadingIcon({ tool }: { tool: string | null | undefined }) {
  const name = String(tool || "").toLowerCase()
  const isDoc = /read|write|list|glob|file|edit|search|grep/.test(name)
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true" dangerouslySetInnerHTML={{ __html: isDoc ? DOC_ICON : TERMINAL_ICON }} />
  )
}

export function ToolRowHead({ tool, summary, state }: { tool: string; summary: string; state: ToolRowState }) {
  const variant = classifyToolVariant(tool)
  const label = TOOL_ROW_TITLES[tool] || TOOL_VARIANT_TITLES[variant]
  return (
    <>
      <span className="tool-leading" aria-hidden="true"><ToolLeadingIcon tool={tool} /></span>
      <span className="tool-title">{label}</span>
      <span className="tool-sep" aria-hidden="true" />
      <span className={`tool-summary${state === "error" ? " tool-summary-error" : ""}`}>{summary}</span>
      <svg className="tool-chevron" width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true" dangerouslySetInnerHTML={{ __html: CHEVRON_ICON }} />
    </>
  )
}

/* ---- 展开体：按变体路由（Terminal/Diff/Read/Search/Web/IN-OUT） ---- */
function TerminalBody({ command, output }: { command: string; output: string }) {
  return (
    <div className="terminal-block" data-terminal="1">
      <div className="terminal-banner"><span className="terminal-dollar" aria-hidden="true">$</span><code>{command}</code></div>
      <div className="terminal-output"><pre>{output.trim() ? output : "(no output)"}</pre></div>
    </div>
  )
}

function DiffBody({ diff, fallback }: { diff: ToolCallData["diff"]; fallback: ReactNode }) {
  if (!diff || !Array.isArray(diff.files) || diff.files.length === 0) return <>{fallback}</>
  const stat = `+${diff.additions ?? 0} −${diff.deletions ?? 0}`
  return (
    <div className="diff-block">
      <div className="diff-stat"><code>{stat}</code><span>{diff.files.join(", ")}</span></div>
      {typeof diff.preview === "string" && diff.preview ? <pre className="diff-preview">{diff.preview}</pre> : null}
    </div>
  )
}

function ReadBody({ output }: { output: string }) {
  return <div className="read-block"><pre>{output.trim() ? output : "(no output)"}</pre></div>
}

function WebBody({ output, data }: { output: string; data: ToolCallData }) {
  const sources = data.sources || data.results || []
  return (
    <div className="web-block">
      {sources.length > 0 && (
        <div className="web-sources">
          {sources.slice(0, 8).map((s, i) => (
            <a key={i} href={s.url || "#"} target="_blank" rel="noopener">{s.title || s.url || `来源 ${i + 1}`}</a>
          ))}
        </div>
      )}
      <pre>{output.trim() ? output : "(no output)"}</pre>
    </div>
  )
}

function IoBody({ args, output }: { args: Record<string, unknown>; output: string }) {
  return (
    <div className="io-body">
      <details><summary>请求参数</summary><pre>{JSON.stringify(args, null, 2)}</pre></details>
      <div className="io-output"><pre>{output.trim() ? output : "(no output)"}</pre></div>
    </div>
  )
}

export function ToolBody({ tool, args, output, data, state }: {
  tool: string
  args: Record<string, unknown>
  output: string
  data: ToolCallData
  state: ToolRowState
}) {
  const variant = classifyToolVariant(tool)
  const name = String(tool || "")
  if (name === "bash" || name === "pwsh") return <TerminalBody command={String(args.command || "")} output={output} />
  if (variant === "write" || variant === "edit") return <DiffBody diff={data.diff} fallback={<IoBody args={args} output={output} />} />
  if (variant === "read") return <ReadBody output={output} />
  if (name === "web_fetch" || name === "web_search") return <WebBody output={output} data={data} />
  return <IoBody args={args} output={output} />
}

/** hook 上下文渲染（dsh HookContexts 行）。 */
export function HookContexts({ contexts }: { contexts: ToolCallData["hook_contexts"] }) {
  if (!contexts || contexts.length === 0) return null
  return (
    <div className="hook-contexts">
      {contexts.map((c, i) => (
        <div key={i} className="hook-context-row">
          <code>{c.source}/{c.event}</code>
          {c.decision ? <em>{c.decision}{c.reason ? `：${c.reason}` : ""}</em> : null}
        </div>
      ))}
    </div>
  )
}

export interface ToolEventView {
  callId: string
  tool: string
  args: Record<string, unknown>
  output: string
  data: ToolCallData
  state: ToolRowState
}

/** 工具事件行（可展开 disclosure + 取消/重试动作）。 */
export function ToolEventRow({ view, onCancel, onRetry }: {
  view: ToolEventView
  onCancel?: (callId: string) => void
  onRetry?: (view: ToolEventView) => void
}) {
  const failureLine = view.state === "error" && view.data.failure_reason ? firstLine(view.data.failure_reason) : ""
  const summary = failureLine || deriveToolSummary(view.tool, view.args, view.data.annotation)
  const retryable = view.state === "error" && view.data.retryable
  return (
    <Disclosure className={`tool-event ${view.state}`} summary={<ToolRowHead tool={view.tool} summary={summary} state={view.state} />}>
      <ToolBody tool={view.tool} args={view.args} output={view.output} data={view.data} state={view.state} />
      <HookContexts contexts={view.data.hook_contexts} />
      {view.state === "running" && onCancel ? (
        <div className="tool-running-actions"><button type="button" onClick={() => onCancel(view.callId)}>取消执行</button></div>
      ) : null}
      {retryable && onRetry ? (
        <div className="tool-running-actions"><button type="button" onClick={() => onRetry(view)}>重试</button></div>
      ) : null}
    </Disclosure>
  )
}
