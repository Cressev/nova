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
  custom_models?: string[]
  [key: string]: unknown
}

const PROTOCOL_LABELS: Record<string, string> = {
  openai: "OpenAI Chat Completions",
  anthropic: "Anthropic Messages",
}

interface ModelItem {
  id: string
  owned_by?: string
  display_name?: string
}

export function SettingsDialog({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: () => void }) {
  const [config, setConfig] = useState<ConfigShape>({})
  const [apiKeyInput, setApiKeyInput] = useState("")
  const [flash, setFlash] = useState("")
  const [error, setError] = useState("")
  const [models, setModels] = useState<ModelItem[]>([])
  const [modelsLoading, setModelsLoading] = useState(false)
  const [candidates, setCandidates] = useState<ModelItem[] | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [adding, setAdding] = useState(false)
  const [addValue, setAddValue] = useState("")
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

  const showFlash = (text: string) => {
    setFlash(text)
    if (flashTimer.current) window.clearTimeout(flashTimer.current)
    flashTimer.current = window.setTimeout(() => setFlash(""), 1600)
  }

  /* 自动获取模型列表（当前 provider 的 /models 端点；失败提示手动输入）。 */
  const fetchModels = async () => {
    setModelsLoading(true)
    setError("")
    try {
      const result = await api<{ models?: ModelItem[]; count?: number }>("/api/runtime/models")
      const items = (result.models || []) as ModelItem[]
      setModels(items)
      if (items.length === 0) {
        setError("提供方返回空列表，可手动添加模型。")
        return
      }
      setCandidates(items)
      setPicked(new Set())
    } catch (exc) {
      setError(`模型列表获取失败：${String((exc as Error)?.message || exc)}，可手动输入模型名。`)
    } finally {
      setModelsLoading(false)
    }
  }

  /* dsh ModelListEditor 语义：custom_models 是用户显式维护的行；
   * 采纳候选 = 追加勾选项（已存在的 id 不重复），一次 PATCH 落盘。 */
  const saveCustomModels = async (next: string[]) => {
    const deduped: string[] = []
    for (const name of next) {
      const value = name.trim()
      if (value && !deduped.includes(value)) deduped.push(value)
    }
    setConfig((c) => ({ ...c, custom_models: deduped }))
    await patch({ custom_models: deduped })
  }

  const adoptPicked = async () => {
    if (!candidates) return
    const existing = (config.custom_models || []) as string[]
    const next = [...existing]
    for (const candidate of candidates) {
      if (picked.has(candidate.id) && !next.includes(candidate.id)) next.push(candidate.id)
    }
    setCandidates(null)
    setPicked(new Set())
    await saveCustomModels(next)
  }

  const commitAdd = async () => {
    const value = addValue.trim()
    if (!value) { setAdding(false); return }
    setAdding(false)
    setAddValue("")
    const existing = (config.custom_models || []) as string[]
    if (existing.includes(value)) return
    await saveCustomModels([...existing, value])
  }

  if (!open) return null

  const presets = (config.provider_presets || []) as PresetItem[]
  const currentPreset = presets.find((p) => p.id === (config.provider_preset || "bigmodel"))
  const protocol = currentPreset?.protocol || "openai"
  const customModels = (config.custom_models || []) as string[]
  const modelOptions: string[] = []
  for (const name of [String(config.model || ""), ...customModels]) {
    if (name && !modelOptions.includes(name)) modelOptions.push(name)
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
                <span className="settings-row-title">当前模型</span>
                <span className="settings-row-desc">对话使用的模型</span>
              </div>
              <select
                id="settings-model"
                className="settings-control settings-control-mono"
                value={String(config.model || "")}
                onChange={(e) => {
                  const value = e.target.value
                  setConfig((c) => ({ ...c, model: value }))
                  void patch({ provider_model: value })
                }}
              >
                {modelOptions.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </div>
            <div className="settings-models-block">
              <div className="settings-models-head">
                <span className="settings-row-title">模型列表</span>
                <div className="settings-models-actions">
                  <button
                    id="settings-model-fetch"
                    className="settings-link-button"
                    type="button"
                    disabled={modelsLoading}
                    onClick={() => void fetchModels()}
                  >
                    {modelsLoading ? "获取中…" : "获取可用模型"}
                  </button>
                  <button
                    className="settings-add-model"
                    type="button"
                    onClick={() => { setAdding(true); setAddValue("") }}
                  >
                    ＋ 添加模型
                  </button>
                </div>
              </div>
              {adding ? (
                <input
                  id="settings-model-add"
                  className="settings-control settings-control-mono settings-model-add-input"
                  type="text"
                  autoFocus
                  placeholder="输入模型名，如 glm-4.6"
                  value={addValue}
                  onChange={(e) => setAddValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.preventDefault(); void commitAdd() }
                    if (e.key === "Escape") { e.preventDefault(); setAdding(false); setAddValue("") }
                  }}
                  onBlur={() => void commitAdd()}
                />
              ) : null}
              {customModels.length === 0 && !adding ? (
                <div className="settings-models-empty">暂无自定义模型，可点击"＋ 添加模型"或从提供方获取</div>
              ) : (
                <div className="settings-models-list">
                  {customModels.map((name) => (
                    <span key={name} className="settings-model-chip">
                      <span className="settings-model-chip-name">{name}</span>
                      {name === config.model ? <span className="settings-model-chip-tag">当前</span> : null}
                      <button
                        type="button"
                        className="settings-model-chip-remove"
                        aria-label={`删除 ${name}`}
                        onClick={() => void saveCustomModels(customModels.filter((m) => m !== name))}
                      >×</button>
                    </span>
                  ))}
                </div>
              )}
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
        {candidates ? (
          <div className="settings-candidates" role="dialog" aria-label="选择要添加的模型">
            <div className="settings-candidates-head">
              <span>获取到 {candidates.length} 个模型，勾选要添加的</span>
              <button
                type="button"
                className="settings-link-button"
                onClick={() => {
                  setPicked(picked.size === candidates.length ? new Set() : new Set(candidates.map((c) => c.id)))
                }}
              >
                {picked.size === candidates.length ? "取消全选" : "全选"}
              </button>
            </div>
            <ul className="settings-candidates-list">
              {candidates.map((c) => {
                const already = customModels.includes(c.id)
                return (
                  <li key={c.id}>
                    <label className={already ? "settings-candidate settings-candidate-dim" : "settings-candidate"}>
                      <input
                        type="checkbox"
                        disabled={already}
                        checked={picked.has(c.id)}
                        onChange={() => {
                          const next = new Set(picked)
                          if (next.has(c.id)) next.delete(c.id)
                          else next.add(c.id)
                          setPicked(next)
                        }}
                      />
                      <span className="settings-candidate-id">{c.id}</span>
                      {already ? <span className="settings-model-chip-tag">已有</span> : null}
                    </label>
                  </li>
                )
              })}
            </ul>
            <div className="settings-candidates-foot">
              <button type="button" className="settings-ghost-button" onClick={() => { setCandidates(null); setPicked(new Set()) }}>取消</button>
              <button
                type="button"
                className="settings-primary-button"
                disabled={picked.size === 0}
                onClick={() => void adoptPicked()}
              >
                添加所选{picked.size > 0 ? `（${picked.size}）` : ""}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}
