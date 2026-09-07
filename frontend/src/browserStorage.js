function localStorageOrNull() {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

export function readBrowserStorage(key) {
  try {
    return localStorageOrNull()?.getItem(key) ?? null
  } catch {
    return null
  }
}

export function writeBrowserStorage(key, value) {
  try {
    const storage = localStorageOrNull()
    if (!storage) return false
    storage.setItem(key, value)
    return true
  } catch {
    return false
  }
}

export function removeBrowserStorage(key) {
  try {
    const storage = localStorageOrNull()
    if (!storage) return false
    storage.removeItem(key)
    return true
  } catch {
    return false
  }
}

export function clearBrowserStorage() {
  try {
    const storage = localStorageOrNull()
    if (!storage) return false
    storage.clear()
    return true
  } catch {
    return false
  }
}
