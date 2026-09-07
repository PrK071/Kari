import assert from "node:assert/strict"
import test from "node:test"

import {
  clearBrowserStorage,
  readBrowserStorage,
  removeBrowserStorage,
  writeBrowserStorage,
} from "../src/browserStorage.js"

function withWindow(localStorage, run) {
  const previous = globalThis.window
  globalThis.window = { localStorage }
  try {
    run()
  } finally {
    if (previous === undefined) delete globalThis.window
    else globalThis.window = previous
  }
}

test("browser storage reads and writes available storage", () => {
  const values = new Map()
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
    clear: () => values.clear(),
  }

  withWindow(storage, () => {
    assert.equal(writeBrowserStorage("kari:test", "ok"), true)
    assert.equal(readBrowserStorage("kari:test"), "ok")
    assert.equal(removeBrowserStorage("kari:test"), true)
    assert.equal(readBrowserStorage("kari:test"), null)
    assert.equal(clearBrowserStorage(), true)
  })
})

test("browser storage failures never escape into the React render", () => {
  const blocked = new Proxy({}, {
    get() {
      throw new DOMException("blocked", "SecurityError")
    },
  })

  withWindow(blocked, () => {
    assert.equal(readBrowserStorage("kari:test"), null)
    assert.equal(writeBrowserStorage("kari:test", "ok"), false)
    assert.equal(removeBrowserStorage("kari:test"), false)
    assert.equal(clearBrowserStorage(), false)
  })
})
