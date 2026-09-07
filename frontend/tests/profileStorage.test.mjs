import assert from "node:assert/strict"
import test from "node:test"

import {
  guestStateMigrationKey,
  scopedStorageKey,
  shouldMigrateGuestState,
} from "../src/profileStorage.js"

test("browser state keys are isolated by profile", () => {
  assert.equal(scopedStorageKey("kari:history:v1", "profile-a"), "kari:history:v1:profile-a")
  assert.equal(scopedStorageKey("kari:history:v1", "profile-b"), "kari:history:v1:profile-b")
  assert.notEqual(
    scopedStorageKey("kari:reader-session:v1", "profile-a"),
    scopedStorageKey("kari:reader-session:v1", "profile-b"),
  )
})

test("guest state has an explicit scope and unsafe separators are encoded", () => {
  assert.equal(scopedStorageKey("kari:favorites:v1", ""), "kari:favorites:v1:guest")
  assert.equal(scopedStorageKey("kari:favorites:v1", "profile/a"), "kari:favorites:v1:profile%2Fa")
})

test("guest state migrates only once to its first authenticated profile", () => {
  assert.equal(shouldMigrateGuestState("discord-1", null, null), true)
  assert.equal(shouldMigrateGuestState("discord-1", "discord-1", null), true)
  assert.equal(shouldMigrateGuestState("discord-1", "discord-1", "1"), false)
  assert.equal(shouldMigrateGuestState("discord-2", "discord-1", null), false)
  assert.equal(guestStateMigrationKey("discord-1"), "kari:guest-state-migrated:v1:discord-1")
})
