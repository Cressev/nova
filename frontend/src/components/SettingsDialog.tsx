import { useEffect, useState } from "react"
import { api } from "../lib/api"

/* 设置对话框（模型提供方/协议 + 运行时权限/沙箱/预算全部可配置可保存）。 */

interface PresetItem {
  id: string
  label: string
  default_model?: string
  protocol?: string
  base_url?: string
}

interface ConfigShape {
  model?: string
  base_url?: string
  provider_preset?: string
  provider_api_key_env?: string
  api_key_set?: boolean
  api_key_source?: string
  provider_presets?: PresetItem[]
  permission_mode?: string
  sandbox_mode?: string
  approval_policy?: string
  network_access?: boolean
  max_tool_rounds?: number
  context_window_tokens?: number
  permission_modes?: string[]
  sandbox_modes?: string[]
  approval_policies?: string[]
  [key: string]: unknown
}

const PERMISSION_LABELS: Record<string, string> = {
  read_only: "只读",
  ask: "询问",
  workspace_write: "工作区写入",
  default: "标准",
  plan: "计划",
  accept_edits: "接受编辑",
  dont_ask: "不询问",
  bypass_permissions: "Full access",
}
const SANDBOX_LABELS: Record<string, string> = {
  read_only: "只读沙箱",
  workspace_write: "工作区沙箱",
  danger_full_access: "全放开沙箱",
}
const APPROVAL_LABELS: Record<string, string> = {
  untrusted: "仅信任命令",
  on_failure: "失败时",
  on_request: "按请求",
  never: "从不",
  granular: "细粒度",
}
const PROTOCOL_LABELS: Record<string, string> = {
  openai: "OpenAI Chat Completions",
  anthropic: "Anthropic Messages",
}

export function SettingsDialog({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: () => void }) {
  const [config, setConfig] = useState<ConfigShape>({})
  const [apiKeyInput, setApiKeyInput] = useState("")
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")

  useEffect(() => {
    if (!open) return
    setMessage("")
    setError("")
    setApiKeyInput("")
    void api<ConfigShape>("/api/runtime/config").then(setConfig).catch(() => setError("无法读取当前配置"))
  }, [open])

  if (!open) return null

  const presets = (config.provider_presets || []) as PresetItem[]
  const currentPreset = presets.find((p) => p.id === (config.provider_preset || "bigmodel"))
  const protocol = currentPreset?.protocol || "openai"

  const save = async () => {
    setSaving(true)
    setError("")
    setMessage("")
    try {
      const patch: Record<string, unknown> = {
        provider_preset: config.provider_preset || "bigmodel",
        provider_base_url: String(config.base_url || "").trim(),
        provider_model: String(config.model || "").trim(),
        permission_mode: config.permission_mode || "ask",
        sandbox_mode: config.sandbox_mode || "read_only",
        approval_policy: config.approval_policy || "on_request",
        network_access: Boolean(config.network_access),
        max_tool_rounds: Number(config.max_tool_rounds) || 10,
        context_window_tokens: Number(config.context_window_tokens) || 128000,
      }
      await api("/api/runtime/config", { method: "PATCH", body: JSON.stringify(patch) })
      if (apiKeyInput.trim()) {
        await api("/api/runtime/secrets", { method: "POST", body: JSON.stringify({ bigmodel_api_key: apiKeyInput.trim() }) })
      }
      const fresh = await api<ConfigShape>("/api/runtime/config")
      setConfig(fresh)
      setApiKeyInput("")
      setMessage("已保存并即时生效")
      onSaved()
    } catch (exc) {
      setError(String((exc as Error)?.message || exc))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="settings-overlay" id="settings-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="settings-dialog" role="dialog" aria-label="设置">
        <div className="settings-head">
          <h2>设置</h2>
          <button className="settings-close" type="button" aria-label="关闭" onClick={onClose}>×</button>
        </div>
        <div className="settings-body">
          <section className="settings-section">
            <h3>模型提供方</h3>
            <label className="settings-field">
              <span>提供方预设</span>
              <select
                id="settings-preset"
                value={config.provider_preset || "bigmodel"}
                onChange={(e) => {
                  const id = e.target.value
                  const preset = presets.find((p) => p.id === id)
                  setConfig((c) => ({
                    ...c,
                    provider_preset: id,
                    base_url: preset?.base_url || c.base_url,
                    model: preset?.default_model || c.model,
                  }))
                }}
              >
                {presets.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
            </label>
            <div className="settings-hint">
              协议：{PROTOCOL_LABELS[protocol] || protocol}
              {config.provider_api_key_env ? ` · 密钥环境变量 ${config.provider_api_key_env}` : ""}
            </div>
            <label className="settings-field">
              <span>Base URL</span>
              <input
                id="settings-base-url"
                type="text"
                value={String(config.base_url || "")}
                placeholder={currentPreset ? "留空使用预设地址" : "https://…"}
                onChange={(e) => setConfig((c) => ({ ...c, base_url: e.target.value }))}
              />
            </label>
            <label className="settings-field">
              <span>模型</span>
              <input
                id="settings-model"
                type="text"
                value={String(config.model || "")}
                placeholder="glm-4.7 / claude-sonnet-4-5 / …"
                onChange={(e) => setConfig((c) => ({ ...c, model: e.target.value }))}
              />
            </label>
            <label className="settings-field">
              <span>API Key（{config.provider_api_key_env || "API_KEY"}）</span>
              <input
                id="settings-api-key"
                type="password"
                value={apiKeyInput}
                placeholder={config.api_key_set ? `已配置（${config.api_key_source === "environment" ? "环境变量" : "运行时"}），输入以更换` : "未配置"}
                onChange={(e) => setApiKeyInput(e.target.value)}
                autoComplete="off"
              />
            </label>
          </section>

          <section className="settings-section">
            <h3>权限与沙箱</h3>
            <label className="settings-field">
              <span>权限模式</span>
              <select id="settings-permission" value={config.permission_mode || "ask"} onChange={(e) => setConfig((c) => ({ ...c, permission_mode: e.target.value }))}>
                {(config.permission_modes || []).map((m) => (
                  <option key={m} value={m}>{PERMISSION_LABELS[m] || m}</option>
                ))}
              </select>
            </label>
            <label className="settings-field">
              <span>文件沙箱</span>
              <select id="settings-sandbox" value={config.sandbox_mode || "read_only"} onChange={(e) => setConfig((c) => ({ ...c, sandbox_mode: e.target.value }))}>
                {(config.sandbox_modes || []).map((m) => (
                  <option key={m} value={m}>{SANDBOX_LABELS[m] || m}</option>
                ))}
              </select>
            </label>
            <label className="settings-field">
              <span>审批策略</span>
              <select id="settings-approval" value={config.approval_policy || "on_request"} onChange={(e) => setConfig((c) => ({ ...c, approval_policy: e.target.value }))}>
                {(config.approval_policies || []).map((m) => (
                  <option key={m} value={m}>{APPROVAL_LABELS[m] || m}</option>
                ))}
              </select>
            </label>
          </section>

          <section className="settings-section">
            <h3>运行时</h3>
            <label className="settings-field">
              <span>网络访问</span>
              <select id="settings-network" value={config.network_access ? "1" : "0"} onChange={(e) => setConfig((c) => ({ ...c, network_access: e.target.value === "1" }))}>
                <option value="1">开启（web_search / web_fetch）</option>
                <option value="0">关闭</option>
              </select>
            </label>
            <label className="settings-field">
              <span>工具轮数上限（1-12）</span>
              <input id="settings-rounds" type="number" min={1} max={12} value={Number(config.max_tool_rounds) || 10} onChange={(e) => setConfig((c) => ({ ...c, max_tool_rounds: Number(e.target.value) }))} />
            </label>
            <label className="settings-field">
              <span>上下文窗口（tokens）</span>
              <input id="settings-window" type="number" min={8192} max={1000000} step={1000} value={Number(config.context_window_tokens) || 128000} onChange={(e) => setConfig((c) => ({ ...c, context_window_tokens: Number(e.target.value) }))} />
            </label>
          </section>
        </div>
        <div className="settings-foot">
          {error ? <span className="settings-error" role="alert">{error}</span> : null}
          {message ? <span className="settings-message">{message}</span> : null}
          <button id="settings-save" className="settings-save" type="button" disabled={saving} onClick={() => void save()}>
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  )
}
