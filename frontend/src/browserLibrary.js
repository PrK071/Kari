const DATABASE_NAME = "kari-browser-library"
const STORE_NAME = "works"
const DATABASE_VERSION = 1

const HQ_EXTENSIONS = new Set(["cbz", "zip", "pdf", "jpg", "jpeg", "png", "webp", "gif", "avif"])
const NOVEL_EXTENSIONS = new Set(["epub", "txt", "md", "html", "htm", "pdf"])

export function fileExtension(name) {
  const match = String(name || "").toLowerCase().match(/\.([^.]+)$/)
  return match?.[1] || ""
}

export function browserImportAccepts(kind, name) {
  const extension = fileExtension(name)
  return (kind === "novels" ? NOVEL_EXTENSIONS : HQ_EXTENSIONS).has(extension)
}

export function titleFromFilename(name) {
  return String(name || "")
    .replace(/\.[^.]+$/, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim() || "Obra importada"
}

export function naturalFileCompare(left, right) {
  return String(left || "").localeCompare(String(right || ""), undefined, {
    numeric: true,
    sensitivity: "base",
  })
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("Este navegador nao oferece armazenamento local de arquivos."))
      return
    }
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION)
    request.onerror = () => reject(request.error || new Error("Nao consegui abrir a biblioteca local."))
    request.onupgradeneeded = () => {
      const database = request.result
      if (!database.objectStoreNames.contains(STORE_NAME)) {
        const store = database.createObjectStore(STORE_NAME, { keyPath: "id" })
        store.createIndex("kind", "kind", { unique: false })
      }
    }
    request.onsuccess = () => resolve(request.result)
  })
}

function databaseRequest(mode, operation) {
  return openDatabase().then((database) => new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, mode)
    const store = transaction.objectStore(STORE_NAME)
    let result
    try {
      result = operation(store)
    } catch (error) {
      database.close()
      reject(error)
      return
    }
    transaction.oncomplete = () => {
      database.close()
      resolve(result?.result)
    }
    transaction.onerror = () => {
      database.close()
      reject(transaction.error || new Error("Falha no armazenamento local."))
    }
    transaction.onabort = transaction.onerror
  }))
}

export async function listBrowserWorks(kind) {
  const values = await databaseRequest("readonly", (store) => store.getAll())
  return (Array.isArray(values) ? values : [])
    .filter((item) => item?.kind === kind)
    .sort((left, right) => Number(right.createdAt || 0) - Number(left.createdAt || 0))
}

export async function saveBrowserWork({ kind, title, secondary, files }) {
  const selectedFiles = Array.from(files || [])
  if (!selectedFiles.length) throw new Error("Selecione pelo menos um arquivo.")
  const invalid = selectedFiles.find((file) => !browserImportAccepts(kind, file.name))
  if (invalid) throw new Error(`Formato nao suportado: ${invalid.name}`)
  if (kind === "novels" && selectedFiles.length !== 1) {
    throw new Error("Importe uma webnovel por vez.")
  }
  const now = Date.now()
  const id = globalThis.crypto?.randomUUID?.() || `${now}-${Math.random().toString(16).slice(2)}`
  const record = {
    id,
    kind,
    title: String(title || "").trim() || titleFromFilename(selectedFiles[0].name),
    secondary: String(secondary || "").trim(),
    createdAt: now,
    files: selectedFiles.map((file) => ({
      name: file.name,
      type: file.type || "application/octet-stream",
      blob: file,
    })),
  }
  await databaseRequest("readwrite", (store) => store.put(record))
  return record
}

export function deleteBrowserWork(id) {
  return databaseRequest("readwrite", (store) => store.delete(String(id || "")))
}
