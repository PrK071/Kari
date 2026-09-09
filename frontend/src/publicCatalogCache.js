const DATABASE_NAME = "kari-public-catalog"
const STORE_NAME = "snapshots"
const DATABASE_VERSION = 1
const SNAPSHOT_KEY = "catalog-v1"

const SENSITIVE_QUERY_NAMES = new Set([
  "access_token", "api_key", "credential", "key", "secret", "signature", "token",
  "x-amz-credential", "x-amz-security-token", "x-amz-signature",
])

function publicUrl(value) {
  const raw = String(value || "").trim()
  if (!raw) return ""
  try {
    const parsed = new URL(raw, globalThis.location?.origin || "https://kari.invalid")
    if (parsed.username || parsed.password) return ""
    for (const [key, nestedUrl] of parsed.searchParams.entries()) {
      if (SENSITIVE_QUERY_NAMES.has(key.toLowerCase())) return ""
      if (nestedUrl.includes("://") && !publicUrl(nestedUrl)) return ""
    }
    return raw
  } catch {
    return ""
  }
}

export function normalizeCatalogText(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[\u00d7✕✖]/g, "x")
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
    .replace(/\s+/g, " ")
}

export function compactPublicCatalogItem(item) {
  const title = String(item?.title || "").trim()
  const id = String(item?.id || "").trim()
  if (!id || !title) return null
  return {
    id,
    title,
    alternative_titles: Array.from(new Set(
      (Array.isArray(item?.alternative_titles) ? item.alternative_titles : [])
        .map((value) => String(value || "").trim())
        .filter(Boolean),
    )),
    provider: String(item?.provider || ""),
    source: String(item?.source || ""),
    source_url: publicUrl(item?.source_url),
    cover_url: publicUrl(item?.cover_url),
    genres: Array.from(new Set(
      (Array.isArray(item?.genres) ? item.genres : [])
        .map((value) => String(value || "").trim())
        .filter(Boolean),
    )),
    chapter_count: Math.max(0, Number.parseInt(item?.chapter_count || 0, 10) || 0),
    latest_chapter: String(item?.latest_chapter || ""),
    catalog_home_ready: Boolean(item?.catalog_home_ready),
  }
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("IndexedDB indisponivel."))
      return
    }
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION)
    request.onerror = () => reject(request.error || new Error("Falha ao abrir cache do catalogo."))
    request.onupgradeneeded = () => {
      const database = request.result
      if (!database.objectStoreNames.contains(STORE_NAME)) {
        database.createObjectStore(STORE_NAME, { keyPath: "key" })
      }
    }
    request.onsuccess = () => resolve(request.result)
  })
}

function databaseRequest(mode, operation) {
  return openDatabase().then((database) => new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, mode)
    const request = operation(transaction.objectStore(STORE_NAME))
    transaction.oncomplete = () => {
      database.close()
      resolve(request?.result)
    }
    transaction.onerror = () => {
      database.close()
      reject(transaction.error || new Error("Falha no cache do catalogo."))
    }
    transaction.onabort = transaction.onerror
  }))
}

export async function readPublicCatalogSnapshot() {
  const snapshot = await databaseRequest("readonly", (store) => store.get(SNAPSHOT_KEY))
  if (!snapshot || !Array.isArray(snapshot.items)) return null
  return snapshot
}

export async function savePublicCatalogSnapshot(payload) {
  const items = (Array.isArray(payload?.items) ? payload.items : [])
    .map(compactPublicCatalogItem)
    .filter(Boolean)
  if (!items.length) return null
  const snapshot = {
    key: SNAPSHOT_KEY,
    version: Number(payload?.version || 0),
    savedAt: Date.now(),
    items,
  }
  await databaseRequest("readwrite", (store) => store.put(snapshot))
  return snapshot
}

function catalogMatchTier(item, query) {
  const title = normalizeCatalogText(item.title)
  const aliases = item.alternative_titles.map(normalizeCatalogText)
  const candidates = [title, ...aliases]
  if (title === query) return 0
  if (aliases.includes(query)) return 1
  if (candidates.some((candidate) => candidate.startsWith(query))) return 2
  if (candidates.some((candidate) => candidate.includes(query))) return 3
  const tokens = query.split(" ").filter(Boolean)
  if (tokens.length && candidates.some((candidate) => tokens.every((token) => candidate.includes(token)))) return 3
  return 9
}

export function buildCachedCatalogPayload(snapshot, { query = "", genre = "", limit = 32, offset = 0 } = {}) {
  if (!snapshot?.items?.length) return null
  const normalizedQuery = normalizeCatalogText(query)
  const normalizedGenre = normalizeCatalogText(genre)
  let ranked = snapshot.items
    .map((item) => ({ item, tier: normalizedQuery ? catalogMatchTier(item, normalizedQuery) : 0 }))
    .filter(({ item, tier }) => (
      (normalizedQuery ? tier < 9 : item.catalog_home_ready)
      && (!normalizedGenre || item.genres.some((value) => normalizeCatalogText(value) === normalizedGenre))
    ))
  ranked.sort((left, right) => left.tier - right.tier || left.item.title.localeCompare(right.item.title))
  const allItems = ranked.map(({ item }) => item)
  const items = allItems.slice(offset, offset + limit)
  return {
    items,
    sections: [{ title: normalizedQuery ? "Resultados salvos" : "Catalogo salvo", items }],
    total: allItems.length,
    limit,
    offset,
    sources: Array.from(new Set(items.map((item) => item.source).filter(Boolean))),
    cached: true,
    refreshing: true,
    browserCached: true,
    savedAt: snapshot.savedAt,
  }
}
