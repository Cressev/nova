export async function api(path, options = {}) {
  // 统一处理 API 错误，调用方只关注业务逻辑。
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}
