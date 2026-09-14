import { useEffect, useRef, useState } from "react"
import { api } from "../lib/api"

/* 设置面板 —— 多供应商组（dsh groups 语义：一个供应商 = 一个组，
 * 组内多个模型可选；composer 按组列出）。
 *
 * 数据：provider_profiles[] 每组 {id,label,protocol,base_url,api_key_env,
 * api_key_set,models[]}；active_provider_id 指向当前启用组。
 * 全部即时生效（无保存按钮）。 */

interface ModelEntry {
  id: string
  name?: string | null
  context_window?: number | null
  max_tokens?: number | null
}

interface Profile {
  id: string
  label: string
  protocol: string
  base_url: string
  api_key_env: string
  api_key_set?: boolean
  models: ModelEntry[]
}

interface ModelItem {
  id: string
  owned_by?: string
  display_name?: string
}

interface ConfigShape {
  model?: string
  active_provider_id?: string
  provider_profiles?: Profile[]
  [key: string]: unknown
}

const PROTOCOL_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
}

let _profileSeq = 0
const newProfileId = () => {
  _profileSeq += 1
  return `custom-${Date.now().toString(36)}-${_profileSeq}`
}

export function SettingsDialog({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: () => void }) {
  const [config, setConfig] = useState<ConfigShape>({})
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [flash, setFlash] = useState("")
  const [error, setError] = useState("")
  const [probing, setProbing] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<{ pid: string; items: ModelItem[]; picked: Set<string> } | null>(null)
  const [adding, setAdding] = useState<{ pid: string } | null>(null)
  const [addValue, setAddValue] = useState("")
  const [newGroup, setNewGroup] = useState(false)
  const [newGroupDraft, setNewGroupDraft] = useState({ label: "", protocol: "openai", base_url: "" })
  const flashTimer = useRef<number | null>(null)

  useEffect(() => {
    if (!open) return
    setError("")
    setNewGroup(false)
    setCandidates(null)
    setAdding(null)
    void api<ConfigShape>("/api/runtime/config").then((c) => {
      setConfig(c)
      setProfiles((c.provider_profiles || []) as Profile[])
    }).catch(() => setError("无法读取当前配置"))
  }, [open])

  useEffect(() => () => { if (flashTimer.current) window.clearTimeout(flashTimer.current) }, [])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, onClose])

  if (!open) return null

  const activeId = String(config.active_provider_id || (profiles[0]?.id ?? ""))

  const showFlash = (text: string) => {
    setFlash(text)
    if (flashTimer.current) window.clearTimeout(flashTimer.current)
    flashTimer.current = window.setTimeout(() => setFlash(""), 1600)
  }

  const refresh = async () => {
    const fresh = await api<ConfigShape>("/api/runtime/config")
    setConfig(fresh)
    setProfiles((fresh.provider_profiles || []) as Profile[])
  }

  const patch = async (update: Record<string, unknown>) => {
    setError("")
    try {
      await api("/api/runtime/config", { method: "PATCH", body: JSON.stringify(update) })
      await refresh()
      showFlash("已生效")
      onSaved()
    } catch (exc) {
      setError(String((exc as Error)?.message || exc))
    }
  }

  const saveProfiles = async (next: Profile[]) => {
    setProfiles(next)
    await patch({ provider_profiles: next.map((p) => ({ id: p.id, label: p.label, protocol: p.protocol, base_url: p.base_url, api_key_env: p.api_key_env, models: p.models.map((m) => ({ id: m.id, name: m.name || null, context_window: m.context_window || null, max_tokens: m.max_tokens || null })) })) })
  }

  const setActive = async (pid: string) => {
    setConfig((c) => ({ ...c, active_provider_id: pid }))
    await patch({ active_provider_id: pid })
  }

  const saveApiKey = async (pid: string, key: string) => {
    setError("")
    try {
      await api("/api/runtime/secrets", { method: "PATCH", body: JSON.stringify({ bigmodel_api_key: key, profile_id: pid }) })
      await refresh()
      showFlash("密钥已保存")
      onSaved()
    } catch (exc) {
      setError(String((exc as Error)?.message || exc))
    }
  }

  const probe = async (p: Profile, keyOverride?: string) => {
    setProbing(p.id)
    setError("")
    try {
      const result = await api<{ models?: ModelItem[] }>("/api/runtime/models", {
        method: "POST",
        body: JSON.stringify({ protocol: p.protocol, base_url: p.base_url, api_key: keyOverride || "", profile_id: p.id, api_key_env: p.api_key_env }),
      })
      const items = (result.models || []) as ModelItem[]
      if (items.length === 0) { setError("该供应商返回空模型列表。"); return }
      setCandidates({ pid: p.id, items, picked: new Set() })
    } catch (exc) {
      setError(`获取失败：${String((exc as Error)?.message || exc)}，可手动添加模型。`)
    } finally {
      setProbing(null)
    }
  }

  const adoptPicked = async () => {
    if (!candidates) return
    const target = profiles.find((p) => p.id === candidates.pid)
    if (!target) return
    const next = [...target.models]
    const existingIds = new Set(next.map((m) => m.id))
    for (const c of candidates.items) {
      if (candidates.picked.has(c.id) && !existingIds.has(c.id)) next.push({ id: c.id, name: c.display_name || null })
    }
    const updated = profiles.map((p) => p.id === candidates.pid ? { ...p, models: next } : p)
    setCandidates(null)
    await saveProfiles(updated)
  }

  const commitAdd = async (pid: string) => {
    const value = addValue.trim()
    setAdding(null)
    setAddValue("")
    if (!value) return
    const target = profiles.find((p) => p.id === pid)
    if (!target || target.models.some((m) => m.id === value)) return
    const updated = profiles.map((p) => p.id === pid ? { ...p, models: [...p.models, { id: value }] } : p)
    await saveProfiles(updated)
  }

  const createGroup = async () => {
    const label = newGroupDraft.label.trim() || "自定义供应商"
    const id = newProfileId()
    const profile: Profile = { id, label, protocol: newGroupDraft.protocol, base_url: newGroupDraft.base_url.trim(), api_key_env: "API_KEY", models: [] }
    setNewGroup(false)
    setNewGroupDraft({ label: "", protocol: "openai", base_url: "" })
    await saveProfiles([...profiles, profile])
    await setActive(id)
  }

  return (
    <div className="settings-overlay" id="settings-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="settings-dialog" role="dialog" aria-label="设置">
        <div className="settings-header">
          <h2>模型设置</h2>
          {flash ? <span className="settings-flash">{flash}</span> : null}
          <button className="settings-close" type="button" aria-label="关闭设置" onClick={onClose}>×</button>
        </div>
        <div className="settings-body">
          <div className="settings-profiles-hint">每个供应商是一个组，组内可添加多个模型，选中即启用。</div>
          {profiles.map((p) => (
            <section key={p.id} className={`settings-profile${p.id === activeId ? " settings-profile-active" : ""}`}>
              <div className="settings-profile-head">
                <input
                  className="settings-profile-label"
                  type="text"
                  defaultValue={p.label}
                  key={`label-${p.id}`}
                  onBlur={(e) => {
                    const value = e.target.value.trim()
                    if (value && value !== p.label) {
                      const updated = profiles.map((x) => x.id === p.id ? { ...x, label: value } : x)
                      void saveProfiles(updated)
                    }
                  }}
                />
                <span className="settings-profile-protocol">{PROTOCOL_LABELS[p.protocol] || p.protocol}</span>
                {p.id === activeId ? <span className="settings-profile-tag">当前</span> : (
                  <button type="button" className="settings-link-button" onClick={() => void setActive(p.id)}>启用</button>
                )}
                <button
                  type="button"
                  className="settings-profile-delete"
                  aria-label="删除该供应商组"
                  title="删除该供应商组"
                  onClick={() => { if (window.confirm(`删除供应商"${p.label}"及其所有模型？`)) void saveProfiles(profiles.filter((x) => x.id !== p.id)) }}
                >×</button>
              </div>
              <div className="settings-profile-fields">
                <label className="settings-profile-field">
                  <span>Base URL</span>
                  <input
                    className="settings-control settings-control-mono"
                    type="text"
                    defaultValue={p.base_url}
                    key={`url-${p.id}`}
                    placeholder="https://…"
                    onBlur={(e) => {
                      const value = e.target.value.trim()
                      if (value !== p.base_url) {
                        const updated = profiles.map((x) => x.id === p.id ? { ...x, base_url: value } : x)
                        void saveProfiles(updated)
                      }
                    }}
                  />
                </label>
                <label className="settings-profile-field">
                  <span>API Key</span>
                  <input
                    className="settings-control settings-control-mono"
                    type="password"
                    placeholder={p.api_key_set ? "已配置，输入可更换" : "未配置"}
                    key={`key-${p.id}`}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); const v = (e.target as HTMLInputElement).value.trim(); if (v) void saveApiKey(p.id, v); (e.target as HTMLInputElement).value = "" } }}
                    onBlur={(e) => { const v = e.target.value.trim(); if (v) { void saveApiKey(p.id, v); e.target.value = "" } }}
                  />
                </label>
              </div>
              <div className="settings-profile-models">
                <div className="settings-profile-models-head">
                  <span className="settings-row-title">模型</span>
                  <div className="settings-models-actions">
                    <button type="button" id={`settings-model-fetch-${p.id}`} className="settings-link-button" disabled={probing === p.id} onClick={() => void probe(p)}>
                      {probing === p.id ? "获取中…" : "获取可用模型"}
                    </button>
                    <button type="button" className="settings-add-model" onClick={() => { setAdding({ pid: p.id }); setAddValue("") }}>＋ 添加</button>
                  </div>
                </div>
                {adding?.pid === p.id ? (
                  <input
                    className="settings-control settings-control-mono settings-model-add-input"
                    type="text"
                    autoFocus
                    placeholder="模型名，如 glm-4.6"
                    value={addValue}
                    onChange={(e) => setAddValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") { e.preventDefault(); void commitAdd(p.id) }
                      if (e.key === "Escape") { e.preventDefault(); setAdding(null); setAddValue("") }
                    }}
                    onBlur={() => void commitAdd(p.id)}
                  />
                ) : null}
                {p.models.length === 0 && adding?.pid !== p.id ? (
                  <div className="settings-models-empty">暂无模型，可获取或手动添加</div>
                ) : (
                  <div className="settings-models-list">
                    {p.models.map((m, mi) => (
                      <ModelRow
                        key={`${p.id}/${m.id}/${mi}`}
                        model={m}
                        isCurrent={p.id === activeId && m.id === config.model}
                        onUse={() => { setConfig((c) => ({ ...c, model: m.id })); void patch({ provider_model: m.id, active_provider_id: p.id }) }}
                        onRemove={() => void saveProfiles(profiles.map((x) => x.id === p.id ? { ...x, models: x.models.filter((_, i) => i !== mi) } : x))}
                        onUpdate={(patch) => void saveProfiles(profiles.map((x) => x.id === p.id ? { ...x, models: x.models.map((mm, i) => i === mi ? { ...mm, ...patch } : mm) } : x))}
                      />
                    ))}
                  </div>
                )}
              </div>
            </section>
          ))}
          {newGroup ? (
            <section className="settings-profile settings-profile-new">
              <div className="settings-profile-head">
                <input className="settings-profile-label" type="text" placeholder="供应商名称" value={newGroupDraft.label} onChange={(e) => setNewGroupDraft((d) => ({ ...d, label: e.target.value }))} autoFocus />
              </div>
              <div className="settings-profile-fields">
                <label className="settings-profile-field">
                  <span>协议</span>
                  <select className="settings-control" value={newGroupDraft.protocol} onChange={(e) => setNewGroupDraft((d) => ({ ...d, protocol: e.target.value }))}>
                    <option value="openai">OpenAI Chat Completions</option>
                    <option value="anthropic">Anthropic Messages</option>
                  </select>
                </label>
                <label className="settings-profile-field">
                  <span>Base URL</span>
                  <input className="settings-control settings-control-mono" type="text" placeholder="https://…" value={newGroupDraft.base_url} onChange={(e) => setNewGroupDraft((d) => ({ ...d, base_url: e.target.value }))} />
                </label>
              </div>
              <div className="settings-profile-foot">
                <button type="button" className="settings-ghost-button" onClick={() => setNewGroup(false)}>取消</button>
                <button type="button" className="settings-primary-button" onClick={() => void createGroup()}>创建并启用</button>
              </div>
            </section>
          ) : (
            <button type="button" className="settings-add-profile" onClick={() => setNewGroup(true)}>＋ 添加供应商</button>
          )}
          {error ? <div className="settings-error" role="alert">{error}</div> : null}
        </div>
        {candidates ? (
          <div className="settings-candidates" role="dialog" aria-label="选择要添加的模型">
            <div className="settings-candidates-head">
              <span>获取到 {candidates.items.length} 个模型，勾选要添加到该组的</span>
              <button type="button" className="settings-link-button" onClick={() => setCandidates({ ...candidates, picked: candidates.picked.size === candidates.items.length ? new Set() : new Set(candidates.items.map((c) => c.id)) })}>
                {candidates.picked.size === candidates.items.length ? "取消全选" : "全选"}
              </button>
            </div>
            <ul className="settings-candidates-list">
              {candidates.items.map((c) => {
                const target = profiles.find((p) => p.id === candidates.pid)
                const already = target ? target.models.some((m) => m.id === c.id) : false
                return (
                  <li key={c.id}>
                    <label className={already ? "settings-candidate settings-candidate-dim" : "settings-candidate"}>
                      <input type="checkbox" disabled={already} checked={candidates.picked.has(c.id)} onChange={() => {
                        const next = new Set(candidates.picked)
                        if (next.has(c.id)) next.delete(c.id)
                        else next.add(c.id)
                        setCandidates({ ...candidates, picked: next })
                      }} />
                      <span className="settings-candidate-id">{c.id}</span>
                      {already ? <span className="settings-model-chip-tag">已有</span> : null}
                    </label>
                  </li>
                )
              })}
            </ul>
            <div className="settings-candidates-foot">
              <button type="button" className="settings-ghost-button" onClick={() => setCandidates(null)}>取消</button>
              <button type="button" className="settings-primary-button" disabled={candidates.picked.size === 0} onClick={() => void adoptPicked()}>
                添加所选{candidates.picked.size > 0 ? `（${candidates.picked.size}）` : ""}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}

/* 模型行（dsh ModelListEditor 语义）：显示 id/name + 展开后编辑 context_window/max_tokens。
 * 每行：使用按钮 + 显示名 + 删除按钮 + 展开折叠高级配置。 */
function ModelRow({ model, isCurrent, onUse, onRemove, onUpdate }: {
  model: ModelEntry
  isCurrent: boolean
  onUse: () => void
  onRemove: () => void
  onUpdate: (patch: Partial<ModelEntry>) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const displayName = model.name || model.id
  return (
    <div className={`settings-model-row${isCurrent ? " settings-model-row-current" : ""}`}>
      <div className="settings-model-row-main">
        <button
          type="button"
          className={`settings-model-chip-name${isCurrent ? " settings-model-chip-name-current" : ""}`}
          title={`使用 ${model.id}`}
          onClick={onUse}
        >{displayName}</button>
        {model.name ? <span className="settings-model-id-hint">{model.id}</span> : null}
        <button
          type="button"
          className="settings-model-expand"
          aria-label="高级配置"
          title="高级配置"
          onClick={() => setExpanded((v) => !v)}
        >{expanded ? "▾" : "▸"}</button>
        <button type="button" className="settings-model-chip-remove" aria-label={`删除 ${model.id}`} onClick={onRemove}>×</button>
      </div>
      {expanded ? (
        <div className="settings-model-advanced">
          <label className="settings-model-field">
            <span>显示名</span>
            <input
              className="settings-control"
              type="text"
              defaultValue={model.name || ""}
              placeholder={model.id}
              onBlur={(e) => { const v = e.target.value.trim(); if (v !== (model.name || "")) onUpdate({ name: v || null }) }}
            />
          </label>
          <label className="settings-model-field">
            <span>上下文窗口</span>
            <input
              className="settings-control"
              type="number"
              defaultValue={model.context_window ?? ""}
              placeholder="留空用默认"
              onBlur={(e) => { const v = e.target.value.trim(); const n = v ? parseInt(v, 10) : null; if (n !== model.context_window) onUpdate({ context_window: n && n > 0 ? n : null }) }}
            />
          </label>
          <label className="settings-model-field">
            <span>最大输出</span>
            <input
              className="settings-control"
              type="number"
              defaultValue={model.max_tokens ?? ""}
              placeholder="留空用默认"
              onBlur={(e) => { const v = e.target.value.trim(); const n = v ? parseInt(v, 10) : null; if (n !== model.max_tokens) onUpdate({ max_tokens: n && n > 0 ? n : null }) }}
            />
          </label>
        </div>
      ) : null}
    </div>
  )
}
