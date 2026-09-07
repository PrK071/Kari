import assert from "node:assert/strict"
import test from "node:test"

import { profileEntryTarget } from "../src/profileAccess.js"

test("profile entry opens authentication for a web guest", () => {
  assert.equal(profileEntryTarget(null), "auth")
  assert.equal(profileEntryTarget({}), "auth")
})

test("profile entry opens the editor for an authenticated profile", () => {
  assert.equal(profileEntryTarget({ id: "profile-123" }), "profile")
})
