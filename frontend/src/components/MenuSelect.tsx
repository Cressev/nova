import { useEffect, useRef, useState } from "react"

/* ============================================================================
   MenuSelect —— 对齐 dsh PermissionSelect + Menu 原语（figma 122:9481）：
   触发器 = 28px pill（r24，hover 浮层底，chevron 120ms 旋转）；
   浮层卡 = r12、4px 内边距、inverted 发丝边、shadow、40px 行（r10、icon 16px 槽）。
   替换原生 <select>（"上个世纪"观感的根源）。
   ========================================================================== */

const CHEVRON = '<path d="M3.5 5.5L7 9L10.5 5.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>'

export interface MenuOption {
  value: string
  label: string
  icon?: React.ReactNode
  hint?: string
}

export function MenuSelect({ id, value, options, onChange, title, leadingIcon }: {
  id?: string
  value: string
  options: MenuOption[]
  onChange: (value: string) => void
  title: string
  leadingIcon?: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const current = options.find((o) => o.value === value) || options[0]

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current !== null && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false) }
    document.addEventListener("mousedown", onDoc)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDoc)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

  return (
    <div className="menu-select" ref={rootRef}>
      <button
        type="button"
        id={id}
        className="menu-select-trigger"
        title={title}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {leadingIcon !== undefined ? <span className="menu-select-icon">{leadingIcon}</span> : null}
        <span className="menu-select-label">{current?.label ?? ""}</span>
        <svg className="menu-select-chevron" width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true" dangerouslySetInnerHTML={{ __html: CHEVRON }} />
      </button>
      {open ? (
        <div className="menu-select-list" role="listbox">
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={option.value === value}
              className={`menu-select-item${option.value === value ? " selected" : ""}`}
              onClick={() => { onChange(option.value); setOpen(false) }}
            >
              {option.icon !== undefined ? <span className="menu-select-item-icon">{option.icon}</span> : null}
              <span className="menu-select-item-label">{option.label}</span>
              {option.hint ? <span className="menu-select-item-hint">{option.hint}</span> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
