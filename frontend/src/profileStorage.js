export function scopedStorageKey(baseKey, profileId) {
  const scope = String(profileId || "guest").trim() || "guest"
  return `${baseKey}:${encodeURIComponent(scope)}`
}

export const GUEST_STATE_OWNER_KEY = "kari:guest-state-owner:v1"
export const GUEST_STATE_MIGRATION_KEY = "kari:guest-state-migrated:v1"

export function guestStateMigrationKey(profileId) {
  return scopedStorageKey(GUEST_STATE_MIGRATION_KEY, profileId)
}

export function shouldMigrateGuestState(profileId, ownerProfileId, completed) {
  const profile = String(profileId || "").trim()
  const owner = String(ownerProfileId || "").trim()
  if (!profile || completed === "1") return false
  return !owner || owner === profile
}

export function legacyProfileScopeForMigration(profileId, storedProfileId, enabled) {
  const profile = String(profileId || "").trim()
  const stored = String(storedProfileId || "").trim()
  return enabled && stored && stored !== profile ? stored : ""
}
