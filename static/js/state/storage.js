export function readStorageList(key, fallback = []) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) {
      return fallback;
    }
    const value = JSON.parse(raw);
    return Array.isArray(value) ? value : [];
  } catch {
    return fallback;
  }
}

export function writeStorageList(key, values) {
  localStorage.setItem(key, JSON.stringify(Array.from(values)));
}

export function readStorageBool(key, fallback = false) {
  const raw = localStorage.getItem(key);
  if (raw === null) {
    return fallback;
  }
  return raw === "true";
}

export function writeStorageBool(key, value) {
  localStorage.setItem(key, String(Boolean(value)));
}
