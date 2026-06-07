export function queryRequired(selector, root = document) {
  const node = root.querySelector(selector);
  if (!node) {
    throw new Error(`缺少页面节点：${selector}`);
  }
  return node;
}
