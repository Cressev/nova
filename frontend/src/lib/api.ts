import type { ReactNode } from "react"

/**
 * API 客户端（对齐 dsh client-runtime 的 fetch 封装形态）。
 * 统一 JSON 请求 + 错误规范化，组件层不直接碰 fetch。
 */
export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {})
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }
  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const detail = await response.json()
      if (detail && typeof detail.detail === "string") message = detail.detail
    } catch {
      /* 非 JSON 错误体，保留状态码文案 */
    }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/** React 帮助：条件 className（dsh css compose 的轻量等价物）。 */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ")
}

/** 通用工具函数集（app.js 移植）。 */
export function formatTime(value: string | null | undefined): string {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
}

export function shortText(text: string, max = 64): string {
  const value = String(text || "")
  return value.length > max ? `${value.slice(0, max)}…` : value
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const ts = Date.parse(iso)
  if (Number.isNaN(ts)) return ""
  const diff = Date.now() - ts
  if (diff < 60_000) return "刚刚"
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}分钟`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}小时`
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

export function projectName(path: string): string {
  const normalized = String(path || "").replace(/\\/g, "/").replace(/\/+$/, "")
  if (!normalized) return "未分组"
  const marker = normalized.lastIndexOf("/")
  return marker === -1 ? normalized : normalized.slice(marker + 1) || "/"
}

export function workspaceGroupKey(path: string | null | undefined): string {
  return path ? String(path) : "__ungrouped__"
}

export function escapeHtml(value: string): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

export function shortId(value: string): string {
  return String(value || "").slice(-8)
}
