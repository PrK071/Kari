import { test } from "node:test"
import assert from "node:assert/strict"

import { classifyMediaUrl, isVideoMedia } from "../src/profileMedia.js"

test("classifyMediaUrl: vazio", () => {
  assert.equal(classifyMediaUrl(""), "empty")
  assert.equal(classifyMediaUrl(null), "empty")
  assert.equal(classifyMediaUrl("   "), "empty")
})

test("classifyMediaUrl: /api/ exige fetch autenticado", () => {
  assert.equal(classifyMediaUrl("/api/profiles/p-1/media/avatar?v=123"), "authed")
})

test("classifyMediaUrl: url absoluta e /static/ sao diretas", () => {
  assert.equal(classifyMediaUrl("https://cdn.example.com/a.png"), "direct")
  assert.equal(classifyMediaUrl("http://cdn.example.com/a.png"), "direct")
  assert.equal(classifyMediaUrl("/static/backgrounds/preset.jpg"), "direct")
})

test("isVideoMedia: content-type de video vence", () => {
  assert.equal(isVideoMedia("video/mp4", "/api/profiles/p-1/media/background"), true)
  assert.equal(isVideoMedia("video/webm", ""), true)
  assert.equal(isVideoMedia("image/png", ""), false)
})

test("isVideoMedia: extensao de video em url direta", () => {
  assert.equal(isVideoMedia("", "https://cdn.example.com/bg.mp4"), true)
  assert.equal(isVideoMedia("", "https://cdn.example.com/bg.webm?x=1"), true)
  assert.equal(isVideoMedia("", "https://cdn.example.com/bg.jpg"), false)
})
