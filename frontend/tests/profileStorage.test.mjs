import assert from "node:assert/strict"
import test from "node:test"

import { scopedStorageKey } from "../src/profileStorage.js"

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
