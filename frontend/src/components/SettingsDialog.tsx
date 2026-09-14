import { useEffect, useRef, useState } from "react"
import { api } from "../lib/api"

/* 设置面板 —— dsh SettingsRoot/Modal 视觉规范（浅色 r24 面板、行式
 * 「标题+描述+右控件」布局、即时生效无保存按钮）。模型设置单区。 */

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
  [key: string]: unknown
}

const PROTOCOL_LABELS: Record<string, string> = {
  openai: "OpenAI Chat Completions",
  anthropic: "Anthropic Messages",
}

export function SettingsDialog({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: () => void }) {
  const [config, setConfig] = useState<ConfigShape>({})
  const [apiKeyInput, setApiKeyInput] = useState("")
  const [flash, setFlash] = useState("")
  const [error, setError] = useState("")
  const flashTimer = useRef<number | null>(null)

  useEffect(() => {
    if (!open) return
    setError("")
    setApiKeyInput("")
    void api<ConfigShape>("/api/runtime/config").then(setConfig).catch(() => setError("无法读取当前配置"))
  }, [open])

  useEffect(() => () => { if (flashTimer.current) window.clearTimeout(flashTimer.current) }, [])

  /* Esc 关闭（dsh Modal 惯例）。 */
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, onClose])

  if (!open) return null

  const presets = (config.provider_presets || []) as PresetItem[]
  const currentPreset = presets.find((p) => p.id === (config.provider_preset || "bigmodel"))
  const protocol = currentPreset?.protocol || "openai"

  const showFlash = (text: string) => {
    setFlash(text)
    if (flashTimer.current) window.clearTimeout(flashTimer.current)
    flashTimer.current = window.setTimeout(() => setFlash(""), 1600)
  }

  /* 即时生效（dsh 语义：改动即落盘，没有保存按钮）。 */
  const patch = async (update: Record<string, unknown>) => {
    setError("")
    try {
      await api("/api/runtime/config", { method: "PATCH", body: JSON.stringify(update) })
      const fresh = await api<ConfigShape>("/api/runtime/config")
      setConfig(fresh)
      showFlash("已生效")
      onSaved()
    } catch (exc) {
      setError(String((exc as Error)?.message || exc))
    }
  }

  const saveApiKey = async () => {
    const value = apiKeyInput.trim()
    if (!value) return
    setError("")
    try {
      await api("/api/runtime/secrets", { method: "POST", body: JSON.stringify({ bigmodel_api_key: value }) })
      setApiKeyInput("")
      const fresh = await api<ConfigShape>("/api/runtime/config")
      setConfig(fresh)
      showFlash("密钥已保存")
      onSaved()
    } catch (exc) {
      setError(String((exc as Error)?.message || exc))
    }
  }

  return (
    <div className="settings-overlay" id="settings-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="settings-dialog" role="dialog" aria-label="设置">
        <div className="settings-header">
          <h2>设置</h2>
          {flash ? <span className="settings-flash">{flash}</span> : null}
          <button className="settings-close" type="button" aria-label="关闭设置" onClick={onClose}>×</button>
        </div>
        <div className="settings-body">
          <section className="settings-group">
            <h3>模型提供方</h3>
            <div className="settings-row">
              <div className="settings-row-text">
                <span className="settings-row-title">提供方</span>
                <span className="settings-row-desc">协议 {PROTOCOL_LABELS[protocol] || protocol}</span>
              </div>
              <select
                id="settings-preset"
                className="settings-control"
                value={config.provider_preset || "bigmodel"}
                onChange={(e) => {
                  const id = e.target.value
                  const preset = presets.find((p) => p.id === id)
                  setConfig((c) => ({ ...c, provider_preset: id, base_url: preset?.base_url || c.base_url, model: preset?.default_model || c.model }))
                  void patch({
                    provider_preset: id,
                    provider_base_url: preset?.base_url || String(config.base_url || ""),
                    provider_model: preset?.default_model || String(config.model || ""),
                  })
                }}
              >
                {presets.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
            </div>
            <div className="settings-row">
              <div className="settings-row-text">
                <span className="settings-row-title">Base URL</span>
                <span className="settings-row-desc">API 端点地址</span>
              </div>
              <input
                id="settings-base-url"
                className="settings-control settings-control-mono"
                type="text"
                title={String(config.base_url || "")}
                defaultValue={String(config.base_url || "")}
                key={`base-${config.provider_preset}`}
                placeholder="https://…"
                onBlur={(e) => {
                  const value = e.target.value.trim()
                  if (value && value !== String(config.base_url || "")) {
                    setConfig((c) => ({ ...c, base_url: value }))
                    void patch({ provider_base_url: value })
                  }
                }}
              />
            </div>
            <div className="settings-row">
              <div className="settings-row-text">
                <span className="settings-row-title">模型</span>
                <span className="settings-row-desc">模型名</span>
              </div>
              <input
                id="settings-model"
                className="settings-control settings-control-mono"
                type="text"
                title={String(config.model || "")}
                defaultValue={String(config.model || "")}
                key={`model-${config.provider_preset}`}
                placeholder="glm-4.7"
                onBlur={(e) => {
                  const value = e.target.value.trim()
                  if (value && value !== String(config.model || "")) {
                    setConfig((c) => ({ ...c, model: value }))
                    void patch({ provider_model: value })
                  }
                }}
              />
            </div>
            <div className="settings-row">
              <div className="settings-row-text">
                <span className="settings-row-title">API Key</span>
                <span className="settings-row-desc">
                  {config.api_key_set
                    ? `已配置（${config.api_key_source === "environment" ? "环境变量" : "运行时"}）`
                    : "未配置"}
                  {config.provider_api_key_env ? ` · ${config.provider_api_key_env}` : ""}
                </span>
              </div>
              <input
                id="settings-api-key"
                className="settings-control"
                type="password"
                value={apiKeyInput}
                placeholder="••••••••"
                autoComplete="off"
                onChange={(e) => setApiKeyInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void saveApiKey() } }}
                onBlur={() => { if (apiKeyInput.trim()) void saveApiKey() }}
              />
            </div>
          </section>
          {error ? <div className="settings-error" role="alert">{error}</div> : null}
        </div>
      </div>
    </div>
  )
}
