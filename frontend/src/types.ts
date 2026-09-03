export interface ChatSession {
  id: string
  title: string
  workspace: string | null
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: string
  role: "user" | "assistant" | "error" | string
  content: string
  created_at: string | null
}

export interface ToolCallData {
  spec?: Record<string, unknown>
  job?: { id?: string }
  job_id?: string
  duration_ms?: number
  annotation?: string | null
  failure_reason?: string
  retryable?: boolean
  status?: string
  diff?: {
    files?: string[]
    additions?: number
    deletions?: number
    preview?: string
  } | null
  sources?: Array<{ title?: string; url?: string }>
  results?: Array<{ title?: string; url?: string }>
  hook_contexts?: Array<{ source: string; event: string; decision?: string; reason?: string }>
  [key: string]: unknown
}

export type ToolRowState = "running" | "ok" | "error" | "stopped"

export interface QuestionOption {
  label: string
  description?: string
}

export interface Question {
  id: string
  question: string
  header?: string
  multi_select?: boolean
  options?: QuestionOption[]
}

export interface PendingApprovalItem {
  id: string
  call_id: string
  tool: string
  arguments: Record<string, unknown>
  permission?: string
  reason?: string
  risk?: string | null
  data?: Record<string, unknown>
  isQuestion?: boolean
  questions?: Question[]
  status?: string
}

export interface BgProcess {
  id: string
  command?: string
  title?: string
  status: string
}

export interface RuntimeConfig {
  model?: string
  models?: string[]
  permission_mode?: string
  network_access?: boolean
  version?: string
  [key: string]: unknown
}

export interface TraceEvent {
  id: string
  event_type: string
  created_at: string
  title?: string
  message?: string
  tool?: string | null
  arguments?: Record<string, unknown>
  output?: string | null
  status?: string | null
  duration_ms?: number
  [key: string]: unknown
}
