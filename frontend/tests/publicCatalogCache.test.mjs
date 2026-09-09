import assert from "node:assert/strict"
import test from "node:test"

import {
  buildCachedCatalogPayload,
  compactPublicCatalogItem,
  normalizeCatalogText,
} from "../src/publicCatalogCache.js"

const hunter = {
  id: "hunter",
  title: "Hunter x Hunter",
  alternative_titles: ["HUNTER × HUNTER"],
  provider: "mangalivre",
  source: "MangaLivre",
  source_url: "https://example.test/manga/hunter",
  cover_url: "https://example.test/hunter.jpg",
  genres: ["Aventura"],
  chapter_count: 420,
  latest_chapter: "420",
  catalog_home_ready: true,
  token: "must-not-survive",
  history: [{ private: true }],
}

test("normalizes unicode multiplication marks consistently", () => {
  assert.equal(normalizeCatalogText("HÚNTER × HUNTER"), "hunter x hunter")
})

test("public projection keeps only discovery fields", () => {
  const item = compactPublicCatalogItem(hunter)
  assert.equal(item.title, "Hunter x Hunter")
  assert.equal(item.token, undefined)
  assert.equal(item.history, undefined)
})

test("public projection rejects signed or credential-bearing URLs", () => {
  const item = compactPublicCatalogItem({
    ...hunter,
    cover_url: "https://user:password@example.test/cover.jpg",
    source_url: "https://example.test/work?X-Amz-Signature=secret",
  })
  assert.equal(item.cover_url, "")
  assert.equal(item.source_url, "")
})

test("cached catalog serves home and local alias search", () => {
  const snapshot = { savedAt: 123, items: [compactPublicCatalogItem(hunter)] }
  const home = buildCachedCatalogPayload(snapshot)
  const search = buildCachedCatalogPayload(snapshot, { query: "HUNTER × HUNTER" })

  assert.equal(home.items.length, 1)
  assert.equal(home.browserCached, true)
  assert.equal(search.items[0].title, "Hunter x Hunter")
  assert.equal(search.sections[0].title, "Resultados salvos")
})
