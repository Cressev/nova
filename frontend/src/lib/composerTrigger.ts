/* 对话框 / 与 $ 触发弹窗的纯逻辑层（dsh ui-input-trigger 的 Nova 简化版）。
 *
 * 与后端 src/nova/runtime/triggers.py 的边界保持一致：
 * - 只在草稿首字符（leading 位置）触发；行中 / @ 不触发；
 * - token 只允许 [A-Za-z0-9_-]（首字符为字母），query 里出现 "/" 等字符
 *   即判定为路径（/Users/...、$HOME/...），返回 null，不弹菜单；
 * - 候选为空同样不弹——路径输入全程无菜单。
 * 交互契约照搬 dsh MenuView：↑↓ 移动高亮、Enter 选中、Esc 关闭、
 * IME 组字放行、mousedown 选中不抢焦点、菜单外 pointerdown 关闭。
 */

export type ComposerTriggerKind = "slash" | "skill"

export interface ComposerTriggerHit {
  kind: ComposerTriggerKind
  /** 触发符之后到光标为止的过滤词（小写）。 */
  query: string
  /** 稳定 key：kind + 原始 query，用于记忆“本 token 已被 Esc 关闭”。 */
  tokenKey: string
}

export interface TriggerCandidate {
  name: string
  description: string
  /** 参数提示（如 /skill 的 <技能名>）。 */
  hint?: string
}

const TOKEN_PREFIX = /^[A-Za-z][A-Za-z0-9_-]*$/

/** 检测草稿首部的 / 或 $ 触发 token；不满足边界（路径、行中、空草稿）返回 null。 */
export function detectComposerTrigger(draft: string, caret: number): ComposerTriggerHit | null {
  if (draft.length === 0 || caret <= 0) return null
  const head = draft.charAt(0)
  if (head !== "/" && head !== "$") return null
  const query = draft.slice(1, Math.min(caret, draft.length))
  if (query.length === 0) {
    return { kind: head === "/" ? "slash" : "skill", query: "", tokenKey: `${head}:` }
  }
  // 首字符必须字母、后续仅限 token 字符；出现 "/" 等即视为路径输入。
  if (!TOKEN_PREFIX.test(query)) return null
  const lower = query.toLowerCase()
  return {
    kind: head === "/" ? "slash" : "skill",
    query: lower,
    tokenKey: `${head}:${lower}`,
  }
}

/** 按前缀（大小写不敏感）过滤命令或技能候选；命令名去前导 / 后匹配。 */
export function filterTriggerCandidates(
  kind: ComposerTriggerKind,
  query: string,
  commands: readonly TriggerCandidate[],
  skills: readonly TriggerCandidate[],
): TriggerCandidate[] {
  const source = kind === "slash" ? commands : skills
  const bare = (name: string) => (kind === "slash" ? name.replace(/^\//, "") : name)
  const q = query.toLowerCase()
  if (!q) return source.slice()
  return source.filter((item) => bare(item.name).toLowerCase().startsWith(q))
}

/** 选中候选后写回草稿的完整 token（带尾随空格，便于直接继续输入参数）。 */
export function triggerToken(kind: ComposerTriggerKind, name: string): string {
  const bare = kind === "slash" ? name.replace(/^\//, "") : name.replace(/^\$/, "")
  return `${kind === "slash" ? "/" : "$"}${bare} `
}
