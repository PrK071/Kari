import assert from "node:assert/strict"
import test from "node:test"

import {
  browserImportAccepts,
  fileExtension,
  naturalFileCompare,
  titleFromFilename,
} from "../src/browserLibrary.js"

test("classifies supported device-library files", () => {
  assert.equal(browserImportAccepts("hq", "volume.cbz"), true)
  assert.equal(browserImportAccepts("hq", "page.webp"), true)
  assert.equal(browserImportAccepts("hq", "book.epub"), false)
  assert.equal(browserImportAccepts("novels", "book.epub"), true)
  assert.equal(browserImportAccepts("novels", "chapter.html"), true)
  assert.equal(fileExtension("VOLUME.CBZ"), "cbz")
})

test("derives readable titles and sorts comic pages naturally", () => {
  assert.equal(titleFromFilename("minha_web-novel.epub"), "minha web novel")
  assert.deepEqual(["10.jpg", "2.jpg", "1.jpg"].sort(naturalFileCompare), ["1.jpg", "2.jpg", "10.jpg"])
})
