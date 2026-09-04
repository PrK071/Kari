export function scopedStorageKey(baseKey, profileId) {
  const scope = String(profileId || "guest").trim() || "guest"
  return `${baseKey}:${encodeURIComponent(scope)}`
}
