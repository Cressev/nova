import { useState } from "react"
import type { PendingApprovalItem, Question, ToolCallData } from "../types"
import { api, escapeHtml } from "../lib/api"
import { Disclosure } from "./Markdown"
import { ToolRowHead, deriveToolSummary, HookContexts } from "./ToolEvent"

/* 审批/提问 takeover 卡（dsh composerStack 停靠语义：渲染在输入卡上方）。 */

async function postApproval(callId: string, approved: boolean) {
  return api<{ events?: unknown[]; message?: unknown }>(`/api/approvals/${encodeURIComponent(callId)}/${approved ? "approve" : "deny"}`, {
    method: "POST",
    body: JSON.stringify(approved ? {} : { reason: "用户在页面拒绝执行" }),
  })
}

export function PermissionCard({ item, onResolved }: {
  item: PendingApprovalItem
  onResolved?: (item: PendingApprovalItem, approved: boolean) => void
}) {
  const [status, setStatus] = useState<"pending" | "approved" | "denied" | "failed">("pending")
  const argsText = JSON.stringify(item.arguments || {}, null, 2)
  const summary = deriveToolSummary(item.tool, item.arguments || {}) || item.reason || "执行该工具前需要用户确认。"
  const stateLabel = status === "pending" ? "待确认" : status === "approved" ? "已允许" : status === "denied" ? "已拒绝" : "审批失败"
  const decide = async (approved: boolean) => {
    setStatus(approved ? "approved" : "denied")
    try {
      await postApproval(item.call_id, approved)
      onResolved?.(item, approved)
    } catch {
      setStatus("failed")
    }
  }
  return (
    <article className={`permission-event pending status-${status}`} data-call-id={item.call_id}>
      <Disclosure
        className="permission-inner"
        summary={
          <>
            <ToolRowHead tool={item.tool} summary={summary} state="running" />
            <em className="permission-state">{stateLabel}</em>
          </>
        }
      >
        <p>{item.reason || "执行该工具前需要用户确认。"}</p>
        <HookContexts contexts={(item.data as ToolCallData)?.hook_contexts} />
        <details><summary>请求参数</summary><pre>{argsText}</pre></details>
        {status === "pending" && (
          <div className="permission-actions">
            <button type="button" onClick={() => decide(true)}>允许</button>
            <button type="button" onClick={() => decide(false)}>拒绝</button>
          </div>
        )}
      </Disclosure>
    </article>
  )
}

function QuestionFields({ questions, onSubmit }: {
  questions: Question[]
  onSubmit: (answers: Record<string, string | string[]>) => void
}) {
  // 多选暂存用 \u0001 连接的字符串；提交时拆成 string[]（单值保持 string）
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const set = (id: string, value: string) => setAnswers((prev) => ({ ...prev, [id]: value }))
  const submit = () => {
    const payload: Record<string, string | string[]> = {}
    for (const [id, value] of Object.entries(answers)) {
      if (!value) continue
      const parts = value.split("\u0001").filter(Boolean)
      payload[id] = parts.length > 1 ? parts : parts[0]
    }
    if (Object.keys(payload).length === 0) return
    onSubmit(payload)
  }
  return (
    <div className="question-list">
      {questions.map((question, index) => {
        const id = question.id || `q${index + 1}`
        const options = Array.isArray(question.options) && question.options.length > 0 ? question.options : null
        return (
          <div className="question-item" key={id}>
            {question.header ? <div className="question-head">{question.header}</div> : null}
            <div className="question-text">{question.question || ""}</div>
            {options ? (
              <div className="question-options">
                {options.map((option) => (
                  <label className="question-option" key={option.label}>
                    <input
                      type={question.multi_select ? "checkbox" : "radio"}
                      name={`opt-${escapeHtml(id)}`}
                      value={option.label}
                      checked={answers[id]?.split("\u0001").includes(option.label) || false}
                      onChange={() => {
                        if (question.multi_select) {
                          const current = (answers[id] || "").split("\u0001").filter(Boolean)
                          const next = current.includes(option.label) ? current.filter((v) => v !== option.label) : [...current, option.label]
                          set(id, next.join("\u0001"))
                        } else {
                          set(id, option.label)
                        }
                      }}
                    />
                    <span><strong>{option.label}</strong>{option.description ? ` — ${option.description}` : ""}</span>
                  </label>
                ))}
              </div>
            ) : (
              <input
                type="text"
                className="question-answer"
                placeholder="输入回答…"
                value={answers[id] || ""}
                onChange={(e) => set(id, e.target.value)}
              />
            )}
          </div>
        )
      })}
      <div className="permission-actions">
        <button type="button" onClick={submit}>提交回答</button>
      </div>
    </div>
  )
}

export function QuestionCard({ item, onAnswered }: {
  item: PendingApprovalItem
  onAnswered?: (item: PendingApprovalItem, answers: Record<string, string | string[]>) => void
}) {
  const [status, setStatus] = useState<"pending" | "answered" | "failed">("pending")
  const questions = Array.isArray(item.questions) ? item.questions : []
  // 回答负载：{ question_id: string | string[] }（对齐 app.js submitUserAnswers 的 answers 对象）
  const headline = questions.length === 1
    ? (questions[0].question || questions[0].header || "问题")
    : `${questions.length} 个问题待回答`
  return (
    <article className={`user-question-event pending status-${status}`} data-call-id={item.call_id}>
      <Disclosure
        className="permission-inner"
        summary={
          <>
            <ToolRowHead tool="ask_user_question" summary={headline} state="running" />
            <em className="permission-state">{status === "pending" ? "待回答" : status === "answered" ? "已回答" : "提交失败"}</em>
          </>
        }
      >
        {status === "pending" ? (
          <QuestionFields
            questions={questions}
            onSubmit={async (answers) => {
              try {
                await api(`/api/approvals/${encodeURIComponent(item.call_id)}/answer`, {
                  method: "POST",
                  body: JSON.stringify({ answers }),
                })
                setStatus("answered")
                onAnswered?.(item, answers)
              } catch {
                setStatus("failed")
              }
            }}
          />
        ) : (
          <p>回答已提交，Nova 正在继续。</p>
        )}
      </Disclosure>
    </article>
  )
}
