import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import JSZip from "jszip"
import { BookText, FileArchive, Loader2, Trash2, Upload, X } from "lucide-react"

import {
  deleteBrowserWork,
  fileExtension,
  listBrowserWorks,
  naturalFileCompare,
  saveBrowserWork,
} from "./browserLibrary.js"

const IMAGE_PATTERN = /\.(?:avif|gif|jpe?g|png|webp)$/i

function htmlText(value) {
  const documentValue = new DOMParser().parseFromString(String(value || ""), "text/html")
  return (documentValue.body?.innerText || documentValue.body?.textContent || "")
    .replace(/\r\n?/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

function resolveArchivePath(basePath, relativePath) {
  const base = String(basePath || "").split("/").slice(0, -1).join("/")
  const stack = base ? base.split("/") : []
  for (const part of String(relativePath || "").split("/")) {
    if (!part || part === ".") continue
    if (part === "..") stack.pop()
    else stack.push(part)
  }
  return stack.join("/")
}

async function readEpub(blob) {
  const archive = await JSZip.loadAsync(blob)
  const container = await archive.file("META-INF/container.xml")?.async("string")
  if (!container) throw new Error("EPUB sem arquivo de estrutura.")
  const containerDocument = new DOMParser().parseFromString(container, "application/xml")
  const packagePath = containerDocument.getElementsByTagName("rootfile")[0]?.getAttribute("full-path") || ""
  const packageFile = archive.file(packagePath)
  if (!packageFile) throw new Error("EPUB sem pacote de conteudo.")
  const packageDocument = new DOMParser().parseFromString(await packageFile.async("string"), "application/xml")
  const manifest = new Map()
  for (const item of packageDocument.getElementsByTagName("item")) {
    manifest.set(item.getAttribute("id"), item.getAttribute("href"))
  }
  const orderedPaths = Array.from(packageDocument.getElementsByTagName("itemref"))
    .map((item) => manifest.get(item.getAttribute("idref")))
    .filter(Boolean)
    .map((href) => resolveArchivePath(packagePath, href))
  const fallbackPaths = Object.keys(archive.files).filter((name) => /\.(?:x?html?|htm)$/i.test(name))
  const paths = orderedPaths.length ? orderedPaths : fallbackPaths.sort(naturalFileCompare)
  const sections = []
  for (const path of paths) {
    const entry = archive.file(path)
    if (!entry) continue
    const text = htmlText(await entry.async("string"))
    if (text) sections.push(text)
  }
  if (!sections.length) throw new Error("Nao encontrei texto legivel neste EPUB.")
  return sections.join("\n\n•••\n\n")
}

async function openStoredWork(work) {
  const files = Array.isArray(work?.files) ? work.files : []
  const first = files[0]
  if (!first?.blob) throw new Error("O arquivo desta obra nao esta mais disponivel.")
  const extension = fileExtension(first.name)
  if (extension === "pdf") {
    const url = URL.createObjectURL(first.blob)
    return { mode: "pdf", url, objectUrls: [url] }
  }
  if (work.kind === "hq") {
    if (extension === "cbz" || extension === "zip") {
      const archive = await JSZip.loadAsync(first.blob)
      const entries = Object.values(archive.files)
        .filter((entry) => !entry.dir && IMAGE_PATTERN.test(entry.name))
        .sort((left, right) => naturalFileCompare(left.name, right.name))
      if (!entries.length) throw new Error("Nao encontrei imagens no arquivo.")
      const pages = []
      for (const entry of entries) {
        pages.push(URL.createObjectURL(await entry.async("blob")))
      }
      return { mode: "images", pages, objectUrls: pages }
    }
    const pages = files
      .filter((file) => IMAGE_PATTERN.test(file.name))
      .sort((left, right) => naturalFileCompare(left.name, right.name))
      .map((file) => URL.createObjectURL(file.blob))
    if (!pages.length) throw new Error("Nao encontrei imagens para abrir.")
    return { mode: "images", pages, objectUrls: pages }
  }
  const text = extension === "epub"
    ? await readEpub(first.blob)
    : extension === "html" || extension === "htm"
      ? htmlText(await first.blob.text())
      : await first.blob.text()
  if (!text.trim()) throw new Error("O arquivo nao possui texto legivel.")
  return { mode: "text", text, objectUrls: [] }
}

function LocalReader({ work, onClose }) {
  const [content, setContent] = useState(null)
  const [error, setError] = useState("")

  useEffect(() => {
    let cancelled = false
    let urls = []
    setContent(null)
    setError("")
    openStoredWork(work)
      .then((result) => {
        urls = result.objectUrls || []
        if (!cancelled) setContent(result)
      })
      .catch((cause) => { if (!cancelled) setError(cause.message || "Nao consegui abrir a obra.") })
    return () => {
      cancelled = true
      for (const url of urls) URL.revokeObjectURL(url)
    }
  }, [work])

  const paragraphs = useMemo(
    () => String(content?.text || "").split(/\n\s*\n+/).map((value) => value.trim()).filter(Boolean),
    [content?.text],
  )

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-app" role="dialog" aria-modal="true" aria-label={`Leitor de ${work.title}`}>
      <header className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-line bg-app/95 px-4 py-3 backdrop-blur">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-black text-zinc-100">{work.title}</h2>
          <p className="text-[11px] text-muted">Arquivo deste aparelho</p>
        </div>
        <button type="button" onClick={onClose} className="grid h-9 w-9 place-items-center rounded border border-line text-zinc-300 hover:border-accent hover:text-accent" aria-label="Fechar leitor">
          <X size={17} aria-hidden="true" />
        </button>
      </header>
      {!content && !error && <div className="grid min-h-[70vh] place-items-center text-accent"><Loader2 size={28} className="animate-spin" /></div>}
      {error && <p className="mx-auto mt-8 max-w-2xl rounded border border-red-900 bg-red-950/30 px-4 py-3 text-sm text-red-200">{error}</p>}
      {content?.mode === "pdf" && <iframe title={work.title} src={content.url} className="h-[calc(100vh-65px)] w-full border-0 bg-white" />}
      {content?.mode === "images" && (
        <main className="mx-auto flex max-w-5xl flex-col items-center bg-black py-3">
          {content.pages.map((src, index) => <img key={src} src={src} alt={`Pagina ${index + 1}`} className="h-auto max-w-full" />)}
        </main>
      )}
      {content?.mode === "text" && (
        <article className="mx-auto max-w-3xl px-5 py-10 text-[17px] leading-8 text-zinc-200 sm:px-8">
          {paragraphs.map((paragraph, index) => <p key={`${index}-${paragraph.slice(0, 20)}`} className="mb-6 whitespace-pre-wrap">{paragraph}</p>)}
        </article>
      )}
    </div>
  )
}

export default function BrowserLibraryPage({ kind }) {
  const isNovel = kind === "novels"
  const Icon = isNovel ? BookText : FileArchive
  const [items, setItems] = useState([])
  const [title, setTitle] = useState("")
  const [secondary, setSecondary] = useState("")
  const [files, setFiles] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [opened, setOpened] = useState(null)
  const inputRef = useRef(null)

  const load = useCallback(async () => {
    try {
      setItems(await listBrowserWorks(kind))
    } catch (cause) {
      setError(cause.message || "Nao consegui abrir a biblioteca deste aparelho.")
    }
  }, [kind])

  useEffect(() => { void load() }, [load])

  const importWork = async (event) => {
    event.preventDefault()
    if (!files.length || busy) return
    setBusy(true)
    setError("")
    try {
      await saveBrowserWork({ kind, title, secondary, files })
      setTitle("")
      setSecondary("")
      setFiles([])
      if (inputRef.current) inputRef.current.value = ""
      await load()
    } catch (cause) {
      setError(cause.message || "Nao consegui importar o arquivo.")
    } finally {
      setBusy(false)
    }
  }

  const remove = async (item) => {
    if (!window.confirm(`Remover "${item.title}" deste aparelho?`)) return
    try {
      await deleteBrowserWork(item.id)
      setItems((current) => current.filter((entry) => entry.id !== item.id))
    } catch (cause) {
      setError(cause.message || "Nao consegui remover a obra.")
    }
  }

  return (
    <main className="mx-auto min-h-[calc(100vh-72px)] w-full max-w-[1600px] px-4 py-6">
      <section aria-labelledby={`${kind}-device-title`}>
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
          <div className="flex items-center gap-3">
            <Icon size={22} className="text-accent" aria-hidden="true" />
            <div>
              <h1 id={`${kind}-device-title`} className="text-xl font-black text-zinc-50">{isNovel ? "Webnovels do aparelho" : "HQs do aparelho"}</h1>
              <p className="text-xs text-muted">{items.length} {items.length === 1 ? "obra" : "obras"} · arquivos nao sao enviados ao servidor</p>
            </div>
          </div>
          <span className="rounded border border-accent/25 bg-accent/10 px-2 py-1 text-[11px] font-semibold text-accent">Neste aparelho</span>
        </header>

        <form onSubmit={importWork} className="mt-4 grid gap-3 border-b border-line/70 pb-5 md:grid-cols-[1fr_180px_1.4fr_auto] md:items-end">
          <label className="text-xs font-semibold text-zinc-300">Titulo
            <input value={title} maxLength={180} onChange={(event) => setTitle(event.target.value)} placeholder="Detectar pelo arquivo" className="mt-1.5 h-10 w-full rounded border border-line bg-app px-3 text-sm text-zinc-100 outline-none focus:border-accent" />
          </label>
          <label className="text-xs font-semibold text-zinc-300">{isNovel ? "Autor" : "Edicao"}
            <input value={secondary} maxLength={180} onChange={(event) => setSecondary(event.target.value)} placeholder="Opcional" className="mt-1.5 h-10 w-full rounded border border-line bg-app px-3 text-sm text-zinc-100 outline-none focus:border-accent" />
          </label>
          <label className="text-xs font-semibold text-zinc-300">Arquivo
            <input ref={inputRef} type="file" required multiple={!isNovel} accept={isNovel ? ".epub,.txt,.md,.html,.htm,.pdf" : ".cbz,.zip,.pdf,image/*"} onChange={(event) => setFiles(Array.from(event.target.files || []))} className="mt-1.5 block h-10 w-full rounded border border-line bg-app text-xs text-zinc-300 file:mr-3 file:h-full file:border-0 file:border-r file:border-line file:bg-soft file:px-3 file:text-xs file:font-semibold file:text-zinc-100" />
          </label>
          <button type="submit" disabled={!files.length || busy} className="flex h-10 items-center justify-center gap-2 rounded bg-accent px-4 text-sm font-bold text-black transition hover:bg-accent-dim disabled:cursor-wait disabled:opacity-50">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />} Importar
          </button>
        </form>

        <p className="mt-3 text-xs text-muted">{isNovel ? "Formatos: EPUB, TXT, Markdown, HTML e PDF." : "Formatos: CBZ/ZIP, PDF ou uma sequencia de imagens."}</p>
        {error && <p className="mt-4 rounded border border-red-900 bg-red-950/30 px-3 py-2 text-sm text-red-200">{error}</p>}
        <div className="grid grid-cols-1 gap-2 py-5 lg:grid-cols-2">
          {items.map((item) => (
            <div key={item.id} className="flex min-h-24 items-center gap-3 rounded border border-line bg-panel p-3">
              <div className="grid h-16 w-12 shrink-0 place-items-center rounded bg-soft text-accent"><Icon size={22} /></div>
              <button type="button" onClick={() => setOpened(item)} className="min-w-0 flex-1 text-left">
                <p className="truncate text-sm font-black text-zinc-100 hover:text-accent">{item.title}</p>
                <p className="mt-1 truncate text-xs text-muted">{item.secondary || item.files?.[0]?.name}</p>
                <p className="mt-1 text-[11px] text-zinc-500">Toque para ler</p>
              </button>
              <button type="button" onClick={() => remove(item)} className="grid h-8 w-8 shrink-0 place-items-center rounded border border-red-900/70 text-red-300 hover:border-red-500" aria-label={`Remover ${item.title}`}><Trash2 size={14} /></button>
            </div>
          ))}
        </div>
        {!items.length && !error && <div className="grid min-h-44 place-items-center text-center text-muted"><div><Icon size={30} className="mx-auto text-zinc-600" /><p className="mt-3 text-sm font-semibold text-zinc-300">Biblioteca vazia</p><p className="mt-1 text-xs">Escolha um arquivo do computador ou celular.</p></div></div>}
      </section>
      {opened && <LocalReader work={opened} onClose={() => setOpened(null)} />}
    </main>
  )
}
