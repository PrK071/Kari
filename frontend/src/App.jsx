import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { FixedSizeGrid as Grid } from "react-window"
import { ArrowUpDown, BookOpen, BookText, Camera, ExternalLink, FileArchive, Grid2X2, Heart, History, Home, ImagePlus, LibraryBig, Link2, Loader2, PanelLeftClose, PanelLeftOpen, Puzzle, Search, Trash2, Unlink, Upload, UserRound, X } from "lucide-react"
import MangaCard, { MangaCardSkeleton } from "./components/MangaCard.jsx"

const API_BASE_URL = import.meta.env.VITE_DESKTOP_BUILD === "1"
  ? window.location.origin
  : (import.meta.env.VITE_API_BASE_URL || window.location.origin)
const CARD_WIDTH = 380
const CARD_HEIGHT = 180
const GRID_GAP = 8
const OVERSCAN_ROWS = 2
const APP_NAME = "Kari"
const CHAPTER_LOADER_DELAY_MS = 500
const CHAPTER_LOADER_SRC = "/marcille-chapter-loader-transparent-v3.gif"
const READER_LOADER_SRC = "/samurai-run-transparent.gif"
const CATALOG_PAGE_SIZE = 32
const CATALOG_MAX_LIMIT = 200
const FAVORITES_STORAGE_KEY = "kari:favorites:v1"
const HISTORY_STORAGE_KEY = "kari:history:v1"
const PROFILE_STORAGE_KEY = "kari:profile-id:v1"
const AUTH_TOKEN_KEY = "kari:auth-token:v1"
const READER_SESSION_STORAGE_KEY = "kari:reader-session:v1"

const HERO_GENRE_STYLES = [
  "border-emerald-300/30 bg-emerald-300/10 text-emerald-100",
  "border-violet-300/30 bg-violet-300/10 text-violet-100",
  "border-cyan-300/30 bg-cyan-300/10 text-cyan-100",
  "border-amber-200/30 bg-amber-200/10 text-amber-100",
]

function resolveApiUrl(url) {
  if (!url) return ""
  return url.startsWith("/") ? `${API_BASE_URL}${url}` : url
}

function resolveReaderContentImage(url) {
  if (!url) return ""
  if (/^https?:\/\//i.test(url)) {
    return `${API_BASE_URL}/api/image?url=${encodeURIComponent(url)}`
  }
  return resolveApiUrl(url)
}

function isVideoUrl(url) {
  return /\.(mp4|webm|mov|m4v)(\?|$)/i.test(url || "")
}

const LANG_LABEL = {
  "pt-br": "PT", pt: "PT", en: "EN", es: "ES", "es-la": "ES", ja: "JP",
  ko: "KR", zh: "ZH", "zh-hk": "ZH", fr: "FR", de: "DE", it: "IT", ru: "RU",
  id: "ID", th: "TH", vi: "VI", pl: "PL", tr: "TR", ar: "AR", uk: "UK",
}
function langLabel(lang) {
  return LANG_LABEL[lang] ?? String(lang || "").toUpperCase().slice(0, 2)
}

function preferredChapterLanguage(languages) {
  const normalized = (languages ?? []).map((lang) => String(lang).toLowerCase())
  return normalized.find((lang) => lang === "pt-br" || lang === "pt") ?? normalized[0] ?? "pt-br"
}

function useElementSize() {
  const ref = useRef(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  useEffect(() => {
    if (!ref.current) return undefined
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setSize({ width, height })
    })
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [])

  return [ref, size]
}

function mangaStorageKey(manga) {
  return String(manga?.source_url || manga?.id || manga?.title || "")
}

function mangaTitleKey(manga) {
  return String(manga?.title || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
}

function mangaTitleAliasKey(manga) {
  // AniList pode omitir "Part 2" enquanto MangaDex inclui. O subtitulo (ex:
  // Sentou Chouryuu) permanece, entao nao mistura partes diferentes de JoJo.
  return mangaTitleKey(manga)
    .replace(/\b(?:part|parte)\s*(?:\d+|[ivxlcdm]+)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

function readStoredMangaList(key) {
  try {
    const value = JSON.parse(window.localStorage.getItem(key) || "[]")
    return Array.isArray(value) ? value.filter((item) => mangaStorageKey(item)) : []
  } catch {
    return []
  }
}

function mergeMangaLists(...lists) {
  const seen = new Set()
  const result = []
  for (const list of lists) {
    for (const manga of list ?? []) {
      const key = mangaStorageKey(manga)
      if (!key || seen.has(key)) continue
      seen.add(key)
      result.push(manga)
    }
  }
  return result
}

function Header({ query, onQueryChange, onHome, onCatalog, onPluginSelect, onHistory, onFavorites, onProfile, profile, libraryView, pluginView, activeGenre, onClearGenre, total, isSearching, collapsed, hidden, onReveal, onRequestHide }) {
  const [pluginsOpen, setPluginsOpen] = useState(false)
  const pluginsRef = useRef(null)
  const pluginActive = pluginView === "hq" || pluginView === "hq-local" || pluginView === "novels"

  useEffect(() => {
    const closeMenu = (event) => {
      if (!pluginsRef.current?.contains(event.target)) setPluginsOpen(false)
    }
    document.addEventListener("pointerdown", closeMenu)
    return () => document.removeEventListener("pointerdown", closeMenu)
  }, [])

  useEffect(() => setPluginsOpen(false), [pluginView])

  const countLabel = libraryView === "history"
    ? `${total} obras no historico`
    : libraryView === "favorites"
      ? `${total} favoritos`
      : libraryView === "library"
        ? `${total} obras na minha lista`
      : pluginView === "hq"
        ? "HQ Now"
        : pluginView === "hq-local"
          ? "Biblioteca local de HQs"
        : pluginView === "novels"
          ? "Biblioteca de Web Novels"
      : activeGenre
    ? `${total} obras em ${activeGenre}`
    : isSearching ? `${total} resultados` : `${total} obras no catalogo`
  return (
    <header
      onMouseEnter={onReveal}
      onMouseLeave={onRequestHide}
      className={`sticky top-0 z-30 border-b border-line/40 bg-app/40 px-4 backdrop-blur-md transition-[transform,opacity,padding] duration-[450ms] ease-[cubic-bezier(0.16,1,0.3,1)] ${collapsed ? "py-1.5" : "py-3"} ${hidden ? "pointer-events-none -translate-y-full opacity-0" : "translate-y-0 opacity-100"}`}
    >
      <div className="mx-auto w-full max-w-[1600px]">
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
          <nav aria-label="Navegacao principal" className="flex items-center gap-1.5">
            <button
              type="button"
              aria-label="Pagina inicial"
              title="Pagina inicial"
              onClick={onHome}
              className="flex h-9 items-center gap-2 rounded-md px-2 text-xs font-semibold text-zinc-400 transition hover:bg-soft hover:text-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            >
              <Home size={18} strokeWidth={1.8} aria-hidden="true" />
              <span className={`hidden overflow-hidden whitespace-nowrap align-middle transition-all duration-[450ms] ease-[cubic-bezier(0.16,1,0.3,1)] lg:inline-block ${collapsed ? "max-w-0 opacity-0" : "max-w-[80px] opacity-100"}`}>Inicio</span>
            </button>
            <button
              type="button"
              aria-label="Catalogo completo"
              title="Catalogo completo"
              onClick={onCatalog}
              className="flex h-9 items-center gap-2 rounded-md px-2 text-xs font-semibold text-zinc-400 transition hover:bg-soft hover:text-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            >
              <LibraryBig size={18} strokeWidth={1.8} aria-hidden="true" />
              <span className={`hidden overflow-hidden whitespace-nowrap align-middle transition-all duration-[450ms] ease-[cubic-bezier(0.16,1,0.3,1)] lg:inline-block ${collapsed ? "max-w-0 opacity-0" : "max-w-[80px] opacity-100"}`}>Catalogo</span>
            </button>
            <div ref={pluginsRef} className="relative">
              <button
                type="button"
                aria-label="Plugins"
                aria-expanded={pluginsOpen}
                aria-haspopup="menu"
                title="Plugins"
                onClick={() => setPluginsOpen((open) => !open)}
                className={`flex h-9 items-center gap-2 rounded-md px-2 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent ${
                  pluginActive ? "bg-soft text-accent" : "text-zinc-400 hover:bg-soft hover:text-accent"
                }`}
              >
                <Puzzle size={18} strokeWidth={1.8} aria-hidden="true" />
                <span className={`hidden overflow-hidden whitespace-nowrap align-middle transition-all duration-[450ms] ease-[cubic-bezier(0.16,1,0.3,1)] lg:inline-block ${collapsed ? "max-w-0 opacity-0" : "max-w-[80px] opacity-100"}`}>Plugins</span>
              </button>
              {pluginsOpen && (
                <div role="menu" className="absolute left-0 top-11 z-30 w-52 rounded-md border border-line bg-panel p-1 shadow-2xl">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => onPluginSelect("hq")}
                    className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm font-semibold text-zinc-200 transition hover:bg-soft hover:text-accent"
                  >
                    <FileArchive size={16} aria-hidden="true" /> HQs
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => onPluginSelect("novels")}
                    className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm font-semibold text-zinc-200 transition hover:bg-soft hover:text-accent"
                  >
                    <BookText size={16} aria-hidden="true" /> Web Novels
                  </button>
                </div>
              )}
            </div>
            <button
              type="button"
              aria-label="Historico de leitura"
              title="Historico"
              onClick={onHistory}
              className={`flex h-9 items-center gap-2 rounded-md px-2 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent ${
                libraryView === "history" ? "bg-soft text-accent" : "text-zinc-400 hover:bg-soft hover:text-accent"
              }`}
            >
              <History size={18} strokeWidth={1.8} aria-hidden="true" />
              <span className={`hidden overflow-hidden whitespace-nowrap align-middle transition-all duration-[450ms] ease-[cubic-bezier(0.16,1,0.3,1)] lg:inline-block ${collapsed ? "max-w-0 opacity-0" : "max-w-[80px] opacity-100"}`}>Historico</span>
            </button>
            <button
              type="button"
              aria-label="Favoritos"
              title="Favoritos"
              onClick={onFavorites}
              className={`flex h-9 items-center gap-2 rounded-md px-2 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent ${
                libraryView === "favorites" ? "bg-soft text-accent" : "text-zinc-400 hover:bg-soft hover:text-accent"
              }`}
            >
              <Heart size={18} strokeWidth={1.8} aria-hidden="true" />
              <span className={`hidden overflow-hidden whitespace-nowrap align-middle transition-all duration-[450ms] ease-[cubic-bezier(0.16,1,0.3,1)] lg:inline-block ${collapsed ? "max-w-0 opacity-0" : "max-w-[80px] opacity-100"}`}>Favoritos</span>
            </button>
          </nav>
          <button
            type="button"
            aria-label="Voltar para a homepage"
            title="Voltar para a homepage"
            onClick={onHome}
            className="text-xl font-black text-accent transition hover:text-accent-dim focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
          >
            {APP_NAME}
          </button>
          <div className="flex min-w-0 items-center justify-self-end gap-2">
            {!collapsed && (
              <>
                <span className="hidden text-xs text-muted md:inline">{countLabel}</span>
                <Grid2X2 size={17} strokeWidth={1.8} className="text-zinc-500" aria-hidden="true" />
              </>
            )}
          {activeGenre && (
            <button
              type="button"
              onClick={onClearGenre}
              title="Limpar filtro de genero"
              className="flex max-w-32 items-center gap-1 rounded border border-emerald-300/25 bg-emerald-300/10 px-2 py-1 text-[10px] font-semibold text-emerald-100 transition hover:border-emerald-200/60"
            >
              <span className="truncate">{activeGenre}</span>
              <X size={12} strokeWidth={2} aria-hidden="true" />
            </button>
          )}
            <button
              type="button"
              aria-label="Perfil"
              title="Perfil"
              onClick={onProfile}
              className="flex h-9 items-center gap-2 rounded-md px-2 text-xs font-semibold text-zinc-400 transition hover:bg-soft hover:text-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            >
              {profile?.avatar_url
                ? <img src={resolveApiUrl(profile.avatar_url)} alt="" className="h-6 w-6 rounded-full object-cover" />
                : <UserRound size={18} strokeWidth={1.8} aria-hidden="true" />}
              <span className="hidden lg:inline truncate max-w-24">{profile?.display_name || "Perfil"}</span>
            </button>
          </div>
        </div>
        <div className={`overflow-hidden transition-all duration-[450ms] ease-[cubic-bezier(0.16,1,0.3,1)] ${collapsed ? "max-h-0 opacity-0" : "mt-3 max-h-16 opacity-100"}`}>
        <div className="relative mx-auto w-full max-w-xl">
          <Search size={18} strokeWidth={1.8} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Buscar manga, manhwa ou novel"
            className="h-10 w-full rounded-md border border-line bg-panel px-10 pr-10 text-sm text-zinc-100 outline-none transition placeholder:text-zinc-500 focus:border-emerald-300/45 focus:ring-1 focus:ring-emerald-300/20"
          />
          {query && (
            <button
              type="button"
              aria-label="Limpar busca"
              onClick={() => onQueryChange("")}
              className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded text-zinc-500 transition hover:bg-soft hover:text-zinc-100"
            >
              <X size={16} strokeWidth={2} aria-hidden="true" />
            </button>
          )}
        </div>
        </div>
      </div>
    </header>
  )
}

function VirtualMangaGrid({ mangas, onSelect, onLoadMore, canLoadMore = false, loadingMore = false }) {
  const [containerRef, size] = useElementSize()
  const columnCount = Math.max(1, Math.floor((size.width + GRID_GAP) / (CARD_WIDTH + GRID_GAP)))
  const rowCount = Math.ceil(mangas.length / columnCount)
  const gridHeight = Math.max(420, size.height)

  const itemData = useMemo(
    () => ({ mangas, columnCount }),
    [mangas, columnCount],
  )

  const Cell = useCallback(({ columnIndex, rowIndex, style, data }) => {
    const index = rowIndex * data.columnCount + columnIndex
    const manga = data.mangas[index]
    if (!manga) return null

    return (
      <div
        style={{
          ...style,
          left: Number(style.left) + GRID_GAP / 2,
          top: Number(style.top) + GRID_GAP / 2,
          width: Number(style.width) - GRID_GAP,
          height: Number(style.height) - GRID_GAP,
        }}
      >
        <MangaCard manga={manga} priority={index < 16} onSelect={onSelect} />
      </div>
    )
  }, [])

  return (
    <main className="flex h-[calc(100vh-124px)] flex-col items-center px-5 py-5">
      <div ref={containerRef} className="min-h-0 w-full max-w-[1600px] flex-1">
        {size.width > 0 && (
          <Grid
            columnCount={columnCount}
            columnWidth={Math.floor(size.width / columnCount)}
            height={gridHeight}
            rowCount={rowCount}
            rowHeight={CARD_HEIGHT}
            width={size.width}
            itemData={itemData}
            overscanRowCount={OVERSCAN_ROWS}
            className="scrollbar-thin scrollbar-thumb-zinc-700 scrollbar-track-app"
          >
            {Cell}
          </Grid>
        )}
      </div>
      {canLoadMore && (
        <div className="flex shrink-0 justify-center pt-4">
          <button
            type="button"
            onClick={onLoadMore}
            disabled={loadingMore}
            className="rounded border border-zinc-700 bg-zinc-900 px-5 py-2 text-sm font-semibold text-zinc-100 transition hover:border-zinc-400 hover:bg-zinc-800 disabled:cursor-wait disabled:opacity-60"
          >
            {loadingMore ? "Carregando..." : "Carregar mais"}
          </button>
        </div>
      )}
    </main>
  )
}

const SOURCE_DOMAINS = {
  "MangaDex": "mangadex.org",
  "MangaLivre": "mangalivre.net",
  "MangasBrasuka": "mangasbrasuka.com.br",
  "MangaGeek": "geekstations.com.br",
  "MangaKatana": "mangakatana.com",
  "Nexus Mangas": "nexusmangas.com",
  "Fliptru": "fliptru.com.br",
  "AniList": "anilist.co",
}

const SOURCE_LOGOS = {
  "MangaGeek": "/source-mangageek.png",
  "MangaLivre": "/source-mangalivre.png",
}

function sourceLogoUrl(source) {
  const localLogo = SOURCE_LOGOS[String(source || "")]
  if (localLogo) return localLogo
  const domain = SOURCE_DOMAINS[String(source || "")]
  return domain ? `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64` : ""
}

function SearchSourceColumns({ mangas, onSelect }) {
  const groups = useMemo(() => {
    const grouped = new Map()
    for (const manga of mangas) {
      const source = String(manga.source || "Fonte desconhecida")
      const current = grouped.get(source) ?? []
      current.push(manga)
      grouped.set(source, current)
    }
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b, "pt-BR"))
  }, [mangas])

  return (
    <main className="h-[calc(100vh-124px)] px-5 py-5">
      <div className="mx-auto h-full w-full max-w-[1600px] overflow-x-auto pb-3 scrollbar-thin scrollbar-thumb-zinc-700 scrollbar-track-app">
        <div className="flex min-h-full w-max min-w-full items-start gap-4">
          {groups.map(([source, items]) => (
            <section key={source} className="w-[380px] shrink-0">
              <div className="mb-3 flex items-center gap-2 border-b border-line/70 px-1 pb-2">
                {sourceLogoUrl(source) ? (
                  <img
                    src={sourceLogoUrl(source)}
                    alt=""
                    width="24"
                    height="24"
                    className="h-6 w-6 rounded object-contain"
                    loading="eager"
                  />
                ) : (
                  <span className="grid h-6 w-6 place-items-center rounded bg-soft text-xs font-black text-emerald-200">
                    {source.slice(0, 1).toUpperCase()}
                  </span>
                )}
                <h2 className="truncate text-sm font-bold text-zinc-100">{source}</h2>
                <span className="ml-auto text-xs text-zinc-500">{items.length}</span>
              </div>
              <div className="space-y-2">
                {items.map((manga, index) => (
                  <MangaCard
                    key={`${source}-${manga.source_url ?? manga.id ?? manga.title}-${index}`}
                    manga={manga}
                    priority={index < 3}
                    onSelect={onSelect}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </main>
  )
}

function SearchEmptyState({ query }) {
  return (
    <main className="grid min-h-[calc(100vh-124px)] place-items-center px-5 py-12">
      <div className="max-w-md text-center">
        <h2 className="text-xl font-black text-zinc-100">Nenhum resultado</h2>
        <p className="mt-2 text-sm text-muted">
          Busca por "{query}" nao encontrou obra nas fontes ativas.
        </p>
      </div>
    </main>
  )
}

function MangaSection({ title, items, sectionIndex, onSelect }) {
  if (!items?.length) return null

  return (
    <section className="mx-auto w-full max-w-[1600px] px-4 py-4">
      <div className="mb-3 flex items-center gap-3">
        <span className="h-5 w-1 rounded-sm bg-accent" />
        <h2 className="text-base font-bold text-zinc-50">{title}</h2>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {items.map((manga, index) => (
          <MangaCard
            key={`${title}-${manga.source_url ?? manga.id ?? manga.title}-${index}`}
            manga={manga}
            priority={sectionIndex === 0 && index < 8}
            onSelect={onSelect}
          />
        ))}
      </div>
    </section>
  )
}

function HeroCarousel({ items, onSelect, onGenreSelect }) {
  const list = (items ?? []).slice(0, 8)
  const [idx, setIdx] = useState(0)
  useEffect(() => {
    setIdx(0)
  }, [items])
  useEffect(() => {
    if (list.length <= 1) return undefined
    const t = setInterval(() => setIdx((i) => (i + 1) % list.length), 6000)
    return () => clearInterval(t)
  }, [list.length])
  if (!list.length) return null
  const manga = list[idx]
  const cover = resolveApiUrl(manga.cover_path || manga.cover_url)
  const go = (d) => setIdx((i) => (i + d + list.length) % list.length)
  return (
    <section className="mx-auto mt-5 w-full max-w-[1600px] px-4">
      <div className="relative min-h-[286px] overflow-hidden rounded-lg border border-line/60 bg-panel/40 shadow-card backdrop-blur-md sm:min-h-[326px]">
      {cover && (
        <img
          src={cover}
          alt=""
          aria-hidden="true"
          className="absolute inset-0 h-full w-full scale-110 object-cover opacity-35 blur-2xl"
        />
      )}
      <div className="absolute inset-0 bg-gradient-to-r from-app/70 via-app/40 to-transparent" />
      <div className="absolute inset-0 bg-gradient-to-t from-app/60 via-transparent to-transparent" />
      <div className="relative flex min-h-[286px] items-center gap-5 p-5 sm:min-h-[326px] sm:gap-7 sm:p-7">
        {cover && (
          <img
            src={cover}
            alt={`Capa de ${manga.title}`}
            className="h-52 w-36 shrink-0 rounded-md border border-white/15 object-cover shadow-glow sm:h-64 sm:w-44"
            loading="eager"
            decoding="async"
          />
        )}
        <div className="min-w-0">
          <span className="text-[10px] font-black uppercase tracking-wide text-accent">
            Em alta
          </span>
          <h2 className="mt-1 line-clamp-2 max-w-3xl text-2xl font-black leading-7 text-zinc-50 sm:text-3xl sm:leading-9">
            {manga.title}
          </h2>
          <div className="mt-2 flex flex-wrap gap-2">
            {manga.genres?.slice(0, 4).map((genre, index) => (
              <button
                key={genre}
                type="button"
                onClick={() => onGenreSelect?.(genre)}
                title={`Ver obras de ${genre}`}
                className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${HERO_GENRE_STYLES[index % HERO_GENRE_STYLES.length]}`}
              >
                {genre}
              </button>
            ))}
          </div>
          {manga.description && (
            <p className="mt-3 line-clamp-3 max-w-3xl text-sm leading-6 text-zinc-300">{manga.description}</p>
          )}
          <button
            type="button"
            onClick={() => onSelect?.(manga)}
            className="mt-4 inline-flex items-center gap-2 rounded-md border border-emerald-300/40 bg-emerald-300/10 px-4 py-2 text-xs font-bold text-emerald-100 transition hover:border-emerald-200 hover:bg-emerald-300/20"
          >
            <BookOpen size={15} strokeWidth={1.8} aria-hidden="true" />
            Abrir
          </button>
        </div>
      </div>
      {list.length > 1 && (
        <>
          <button
            type="button"
            onClick={() => go(-1)}
            aria-label="Anterior"
            className="hidden"
          >
            <span className="text-zinc-100">{"<"}</span>
            ‹
          </button>
          <button
            type="button"
            onClick={() => go(1)}
            aria-label="Proximo"
            className="hidden"
          >
            <span className="text-zinc-100">{">"}</span>
            ›
          </button>
          <div className="absolute bottom-3 left-1/2 flex -translate-x-1/2 gap-1.5">
            {list.map((_, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setIdx(i)}
                aria-label={`Slide ${i + 1}`}
                className={`h-1 rounded-full transition-all ${
                  i === idx ? "w-5 bg-accent" : "w-1 bg-zinc-600 hover:bg-zinc-300"
                }`}
              />
            ))}
          </div>
        </>
      )}
      </div>
    </section>
  )
}

function MangaCarousel({ title, items, onSelect }) {
  if (!items?.length) return null
  return (
    <section className="mx-auto w-full max-w-[1600px] px-4 py-4">
      <div className="mb-3 flex items-center gap-3">
        <span className="h-5 w-1 rounded-sm bg-accent" />
        <h2 className="text-base font-bold text-zinc-50">{title}</h2>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {items.map((manga, index) => (
          <MangaCard
            key={`hot-${manga.source_url ?? manga.id ?? manga.title}-${index}`}
            manga={manga}
            priority={index < 8}
            onSelect={onSelect}
          />
        ))}
      </div>
    </section>
  )
}

function SectionedCatalog({ sections, items, onSelect }) {
  // If sections is empty but items exist, group items by their section field
  const visibleSections = useMemo(() => {
    const fromSections = (sections ?? []).filter((s) => s.items?.length)
    if (fromSections.length > 0) return fromSections

    // Fallback: group flat items by section field
    if (!items?.length) return []
    const grouped = new Map()
    for (const item of items) {
      const sec = item.section || "Catálogo"
      if (!grouped.has(sec)) grouped.set(sec, [])
      grouped.get(sec).push(item)
    }
    return Array.from(grouped.entries()).map(([title, secItems]) => ({ title, items: secItems }))
  }, [sections, items])

  // lazy home: renderiza poucas secoes, carrega mais conforme rola
  const [shown, setShown] = useState(4)
  const sentinelRef = useRef(null)
  // Assinatura estavel do conjunto de secoes (titulos). O repoll da home
  // (refreshing=true, a cada 2.5s) recria os arrays, mas enquanto os titulos
  // forem os mesmos NAO resetamos `shown` -> sem pulo de scroll ao ler embaixo.
  const sectionsSignature = useMemo(
    () => visibleSections.map((section) => section.title).join("|"),
    [visibleSections],
  )
  useEffect(() => {
    setShown(4)
  }, [sectionsSignature])
  useEffect(() => {
    if (shown >= visibleSections.length) return undefined
    const el = sentinelRef.current
    if (!el) return undefined
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setShown((value) => Math.min(value + 2, visibleSections.length))
        }
      },
      { rootMargin: "800px" },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [shown, visibleSections])

  if (!visibleSections.length) {
    return (
      <main className="px-5 py-8 text-sm text-muted">
        Nenhuma obra encontrada.
      </main>
    )
  }

  return (
    <main className="pb-8">
      {visibleSections.slice(0, shown).map((section, index) =>
        section.layout === "carousel" ? (
          <MangaCarousel
            key={section.title}
            title={section.title}
            items={section.items}
            onSelect={onSelect}
          />
        ) : (
          <MangaSection
            key={section.title}
            title={section.title}
            items={section.items}
            sectionIndex={index}
            onSelect={onSelect}
          />
        ),
      )}
      {shown < visibleSections.length && (
        <div ref={sentinelRef} className="flex justify-center py-8 text-xs text-muted">
          Carregando mais...
        </div>
      )}
    </main>
  )
}

function SkeletonGrid() {
  return (
    <main className="mx-auto grid w-full max-w-[1600px] grid-cols-1 gap-3 px-4 py-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: 18 }).map((_, index) => (
        <MangaCardSkeleton key={index} />
      ))}
    </main>
  )
}

function authorName(author) {
  if (typeof author === "string") return author.trim()
  return String(author?.name || author?.full_name || "").trim()
}

function AuthorDetailRow({ label, value }) {
  const text = Array.isArray(value) ? value.filter(Boolean).join(", ") : value
  if (text === undefined || text === null || String(text).trim() === "") return null
  return (
    <div className="rounded border border-line bg-soft px-3 py-2">
      <dt className="text-[10px] uppercase text-muted">{label}</dt>
      <dd className="mt-1 truncate text-sm font-semibold text-zinc-100" title={String(text)}>
        {text}
      </dd>
    </div>
  )
}

function AuthorInfoModal({ panel, onClose }) {
  if (!panel) return null

  const data = panel.data ?? {}
  const name = data.name || panel.name
  const yearsActive = data.years_active?.length ? data.years_active.join(" - ") : ""
  const sourceLinks = data.source_links?.length
    ? data.source_links
    : (data.site_url ? [{ label: data.source || "Fonte", url: data.site_url }] : [])
  const socialLinks = [
    ...(data.social_links || []),
    data.official_site ? { label: "Site oficial", url: data.official_site } : null,
    data.twitter ? { label: "Twitter", url: data.twitter } : null,
    data.facebook ? { label: "Facebook", url: data.facebook } : null,
  ].filter((link, index, links) => link?.url && links.findIndex((candidate) => candidate?.url === link.url) === index)

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 px-4 py-6"
      role="dialog"
      aria-modal="true"
      aria-label={`Autor ${name}`}
      onMouseDown={onClose}
    >
      <section
        className="grid max-h-[90vh] w-full max-w-3xl grid-cols-1 overflow-hidden rounded-md border border-line bg-panel shadow-2xl md:grid-cols-[210px_minmax(0,1fr)] md:grid-rows-[minmax(0,1fr)]"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="border-b border-line bg-soft p-4 md:max-h-[90vh] md:overflow-y-auto md:border-b-0 md:border-r">
          <div className="aspect-[3/4] overflow-hidden rounded bg-zinc-950">
            {data.image_url ? (
              <img
                src={data.image_url}
                alt={`Foto de ${name}`}
                className="h-full w-full object-cover"
                onError={(event) => {
                  const fallback = data.image_fallbacks?.[0]
                  if (fallback && event.currentTarget.src !== fallback) {
                    event.currentTarget.src = fallback
                  }
                }}
              />
            ) : (
              <div className="flex h-full items-center justify-center px-4 text-center text-sm text-muted">
                Foto indisponivel
              </div>
            )}
          </div>
          {sourceLinks.length > 0 && (
            <div className="mt-3 flex flex-col gap-2">
              {sourceLinks.map((link) => (
                <a
                  key={`${link.label}-${link.url}`}
                  href={link.url}
                  target="_blank"
                  rel="noreferrer"
                  className="block rounded border border-line px-3 py-2 text-center text-xs font-semibold text-zinc-200 transition hover:border-zinc-500"
                >
                  {link.label}
                </a>
              ))}
            </div>
          )}
        </div>

        <div className="min-w-0 overflow-y-auto p-5 scrollbar-thin scrollbar-thumb-zinc-700 scrollbar-track-transparent">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h2 className="truncate text-2xl font-black text-zinc-50">{name}</h2>
              {data.native_name && <p className="mt-1 truncate text-sm text-muted">{data.native_name}</p>}
              {(data.matched_title || data.role) && (
                <p className="mt-2 text-sm text-zinc-300">
                  {data.matched_title}
                  {data.role ? ` - ${data.role}` : ""}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={onClose}
              className="shrink-0 rounded border border-line bg-soft px-3 py-1.5 text-sm text-zinc-100 hover:border-zinc-500"
            >
              Fechar
            </button>
          </div>

          {panel.loading && (
            <p className="mt-5 rounded border border-line bg-soft px-3 py-2 text-sm text-muted">
              Buscando informacoes...
            </p>
          )}
          {panel.error && (
            <p className="mt-5 rounded border border-red-900 bg-red-950/30 px-3 py-2 text-sm text-red-200">
              {panel.error}
            </p>
          )}

          {!panel.loading && !panel.error && (
            <>
              <dl className="mt-5 grid grid-cols-1 gap-2 sm:grid-cols-2">
                <AuthorDetailRow label="Ocupacao" value={data.occupations} />
                <AuthorDetailRow label="Idioma" value={data.language} />
                <AuthorDetailRow label="Nascimento" value={data.birth_date} />
                <AuthorDetailRow label="Idade" value={data.age} />
                <AuthorDetailRow label="Falecimento" value={data.death_date} />
                <AuthorDetailRow label="Cidade" value={data.home_town} />
                <AuthorDetailRow label="Anos ativos" value={yearsActive} />
                <AuthorDetailRow label="Favoritos" value={data.favourites} />
                <AuthorDetailRow label="Genero" value={data.gender} />
                <AuthorDetailRow label="Status" value={data.status} />
                <AuthorDetailRow label="Generos" value={data.genres} />
                <AuthorDetailRow label="Obras" value={data.total_series} />
                <AuthorDetailRow label="Tipo sanguineo" value={data.blood_type} />
              </dl>

              {data.alternative_names?.length > 0 && (
                <div className="mt-5">
                  <h3 className="text-xs font-bold uppercase text-muted">Tambem conhecido como</h3>
                  <p className="mt-2 text-sm leading-6 text-zinc-200">{data.alternative_names.join(", ")}</p>
                </div>
              )}

              {data.description && (
                <div className="mt-5">
                  <h3 className="text-xs font-bold uppercase text-muted">Bio</h3>
                  <p className="mt-2 whitespace-pre-line text-sm leading-6 text-zinc-200">
                    {data.description}
                  </p>
                </div>
              )}

              {socialLinks.length > 0 && (
                <div className="mt-5 border-t border-line/70 pt-4">
                  <h3 className="text-xs font-bold uppercase text-muted">Redes do autor</h3>
                  <div className="mt-2 flex flex-wrap gap-2">
                  {socialLinks.map((link) => (
                    <a
                      key={`${link.label}-${link.url}`}
                      href={link.url}
                      target="_blank"
                      rel="noreferrer"
                      className="rounded border border-line bg-soft px-3 py-1.5 text-xs font-semibold text-zinc-200 transition hover:border-zinc-500"
                    >
                      {link.label}
                    </a>
                  ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  )
}

const LOCAL_LIBRARY_CONFIG = {
  hq: {
    endpoint: "hq",
    title: "Biblioteca de HQs",
    source: "HQ Local",
    accept: ".cbz,.zip,.cbr,.pdf",
    formats: "CBZ, ZIP, CBR ou PDF",
    secondaryLabel: "Edicao",
    secondaryKey: "issue_number",
    secondaryPlaceholder: "#",
    Icon: FileArchive,
  },
  novels: {
    endpoint: "light-novels",
    title: "Biblioteca de Web Novels",
    source: "Web Novel Local",
    accept: ".epub,.txt,.md",
    formats: "EPUB, TXT ou MD",
    secondaryLabel: "Autor",
    secondaryKey: "author",
    secondaryPlaceholder: "Opcional",
    Icon: BookText,
  },
}

function readReaderSession() {
  try {
    const value = JSON.parse(window.localStorage.getItem(READER_SESSION_STORAGE_KEY) || "null")
    if (!value?.manga || !mangaStorageKey(value.manga) || !value.chapter_url) return null
    return value
  } catch {
    return null
  }
}

function clearReaderSession() {
  window.localStorage.removeItem(READER_SESSION_STORAGE_KEY)
}

function chapterTextBlocks(content) {
  let text = String(content || "")
  if (/<[a-z][\s\S]*>/i.test(text)) {
    text = text
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/(p|div|section|article|blockquote|h[1-6]|li)>/gi, "\n\n")
    const documentValue = new DOMParser().parseFromString(text, "text/html")
    text = documentValue.body.textContent || ""
  }
  return text
    .replace(/\r\n?/g, "\n")
    .split(/\n\s*\n+/)
    .map((block) => block.replace(/[ \t]+/g, " ").trim())
    .filter(Boolean)
}

function HQNowPluginPage({ onOpen }) {
  const [tab, setTab] = useState("remote")
  const [query, setQuery] = useState("")
  const [activeQuery, setActiveQuery] = useState("")
  const [limit, setLimit] = useState(32)
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const loadHQs = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const params = new URLSearchParams({ limit: String(limit) })
      if (activeQuery) params.set("q", activeQuery)
      const response = await fetch(`${API_BASE_URL}/api/plugins/hq-now?${params}`)
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      setItems(data.items ?? [])
    } catch (cause) {
      setError(cause.message || "Nao consegui carregar HQ Now.")
    } finally {
      setLoading(false)
    }
  }, [activeQuery, limit])

  useEffect(() => {
    void loadHQs()
  }, [loadHQs])

  const submitSearch = (event) => {
    event.preventDefault()
    setLimit(32)
    setActiveQuery(query.trim())
  }

  return (
    <>
    <div className="mx-auto flex w-full max-w-[1600px] gap-1 px-4 pt-5" role="tablist" aria-label="Fontes de HQ">
      <button
        type="button"
        role="tab"
        aria-selected={tab === "remote"}
        onClick={() => setTab("remote")}
        className={`flex h-9 items-center gap-2 rounded px-3 text-xs font-bold transition ${tab === "remote" ? "bg-accent text-black" : "border border-line bg-panel text-zinc-300 hover:border-accent"}`}
      >
        <FileArchive size={15} aria-hidden="true" /> HQ Now
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={tab === "local"}
        onClick={() => setTab("local")}
        className={`flex h-9 items-center gap-2 rounded px-3 text-xs font-bold transition ${tab === "local" ? "bg-accent text-black" : "border border-line bg-panel text-zinc-300 hover:border-accent"}`}
      >
        <Upload size={15} aria-hidden="true" /> Minha biblioteca
      </button>
    </div>
    {tab === "local" ? (
      <PluginLibraryPage kind="hq" onOpen={onOpen} />
    ) : (
    <main className="mx-auto min-h-[calc(100vh-72px)] w-full max-w-[1600px] px-4 py-6">
      <section aria-labelledby="hq-now-title">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
          <div className="flex items-center gap-3">
            <FileArchive size={22} className="text-accent" aria-hidden="true" />
            <div>
              <h1 id="hq-now-title" className="text-xl font-black text-zinc-50">HQs</h1>
              <p className="text-xs text-muted">{items.length} {items.length === 1 ? "obra" : "obras"}</p>
            </div>
          </div>
          <span className="rounded border border-accent/25 bg-accent/10 px-2 py-1 text-[11px] font-semibold text-accent">HQ Now</span>
        </header>

        <form onSubmit={submitSearch} className="relative mx-auto mt-5 max-w-3xl">
          <Search size={18} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Buscar HQ"
            aria-label="Buscar HQ no HQ Now"
            className="h-11 w-full rounded border border-line bg-panel pl-10 pr-24 text-sm text-zinc-100 outline-none transition focus:border-accent"
          />
          <button
            type="submit"
            className="absolute right-1.5 top-1.5 h-8 rounded bg-accent px-4 text-xs font-bold text-black transition hover:bg-accent-dim"
          >
            Buscar
          </button>
        </form>

        {activeQuery && (
          <div className="mt-3 flex items-center justify-center gap-2 text-xs text-muted">
            Resultados para “{activeQuery}”
            <button
              type="button"
              onClick={() => { setQuery(""); setActiveQuery(""); setLimit(32) }}
              className="grid h-6 w-6 place-items-center rounded text-zinc-400 hover:bg-soft hover:text-zinc-100"
              aria-label="Limpar busca"
              title="Limpar busca"
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        )}

        {error && <p className="mt-4 rounded border border-red-900 bg-red-950/30 px-3 py-2 text-sm text-red-200">{error}</p>}

        <div className="py-5">
          {loading ? (
            <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
              {Array.from({ length: 8 }, (_, index) => <MangaCardSkeleton key={index} />)}
            </div>
          ) : items.length ? (
            <>
              <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
                {items.map((manga, index) => (
                  <MangaCard key={mangaStorageKey(manga)} manga={manga} priority={index < 6} onSelect={onOpen} />
                ))}
              </div>
              {items.length >= limit && limit < 60 && (
                <div className="mt-5 flex justify-center">
                  <button
                    type="button"
                    onClick={() => setLimit((current) => Math.min(60, current + 16))}
                    className="h-9 rounded border border-line bg-panel px-4 text-xs font-bold text-zinc-200 transition hover:border-accent hover:text-accent"
                  >
                    Carregar mais
                  </button>
                </div>
              )}
            </>
          ) : (
            <div className="grid min-h-52 place-items-center text-center">
              <div>
                <FileArchive size={30} className="mx-auto text-zinc-600" aria-hidden="true" />
                <p className="mt-3 text-sm font-semibold text-zinc-300">Nenhuma HQ encontrada</p>
              </div>
            </div>
          )}
        </div>
      </section>
    </main>
    )}
    </>
  )
}

const REMOTE_NOVEL_PLUGINS = {
  "novel-mania": { label: "Novel Mania", endpoint: "novel-mania" },
  "central-novel": { label: "Central Novel", endpoint: "central-novel" },
  "tensura-fan": { label: "Tensura Fan", endpoint: "tensura-fan" },
  "pleiades-translations": { label: "Pleiades Translations", endpoint: "pleiades-translations" },
}

function RemoteNovelsPluginPage({ source, onOpen }) {
  const config = REMOTE_NOVEL_PLUGINS[source] ?? REMOTE_NOVEL_PLUGINS["novel-mania"]
  const [query, setQuery] = useState("")
  const [activeQuery, setActiveQuery] = useState("")
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const loadNovels = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const params = new URLSearchParams({ limit: "24" })
      if (activeQuery) params.set("q", activeQuery)
      const response = await fetch(`${API_BASE_URL}/api/plugins/${config.endpoint}?${params}`)
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      setItems(data.items ?? [])
    } catch (cause) {
      setError(cause.message || `Nao consegui carregar o ${config.label}.`)
    } finally {
      setLoading(false)
    }
  }, [activeQuery, config.endpoint, config.label])

  useEffect(() => {
    void loadNovels()
  }, [loadNovels])

  const submitSearch = (event) => {
    event.preventDefault()
    setActiveQuery(query.trim())
  }

  return (
    <main className="mx-auto min-h-[calc(100vh-120px)] w-full max-w-[1600px] px-4 py-6">
      <section aria-labelledby={`${config.endpoint}-title`}>
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
          <div className="flex items-center gap-3">
            <BookText size={22} className="text-accent" aria-hidden="true" />
            <div>
              <h1 id={`${config.endpoint}-title`} className="text-xl font-black text-zinc-50">Web Novels</h1>
              <p className="text-xs text-muted">{items.length} {items.length === 1 ? "obra" : "obras"}</p>
            </div>
          </div>
          <span className="rounded border border-accent/25 bg-accent/10 px-2 py-1 text-[11px] font-semibold text-accent">{config.label}</span>
        </header>

        <form onSubmit={submitSearch} className="relative mx-auto mt-5 max-w-3xl">
          <Search size={18} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Buscar web novel"
            aria-label={`Buscar web novel no ${config.label}`}
            className="h-11 w-full rounded border border-line bg-panel pl-10 pr-24 text-sm text-zinc-100 outline-none transition focus:border-accent"
          />
          <button type="submit" className="absolute right-1.5 top-1.5 h-8 rounded bg-accent px-4 text-xs font-bold text-black transition hover:bg-accent-dim">
            Buscar
          </button>
        </form>

        {activeQuery && (
          <div className="mt-3 flex items-center justify-center gap-2 text-xs text-muted">
            Resultados para "{activeQuery}"
            <button
              type="button"
              onClick={() => { setQuery(""); setActiveQuery("") }}
              className="grid h-6 w-6 place-items-center rounded text-zinc-400 hover:bg-soft hover:text-zinc-100"
              aria-label="Limpar busca"
              title="Limpar busca"
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        )}

        {error && <p className="mt-4 rounded border border-red-900 bg-red-950/30 px-3 py-2 text-sm text-red-200">{error}</p>}

        <div className="py-5">
          {loading ? (
            <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
              {Array.from({ length: 8 }, (_, index) => <MangaCardSkeleton key={index} />)}
            </div>
          ) : items.length ? (
            <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
              {items.map((manga, index) => (
                <MangaCard key={mangaStorageKey(manga)} manga={manga} priority={index < 6} onSelect={onOpen} />
              ))}
            </div>
          ) : (
            <div className="grid min-h-52 place-items-center text-center">
              <div>
                <BookText size={30} className="mx-auto text-zinc-600" aria-hidden="true" />
                <p className="mt-3 text-sm font-semibold text-zinc-300">Nenhuma web novel encontrada</p>
              </div>
            </div>
          )}
        </div>
      </section>
    </main>
  )
}

function WebNovelsPluginPage({ onOpen, onChanged }) {
  const [tab, setTab] = useState("novel-mania")
  return (
    <>
      <div className="mx-auto flex w-full max-w-[1600px] gap-1 px-4 pt-5" role="tablist" aria-label="Fontes de web novel">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "novel-mania"}
          onClick={() => setTab("novel-mania")}
          className={`flex h-9 items-center gap-2 rounded px-3 text-xs font-bold transition ${tab === "novel-mania" ? "bg-accent text-black" : "border border-line bg-panel text-zinc-300 hover:border-accent"}`}
        >
          <BookText size={15} aria-hidden="true" /> Novel Mania
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "central-novel"}
          onClick={() => setTab("central-novel")}
          className={`flex h-9 items-center gap-2 rounded px-3 text-xs font-bold transition ${tab === "central-novel" ? "bg-accent text-black" : "border border-line bg-panel text-zinc-300 hover:border-accent"}`}
        >
          <BookOpen size={15} aria-hidden="true" /> Central Novel
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "tensura-fan"}
          onClick={() => setTab("tensura-fan")}
          className={`flex h-9 items-center gap-2 rounded px-3 text-xs font-bold transition ${tab === "tensura-fan" ? "bg-accent text-black" : "border border-line bg-panel text-zinc-300 hover:border-accent"}`}
        >
          <BookOpen size={15} aria-hidden="true" /> Tensura Fan
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "pleiades-translations"}
          onClick={() => setTab("pleiades-translations")}
          className={`flex h-9 items-center gap-2 rounded px-3 text-xs font-bold transition ${tab === "pleiades-translations" ? "bg-accent text-black" : "border border-line bg-panel text-zinc-300 hover:border-accent"}`}
        >
          <BookOpen size={15} aria-hidden="true" /> Pleiades
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "local"}
          onClick={() => setTab("local")}
          className={`flex h-9 items-center gap-2 rounded px-3 text-xs font-bold transition ${tab === "local" ? "bg-accent text-black" : "border border-line bg-panel text-zinc-300 hover:border-accent"}`}
        >
          <Upload size={15} aria-hidden="true" /> Minha biblioteca
        </button>
      </div>
      {tab !== "local" ? (
        <RemoteNovelsPluginPage source={tab} onOpen={onOpen} />
      ) : (
        <PluginLibraryPage kind="novels" onOpen={onOpen} onChanged={onChanged} />
      )}
    </>
  )
}

function PluginLibraryPage({ kind, onOpen, onChanged }) {
  const config = LOCAL_LIBRARY_CONFIG[kind] ?? LOCAL_LIBRARY_CONFIG.hq
  const Icon = config.Icon
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState("")
  const [title, setTitle] = useState("")
  const [secondary, setSecondary] = useState("")
  const [file, setFile] = useState(null)
  const fileInputRef = useRef(null)

  const loadLibrary = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const response = await fetch(`${API_BASE_URL}/api/${config.endpoint}/library`)
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      setItems(data.items ?? [])
    } catch (cause) {
      setError(cause.message || `Nao consegui carregar ${config.title.toLowerCase()}.`)
    } finally {
      setLoading(false)
    }
  }, [config.endpoint, config.title])

  useEffect(() => {
    setItems([])
    setTitle("")
    setSecondary("")
    setFile(null)
    if (fileInputRef.current) fileInputRef.current.value = ""
    void loadLibrary()
  }, [kind, loadLibrary])

  const importItem = async (event) => {
    event.preventDefault()
    if (!file || uploading) return
    const body = new FormData()
    body.append("file", file)
    if (title.trim()) body.append("title", title.trim())
    if (secondary.trim()) body.append(config.secondaryKey, secondary.trim())
    setUploading(true)
    setError("")
    try {
      const response = await fetch(`${API_BASE_URL}/api/${config.endpoint}/import`, { method: "POST", body })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      setFile(null)
      setTitle("")
      setSecondary("")
      if (fileInputRef.current) fileInputRef.current.value = ""
      await loadLibrary()
      onChanged?.()
    } catch (cause) {
      setError(cause.message || `Nao consegui importar em ${config.title.toLowerCase()}.`)
    } finally {
      setUploading(false)
    }
  }

  const removeItem = async (manga) => {
    const itemId = String(manga.source_url || "").split("/").filter(Boolean).at(-1)
    if (!itemId) return
    if (!window.confirm(`Remover "${manga.title}" da biblioteca local?`)) return
    setError("")
    try {
      const response = await fetch(`${API_BASE_URL}/api/${config.endpoint}/${encodeURIComponent(itemId)}`, {
        method: "DELETE",
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      setItems((current) => current.filter((item) => mangaStorageKey(item) !== mangaStorageKey(manga)))
      onChanged?.()
    } catch (cause) {
      setError(cause.message || "Nao consegui remover obra.")
    }
  }

  return (
    <main className="mx-auto min-h-[calc(100vh-72px)] w-full max-w-[1600px] px-4 py-6">
      <section aria-labelledby={`${kind}-library-title`}>
        <header className="flex items-center justify-between border-b border-line pb-4">
          <div className="flex items-center gap-3">
            <Icon size={22} className="text-accent" aria-hidden="true" />
            <div>
              <h1 id={`${kind}-library-title`} className="text-xl font-black text-zinc-50">{config.title}</h1>
              <p className="text-xs text-muted">{items.length} {items.length === 1 ? "obra" : "obras"}</p>
            </div>
          </div>
          <span className="rounded border border-accent/25 bg-accent/10 px-2 py-1 text-[11px] font-semibold text-accent">{config.source}</span>
        </header>

        <form onSubmit={importItem} className="mt-4 grid gap-3 border-b border-line/70 pb-5 md:grid-cols-[1fr_180px_1.4fr_auto] md:items-end">
          <label className="text-xs font-semibold text-zinc-300">
            Titulo
            <input
              value={title}
              maxLength={180}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Detectar pelo arquivo"
              className="mt-1.5 h-10 w-full rounded border border-line bg-app px-3 text-sm text-zinc-100 outline-none focus:border-accent"
            />
          </label>
          <label className="text-xs font-semibold text-zinc-300">
            {config.secondaryLabel}
            <input
              value={secondary}
              maxLength={180}
              onChange={(event) => setSecondary(event.target.value)}
              placeholder={config.secondaryPlaceholder}
              className="mt-1.5 h-10 w-full rounded border border-line bg-app px-3 text-sm text-zinc-100 outline-none focus:border-accent"
            />
          </label>
          <label className="text-xs font-semibold text-zinc-300">
            Arquivo
            <input
              ref={fileInputRef}
              type="file"
              required
              accept={config.accept}
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="mt-1.5 block h-10 w-full rounded border border-line bg-app text-xs text-zinc-300 file:mr-3 file:h-full file:border-0 file:border-r file:border-line file:bg-soft file:px-3 file:text-xs file:font-semibold file:text-zinc-100"
            />
          </label>
          <button
            type="submit"
            disabled={!file || uploading}
            className="flex h-10 items-center justify-center gap-2 rounded bg-accent px-4 text-sm font-bold text-black transition hover:bg-accent-dim disabled:cursor-wait disabled:opacity-50"
          >
            {uploading ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}
            Importar
          </button>
        </form>

        {error && <p className="mt-4 rounded border border-red-900 bg-red-950/30 px-3 py-2 text-sm text-red-200">{error}</p>}

        <div className="py-5">
          {loading ? (
            <div className="grid min-h-52 place-items-center text-muted"><Loader2 size={24} className="animate-spin" /></div>
          ) : items.length ? (
            <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
              {items.map((manga, index) => (
                <div key={mangaStorageKey(manga)} className="relative">
                  <MangaCard
                    manga={manga}
                    priority={index < 4}
                    onSelect={onOpen}
                  />
                  <button
                    type="button"
                    onClick={() => removeItem(manga)}
                    aria-label={`Remover ${manga.title}`}
                    title="Remover da biblioteca"
                    className="absolute bottom-2 right-2 grid h-7 w-7 place-items-center rounded border border-red-900/70 bg-app/90 text-red-300 transition hover:border-red-500 hover:text-red-100"
                  >
                    <Trash2 size={13} aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="grid min-h-52 place-items-center text-center">
              <div>
                <Icon size={30} className="mx-auto text-zinc-600" aria-hidden="true" />
                <p className="mt-3 text-sm font-semibold text-zinc-300">Biblioteca vazia</p>
                <p className="mt-1 text-xs text-muted">Importe {config.formats}.</p>
              </div>
            </div>
          )}
        </div>
      </section>
    </main>
  )
}

const PROFILE_PROVIDERS = [
  { key: "anilist", label: "AniList" },
  { key: "myanimelist", label: "MyAnimeList" },
]

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(new Error("read"))
    reader.readAsDataURL(file)
  })
}

function ProfilePanel({ profile, historyCount, onClose, onSave, onProfileChange, onShowHistory, onShowFavorites, onShowLibrary, authed, onOpenAuth, onLogout }) {
  const [name, setName] = useState(profile?.display_name || "Leitor")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [busyImage, setBusyImage] = useState("")
  const [linking, setLinking] = useState("")
  const [providerConfig, setProviderConfig] = useState({})
  const [presets, setPresets] = useState([])
  const avatarInputRef = useRef(null)
  const backgroundInputRef = useRef(null)
  const homeBgInputRef = useRef(null)

  useEffect(() => {
    setName(profile?.display_name || "Leitor")
    setError("")
  }, [profile?.id, profile?.display_name])

  // Quais provedores OAuth o servidor tem configurados (habilita os botoes).
  useEffect(() => {
    if (!profile?.id) return undefined
    let cancelled = false
    fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(profile.id)}/link/status`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (data && !cancelled) setProviderConfig(data.providers || {}) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [profile?.id])

  // Backgrounds pre-definidos oferecidos pelo servidor.
  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE_URL}/api/backgrounds`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (data && !cancelled) setPresets(data.backgrounds || []) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  // Retorno do popup OAuth: recarrega o perfil quando o vinculo conclui.
  useEffect(() => {
    if (!profile?.id) return undefined
    const onMessage = (event) => {
      const data = event.data
      if (!data || data.source !== "kari-oauth") return
      setLinking("")
      if (data.ok) {
        setNotice(data.detail || "Conta vinculada!")
        setError("")
        fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(profile.id)}`)
          .then((response) => (response.ok ? response.json() : null))
          .then((fresh) => { if (fresh) onProfileChange?.(fresh) })
          .catch(() => {})
      } else {
        setError(data.detail || "Falha ao vincular a conta.")
      }
    }
    window.addEventListener("message", onMessage)
    return () => window.removeEventListener("message", onMessage)
  }, [profile?.id, onProfileChange])

  if (!profile) return null

  const save = async (event) => {
    event.preventDefault()
    const displayName = name.trim()
    if (!displayName) return
    setSaving(true)
    setError("")
    try {
      await onSave(displayName)
      onClose()
    } catch {
      setError("Nao consegui salvar perfil.")
    } finally {
      setSaving(false)
    }
  }

  const applyImage = async (kind, body) => {
    if (!profile?.id) return
    setBusyImage(kind)
    setError("")
    setNotice("")
    try {
      const response = await fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(profile.id)}/${kind}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      const data = await response.json().catch(() => null)
      if (!response.ok) {
        setError((data && data.detail) || "Nao consegui atualizar a imagem.")
        return
      }
      onProfileChange?.(data)
    } catch {
      setError("Nao consegui atualizar a imagem.")
    } finally {
      setBusyImage("")
    }
  }

  const onPickFile = async (kind, event) => {
    const file = event.target.files?.[0]
    event.target.value = ""
    if (!file) return
    const allowsVideo = kind === "background" || kind === "home-background"
    const okType = file.type.startsWith("image/") || (allowsVideo && file.type.startsWith("video/"))
    if (!okType) {
      setError(allowsVideo ? "Selecione uma imagem ou video (mp4/webm)." : "Selecione um arquivo de imagem.")
      return
    }
    try {
      const dataUrl = await fileToDataUrl(file)
      await applyImage(kind, { data: dataUrl })
    } catch {
      setError("Nao consegui ler o arquivo.")
    }
  }

  const linkAccount = async (provider) => {
    if (!profile?.id) return
    setLinking(provider)
    setError("")
    setNotice("")
    try {
      const response = await fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(profile.id)}/link/${provider}`, {
        method: "POST",
      })
      const data = await response.json().catch(() => null)
      if (!response.ok) {
        setError((data && data.detail) || "Nao consegui iniciar o vinculo.")
        setLinking("")
        return
      }
      const popup = window.open(data.authorize_url, "kari-oauth", "width=560,height=760")
      if (!popup) {
        setError("Habilite popups para vincular a conta.")
        setLinking("")
      }
    } catch {
      setError("Nao consegui iniciar o vinculo.")
      setLinking("")
    }
  }

  const unlinkAccount = async (provider) => {
    if (!profile?.id) return
    setLinking(provider)
    setError("")
    setNotice("")
    try {
      const response = await fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(profile.id)}/link/${provider}`, {
        method: "DELETE",
      })
      const data = await response.json().catch(() => null)
      if (!response.ok) {
        setError((data && data.detail) || "Nao consegui desvincular.")
        return
      }
      onProfileChange?.(data)
    } catch {
      setError("Nao consegui desvincular.")
    } finally {
      setLinking("")
    }
  }

  const syncAccount = async (provider, label) => {
    if (!profile?.id) return
    setLinking(`sync:${provider}`)
    setError("")
    setNotice("")
    try {
      const response = await fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(profile.id)}/sync/${provider}`, { method: "POST" })
      const data = await response.json().catch(() => null)
      if (!response.ok) {
        setError((data && data.detail) || `Nao consegui sincronizar ${label}.`)
        return
      }
      onProfileChange?.(data.profile)
      const sync = data.sync || {}
      setNotice(`${label}: ${sync.matched_count || 0}/${sync.list_count || 0} obras no Kari, ${sync.added_count || 0} novas na lista.`)
    } catch {
      setError(`Nao consegui sincronizar ${label}.`)
    } finally {
      setLinking("")
    }
  }

  const avatarSrc = resolveApiUrl(profile.avatar_url)
  const backgroundSrc = resolveApiUrl(profile.background_url || profile.home_background_url)
  const links = profile.links || {}

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 px-4 py-6"
      role="dialog"
      aria-modal="true"
      aria-label="Perfil"
      onMouseDown={onClose}
    >
      <form
        onSubmit={save}
        onMouseDown={(event) => event.stopPropagation()}
        className="relative max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-md border border-line bg-panel shadow-2xl"
      >
        {/* Imagem/video de background dimensionada no card inteiro */}
        {backgroundSrc && (
          isVideoUrl(backgroundSrc) ? (
            <video
              src={backgroundSrc}
              autoPlay
              muted
              loop
              playsInline
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 h-full w-full rounded-md object-cover"
              style={{ zIndex: 0 }}
            />
          ) : (
            <div
              className="pointer-events-none absolute inset-0 rounded-md bg-cover bg-center"
              style={{ zIndex: 0, backgroundImage: `url("${backgroundSrc}")` }}
              aria-hidden="true"
            />
          )
        )}
        <div className="pointer-events-none absolute inset-0 rounded-md bg-app/45" style={{ zIndex: 0 }} aria-hidden="true" />
        <div className="relative" style={{ zIndex: 1 }}>
        {/* Cabecalho com background + avatar */}
        <div className="relative">
          <div className="relative h-28 w-full rounded-t-md">
            {!backgroundSrc && <div className="h-full w-full rounded-t-md bg-gradient-to-br from-accent/20 to-violet-500/10" />}
            <button
              type="button"
              onClick={() => backgroundInputRef.current?.click()}
              disabled={busyImage === "background"}
              className="absolute right-2 top-2 flex items-center gap-1 rounded bg-black/60 px-2 py-1 text-[11px] font-semibold text-zinc-100 hover:bg-black/80 disabled:opacity-60"
            >
              {busyImage === "background" ? <Loader2 size={13} className="animate-spin" /> : <ImagePlus size={13} />}
              Background
            </button>
            {profile.background_url && (
              <button
                type="button"
                onClick={() => applyImage("background", {})}
                disabled={busyImage === "background"}
                className="absolute right-2 top-9 flex items-center gap-1 rounded bg-black/60 px-2 py-1 text-[11px] font-semibold text-red-200 hover:bg-black/80 disabled:opacity-60"
              >
                <Trash2 size={13} /> Remover
              </button>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="absolute right-2 top-2 grid h-8 w-8 place-items-center rounded border border-line bg-black/40 text-zinc-200 hover:border-zinc-500 lg:hidden"
            aria-label="Fechar perfil"
          >
            <X size={16} strokeWidth={2} aria-hidden="true" />
          </button>
          <div className="absolute -bottom-8 left-5">
            <div className="relative h-20 w-20">
              <div className="h-20 w-20 overflow-hidden rounded-full border-2 border-panel bg-accent/15">
                {avatarSrc
                  ? <img src={avatarSrc} alt="Avatar" className="h-full w-full object-cover" />
                  : <div className="grid h-full w-full place-items-center text-accent"><UserRound size={30} strokeWidth={1.8} /></div>}
              </div>
              <button
                type="button"
                onClick={() => avatarInputRef.current?.click()}
                disabled={busyImage === "avatar"}
                className="absolute -bottom-1 -right-1 grid h-7 w-7 place-items-center rounded-full border border-line bg-app text-accent hover:border-accent disabled:opacity-60"
                aria-label="Trocar foto"
              >
                {busyImage === "avatar" ? <Loader2 size={13} className="animate-spin" /> : <Camera size={14} />}
              </button>
            </div>
          </div>
        </div>

        <input ref={avatarInputRef} type="file" accept="image/*" hidden onChange={(event) => onPickFile("avatar", event)} />
        <input ref={backgroundInputRef} type="file" accept="image/*,video/mp4,video/webm" hidden onChange={(event) => onPickFile("background", event)} />
        <input ref={homeBgInputRef} type="file" accept="image/*,video/mp4,video/webm" hidden onChange={(event) => onPickFile("home-background", event)} />

        <div className="px-5 pb-5 pt-10">
          <div className="flex items-center justify-between gap-4">
            <h2 className="text-lg font-black text-zinc-50">Perfil</h2>
            {profile.avatar_url && (
              <button
                type="button"
                onClick={() => applyImage("avatar", {})}
                disabled={busyImage === "avatar"}
                className="flex items-center gap-1 text-[11px] font-semibold text-red-300 hover:text-red-200 disabled:opacity-60"
              >
                <Trash2 size={12} /> Remover foto
              </button>
            )}
          </div>

          <label className="mt-4 block text-xs font-semibold text-zinc-300" htmlFor="profile-name">Nome</label>
          <input
            id="profile-name"
            value={name}
            maxLength={48}
            onChange={(event) => setName(event.target.value)}
            className="mt-2 h-10 w-full rounded border border-line/70 bg-app/50 px-3 text-sm text-zinc-100 outline-none backdrop-blur focus:border-accent"
          />

          <div className="mt-4 grid grid-cols-3 gap-2">
            <button
              type="button"
              onClick={onShowFavorites}
              className="rounded border border-line/70 bg-soft/40 px-3 py-2 text-left backdrop-blur transition hover:border-accent/60 hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            >
              <p className="text-[10px] uppercase text-muted">Favoritos</p>
              <p className="mt-1 text-lg font-black text-zinc-100">{profile.favorites?.length || 0}</p>
            </button>
            <button
              type="button"
              onClick={onShowHistory}
              className="rounded border border-line/70 bg-soft/40 px-3 py-2 text-left backdrop-blur transition hover:border-accent/60 hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            >
              <p className="text-[10px] uppercase text-muted">Historico</p>
              <p className="mt-1 text-lg font-black text-zinc-100">{historyCount}</p>
            </button>
            <button
              type="button"
              onClick={onShowLibrary}
              className="rounded border border-line/70 bg-soft/40 px-3 py-2 text-left backdrop-blur transition hover:border-accent/60 hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            >
              <p className="text-[10px] uppercase text-muted">Minha lista</p>
              <p className="mt-1 text-lg font-black text-zinc-100">{profile.library?.length || 0}</p>
            </button>
          </div>

          {/* Conta (cadastro/login) */}
          <div className="mt-5">
            <p className="text-xs font-semibold text-zinc-300">Conta</p>
            {authed ? (
              <div className="mt-2 flex items-center justify-between gap-2 rounded border border-line/70 bg-soft/40 px-3 py-2 backdrop-blur">
                <p className="truncate text-xs text-zinc-200">Logado como <span className="font-bold text-accent">{profile.display_name}</span></p>
                <button
                  type="button"
                  onClick={onLogout}
                  className="shrink-0 rounded border border-line px-3 py-1 text-[11px] font-semibold text-red-300 hover:border-red-400/50"
                >
                  Sair
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={onOpenAuth}
                className="mt-2 h-10 w-full rounded border border-accent/40 bg-accent/10 text-sm font-bold text-accent transition hover:bg-accent/20"
              >
                Entrar / Cadastrar
              </button>
            )}
          </div>

          {/* Aparencia da Home */}
          <div className="mt-5">
            <p className="text-xs font-semibold text-zinc-300">Background da Home</p>
            <div className="mt-2 overflow-hidden rounded border border-line">
              <div className="relative h-24 w-full bg-soft">
                {profile.home_background_url
                  ? (isVideoUrl(resolveApiUrl(profile.home_background_url))
                      ? <video src={resolveApiUrl(profile.home_background_url)} autoPlay muted loop playsInline className="h-full w-full object-cover" />
                      : <img src={resolveApiUrl(profile.home_background_url)} alt="" className="h-full w-full object-cover" />)
                  : <div className="h-full w-full bg-gradient-to-br from-accent/15 to-violet-500/10" />}
                <div className="absolute inset-0 flex items-end justify-end gap-2 p-2">
                  <button
                    type="button"
                    onClick={() => homeBgInputRef.current?.click()}
                    disabled={busyImage === "home-background"}
                    className="flex items-center gap-1 rounded bg-black/60 px-2 py-1 text-[11px] font-semibold text-zinc-100 hover:bg-black/80 disabled:opacity-60"
                  >
                    {busyImage === "home-background" ? <Loader2 size={13} className="animate-spin" /> : <ImagePlus size={13} />}
                    Trocar
                  </button>
                  {profile.home_background_url && (
                    <button
                      type="button"
                      onClick={() => applyImage("home-background", {})}
                      disabled={busyImage === "home-background"}
                      className="flex items-center gap-1 rounded bg-black/60 px-2 py-1 text-[11px] font-semibold text-red-200 hover:bg-black/80 disabled:opacity-60"
                    >
                      <Trash2 size={13} /> Remover
                    </button>
                  )}
                </div>
              </div>
            </div>
            <p className="mt-1 text-[11px] text-muted">A imagem aparece atras do catalogo na pagina inicial.</p>
            {presets.length > 0 && (
              <div className="mt-3">
                <p className="mb-2 text-[11px] font-semibold text-zinc-400">Predefinidos</p>
                <div className="grid grid-cols-4 gap-2">
                  {presets.map((preset) => {
                    const src = resolveApiUrl(preset.url)
                    return (
                      <button
                        key={preset.url}
                        type="button"
                        onClick={() => applyImage("home-background", { url: preset.url })}
                        disabled={busyImage === "home-background"}
                        title={`Usar "${preset.name}"`}
                        className="group relative aspect-video overflow-hidden rounded border border-line/70 transition hover:border-accent disabled:opacity-60"
                      >
                        {preset.kind === "video"
                          ? <video src={src} muted loop playsInline preload="metadata" className="h-full w-full object-cover transition group-hover:scale-105" />
                          : <img src={src} alt={preset.name} loading="lazy" className="h-full w-full object-cover transition group-hover:scale-105" />}
                        {preset.kind === "video" && (
                          <span className="absolute bottom-0.5 right-0.5 rounded bg-black/70 px-1 text-[9px] font-bold text-zinc-100">MP4</span>
                        )}
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Contas vinculadas */}
          <div className="mt-5">
            <p className="text-xs font-semibold text-zinc-300">Contas vinculadas</p>
            <div className="mt-2 space-y-2">
              {PROFILE_PROVIDERS.map(({ key, label }) => {
                const link = links[key]
                const configured = providerConfig[key]?.configured
                const busy = linking === key || linking === `sync:${key}`
                const syncBusy = linking === `sync:${key}`
                return (
                  <div key={key} className="flex items-center justify-between gap-2 rounded border border-line/70 bg-soft/40 px-3 py-2 backdrop-blur">
                    <div className="flex min-w-0 items-center gap-2">
                      {link?.avatar
                        ? <img src={link.avatar} alt="" className="h-7 w-7 rounded-full object-cover" />
                        : <div className="grid h-7 w-7 place-items-center rounded-full bg-accent/15 text-accent"><Link2 size={14} /></div>}
                      <div className="min-w-0">
                        <p className="truncate text-xs font-semibold text-zinc-100">{label}</p>
                        {link
                          ? (
                            <>
                              <a href={link.url || "#"} target="_blank" rel="noreferrer" className="flex items-center gap-1 truncate text-[11px] text-accent hover:underline">
                                {link.name || "vinculado"} <ExternalLink size={10} />
                              </a>
                              {link.synced_at > 0 && (
                                <p className="truncate text-[10px] text-muted">
                                  {link.matched_count}/{link.list_count} obras sincronizadas
                                </p>
                              )}
                            </>
                          )
                          : <p className="truncate text-[11px] text-muted">{configured ? "nao vinculado" : "indisponivel no servidor"}</p>}
                      </div>
                    </div>
                    {link
                      ? (
                        <div className="flex shrink-0 items-center gap-1.5">
                          <button
                            type="button"
                            onClick={() => syncAccount(key, label)}
                            disabled={busy}
                            className="flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-2 py-1 text-[11px] font-semibold text-accent hover:bg-accent/20 disabled:opacity-60"
                          >
                            {syncBusy ? <Loader2 size={12} className="animate-spin" /> : <Link2 size={12} />} Sincronizar
                          </button>
                          <button
                            type="button"
                            onClick={() => unlinkAccount(key)}
                            disabled={busy}
                            aria-label={`Desvincular ${label}`}
                            title={`Desvincular ${label}`}
                            className="grid h-7 w-7 place-items-center rounded border border-line text-red-300 hover:border-red-400/50 disabled:opacity-60"
                          >
                            <Unlink size={12} />
                          </button>
                        </div>
                      )
                      : (
                        <button
                          type="button"
                          onClick={() => linkAccount(key)}
                          disabled={busy || !configured}
                          title={configured ? "" : "Configure as credenciais OAuth no .env do servidor."}
                          className="flex shrink-0 items-center gap-1 rounded bg-accent px-2 py-1 text-[11px] font-black text-app hover:bg-emerald-200 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {busy ? <Loader2 size={12} className="animate-spin" /> : <Link2 size={12} />} Vincular
                        </button>
                      )}
                  </div>
                )
              })}
            </div>
          </div>

          {notice && <p className="mt-4 text-sm text-accent">{notice}</p>}
          {error && <p className="mt-4 text-sm text-red-300">{error}</p>}
          <button type="submit" disabled={saving || !name.trim()} className="mt-5 h-10 w-full rounded bg-accent text-sm font-black text-app transition hover:bg-emerald-200 disabled:cursor-wait disabled:opacity-60">
            {saving ? "Salvando..." : "Salvar"}
          </button>
        </div>
        </div>
      </form>
    </div>
  )
}

function MangaDetailPanel({ manga, onClose, onGenreSelect, isFavorite, onToggleFavorite, onRead, libraryEntry, onSaveLibrary, onDeleteLibrary }) {
  const [chapters, setChapters] = useState([])
  const [loadingChapters, setLoadingChapters] = useState(false)
  const [showChaptersLoader, setShowChaptersLoader] = useState(false)
  const [chapterError, setChapterError] = useState("")
  const [resolvedSource, setResolvedSource] = useState("")
  const [sourceChanged, setSourceChanged] = useState(false)
  const [openedChapter, setOpenedChapter] = useState(null)
  const [loadingChapter, setLoadingChapter] = useState(false)
  const [firstChapterPageLoaded, setFirstChapterPageLoaded] = useState(false)
  const [loaderTick, setLoaderTick] = useState(0)
  const [openChapterError, setOpenChapterError] = useState("")
  const [meta, setMeta] = useState(null)
  const [authorPanel, setAuthorPanel] = useState(null)
  const [readerSidebarOpen, setReaderSidebarOpen] = useState(false)
  const [readerSidebarCollapsed, setReaderSidebarCollapsed] = useState(false)
  const [readerBrightness, setReaderBrightness] = useState(100)
  const [readerZoom, setReaderZoom] = useState(100)
  const [chaptersDescending, setChaptersDescending] = useState(false)
  const [libraryStatus, setLibraryStatus] = useState("COMPLETED")
  const [libraryScore, setLibraryScore] = useState("")
  const [libraryReview, setLibraryReview] = useState("")
  const [savingLibrary, setSavingLibrary] = useState(false)
  const [deletingLibrary, setDeletingLibrary] = useState(false)
  const [libraryMessage, setLibraryMessage] = useState("")
  const readerSwipeStart = useRef(null)
  const restoredReaderKey = useRef("")

  useEffect(() => {
    setLibraryStatus(libraryEntry?.status || "COMPLETED")
    setLibraryScore(libraryEntry?.score == null ? "" : String(libraryEntry.score))
    setLibraryReview(libraryEntry?.review || "")
  }, [manga?.id, manga?.source_url, libraryEntry?.score, libraryEntry?.status, libraryEntry?.review])

  const saveLibrary = async () => {
    if (!manga || !onSaveLibrary) return
    setSavingLibrary(true)
    setLibraryMessage("")
    try {
      const result = await onSaveLibrary(manga, {
        status: libraryStatus,
        score: libraryScore === "" ? null : Number(libraryScore),
        review: libraryReview,
      })
      const remoteScore = result?.anilist?.score
      setLibraryMessage(
        result?.anilist?.state === "updated"
          ? (remoteScore == null ? "Lista atualizada no AniList." : `Nota ${remoteScore} salva no AniList.`)
          : "Obra salva na sua lista.",
      )
    } finally {
      setSavingLibrary(false)
    }
  }

  const deleteLibrary = async () => {
    if (!manga || !libraryEntry || !onDeleteLibrary) return
    if (!window.confirm("Remover esta obra da sua lista?")) return
    setDeletingLibrary(true)
    setLibraryMessage("")
    try {
      const result = await onDeleteLibrary(libraryEntry)
      setLibraryMessage(result?.anilist?.state === "deleted" ? "Removida da lista e do AniList." : "Obra removida da sua lista.")
    } finally {
      setDeletingLibrary(false)
    }
  }

  useEffect(() => {
    if (!loadingChapters) {
      setShowChaptersLoader(false)
      return undefined
    }
    const timer = window.setTimeout(
      () => setShowChaptersLoader(true),
      CHAPTER_LOADER_DELAY_MS,
    )
    return () => window.clearTimeout(timer)
  }, [loadingChapters])

  // Lista da home e enxuta; os metadados ricos (sinopse multi-idioma, generos,
  // autores, idiomas) vem do /api/chapters (payload.manga) e sao mesclados aqui.
  // MERGE que PRESERVA o card: o /api/chapters retorna o objeto manga com TODAS
  // as chaves, mesmo vazias (description:"", genres:[], authors:[]). Um spread
  // ingenuo ({...manga, ...meta}) deixaria esses vazios sobrescrever os dados bons
  // do card -> sinopse/autor/tags "fugiam" quando os capitulos chegavam. Aqui o
  // meta so sobrescreve quando traz valor de fato (string nao-vazia / array com
  // itens / valor != null); senao mantem o que veio do card.
  const detail = useMemo(() => {
    const merged = { ...manga }
    for (const [key, value] of Object.entries(meta || {})) {
      const isEmpty =
        value == null ||
        (typeof value === "string" && value.trim() === "") ||
        (Array.isArray(value) && value.length === 0)
      if (!isEmpty) merged[key] = value
    }
    return merged
  }, [manga, meta])

  const descriptions = Array.isArray(detail?.descriptions) && detail.descriptions.length
    ? detail.descriptions
    : (detail?.description ? [{ lang: "pt-br", text: detail.description }] : [])
  const [descLang, setDescLang] = useState(descriptions[0]?.lang ?? "pt-br")
  useEffect(() => {
    setDescLang(descriptions[0]?.lang ?? "pt-br")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manga?.id, meta])
  const activeDesc = descriptions.find((d) => d.lang === descLang) ?? descriptions[0]
  const authorNames = useMemo(
    () => (detail?.authors ?? []).map(authorName).filter(Boolean),
    [detail?.authors],
  )

  const chapterLangs = useMemo(() => {
    const raw = (detail?.chapter_languages ?? []).map((l) => String(l).toLowerCase())
    const uniq = [...new Set(raw)]
    const pt = uniq.filter((l) => l === "pt-br" || l === "pt")
    const en = uniq.filter((l) => l === "en")
    const rest = uniq.filter((l) => !["pt-br", "pt", "en"].includes(l)).sort()
    const ordered = [...pt, ...en, ...rest]
    return ordered.length ? ordered : ["pt-br"]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manga?.id, meta])
  const [chapterLang, setChapterLang] = useState("pt-br")
  useEffect(() => {
    setChapterLang(preferredChapterLanguage(manga?.chapter_languages))
  }, [manga?.id, manga?.source_url, manga?.chapter_languages])

  useEffect(() => {
    if (!manga?.source_url) return undefined
    const controller = new AbortController()
    setShowChaptersLoader(false)
    setLoadingChapters(true)
    setChapterError("")
    setChapters([])
    setResolvedSource("")
    setSourceChanged(false)
    setOpenedChapter(null)
    setLoadingChapter(false)
    setFirstChapterPageLoaded(false)
    setOpenChapterError("")
    setMeta(null)
    setAuthorPanel(null)
    setReaderSidebarOpen(false)
    setReaderSidebarCollapsed(false)
    setReaderBrightness(100)
    setReaderZoom(100)

    const load = async () => {
      try {
        const params = new URLSearchParams({
          source_url: manga.source_url,
          title: manga.title ?? "",
          lang: chapterLang,
        })
        const response = await fetch(`${API_BASE_URL}/api/chapters?${params}`, {
          signal: controller.signal,
          headers: { Accept: "application/json" },
        })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const payload = await response.json()
        setChapters(payload.chapters ?? [])
        setMeta(payload.manga ?? null)
        setResolvedSource(payload.resolved_source || payload.provider || "")
        setSourceChanged(
          Boolean(payload.resolved_source_url)
          && payload.resolved_source_url !== payload.requested_source_url,
        )

        const metaParams = new URLSearchParams({
          source_url: payload.resolved_source_url || manga.source_url,
          title: manga.title ?? "",
        })
        void fetch(`${API_BASE_URL}/api/manga-meta?${metaParams}`, {
          signal: controller.signal,
          headers: { Accept: "application/json" },
        })
          .then((metaResponse) => {
            if (!metaResponse.ok) throw new Error(`HTTP ${metaResponse.status}`)
            return metaResponse.json()
          })
          .then((fullMeta) => {
            if (!controller.signal.aborted) setMeta(fullMeta)
          })
          .catch(() => {})
      } catch (err) {
        if (err.name !== "AbortError") {
          setChapterError("Nao consegui carregar os capitulos dessa fonte.")
        }
      } finally {
        if (!controller.signal.aborted) setLoadingChapters(false)
      }
    }

    load()
    return () => controller.abort()
  }, [manga, chapterLang])

  const sourceLabel = resolvedSource || manga?.source || ""
  const orderedChapters = [...chapters].sort((a, b) => {
    const numberA = Number(a.number ?? a.number_text)
    const numberB = Number(b.number ?? b.number_text)
    if (Number.isFinite(numberA) && Number.isFinite(numberB)) {
      return numberA - numberB
    }
    return String(a.label ?? "").localeCompare(String(b.label ?? ""), "pt-BR", { numeric: true })
  })
  if (chaptersDescending) orderedChapters.reverse()
  const textBlocks = useMemo(
    () => chapterTextBlocks(openedChapter?.content),
    [openedChapter?.content],
  )
  const richTextBlocks = useMemo(
    () => Array.isArray(openedChapter?.blocks)
      ? openedChapter.blocks.filter((block) => block?.type === "text" || (block?.type === "image" && block?.src))
      : [],
    [openedChapter?.blocks],
  )

  const openChapter = useCallback(async (chapter) => {
    if (!chapter?.url) return
    // Capitulo licenciado/externo (ex.: MangaPlus): MangaDex nao serve paginas.
    // Abre o link oficial em nova aba em vez de tentar ler no proxy.
    if (chapter.external_url) {
      window.open(chapter.external_url, "_blank", "noopener,noreferrer")
      return
    }
    try {
      window.localStorage.setItem(READER_SESSION_STORAGE_KEY, JSON.stringify({
        manga,
        manga_key: mangaStorageKey(manga),
        chapter_url: chapter.url,
        chapter_lang: chapterLang,
      }))
    } catch {
      // Leitor continua funcionando caso armazenamento local esteja indisponivel.
    }
    setLoadingChapter(true)
    setLoaderTick((t) => t + 1)
    setFirstChapterPageLoaded(false)
    setOpenChapterError("")
    setOpenedChapter(null)
    try {
      const params = new URLSearchParams({
        source_url: chapter.url,
        lang: chapterLang,
      })
      if (manga?.source_url) params.set("fallback_source_url", manga.source_url)
      if (manga?.title) params.set("title", manga.title)
      if (chapter.number_text || chapter.number != null) {
        params.set("chapter_number", String(chapter.number_text ?? chapter.number))
      }
      const response = await fetch(`${API_BASE_URL}/api/chapter?${params}`, {
        headers: { Accept: "application/json" },
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const payload = await response.json()
      // Backend sinalizou capitulo externo (sem paginas): abre o oficial.
      if (payload.external_url && !(payload.images?.length)) {
        window.open(payload.external_url, "_blank", "noopener,noreferrer")
        setLoadingChapter(false)
        return
      }
      setOpenedChapter(payload)
      onRead?.(manga)
    } catch (_err) {
      setOpenChapterError("Nao consegui abrir esse capitulo.")
    } finally {
      setLoadingChapter(false)
    }
  }, [chapterLang, manga, onRead])

  useEffect(() => {
    const session = readReaderSession()
    if (!session || session.manga_key !== mangaStorageKey(manga)) return
    if (session.chapter_lang && session.chapter_lang !== chapterLang) {
      setChapterLang(session.chapter_lang)
      return
    }
    const restoreKey = `${session.manga_key}:${session.chapter_url}:${session.chapter_lang || chapterLang}`
    if (restoredReaderKey.current === restoreKey) return
    restoredReaderKey.current = restoreKey
    void openChapter({ url: session.chapter_url })
  }, [chapterLang, manga, openChapter])

  const openAuthor = useCallback(async (name) => {
    const cleanName = authorName(name)
    if (!cleanName) return
    const requestKey = `${manga?.id ?? manga?.source_url ?? manga?.title}:${cleanName}`
    setAuthorPanel({
      requestKey,
      name: cleanName,
      loading: true,
      error: "",
      data: null,
    })

    try {
      const params = new URLSearchParams({
        name: cleanName,
        title: detail?.title || manga?.title || "",
        source_url: manga?.source_url || "",
      })
      const response = await fetch(`${API_BASE_URL}/api/authors/lookup?${params}`, {
        headers: { Accept: "application/json" },
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload.detail || `HTTP ${response.status}`)
      }
      setAuthorPanel((current) =>
        current?.requestKey === requestKey
          ? { ...current, loading: false, error: "", data: payload }
          : current,
      )
    } catch (err) {
      setAuthorPanel((current) =>
        current?.requestKey === requestKey
          ? {
              ...current,
              loading: false,
              error: err.message || "Nao consegui carregar esse autor no AniList.",
            }
          : current,
      )
    }
  }, [detail?.title, manga?.id, manga?.source_url, manga?.title])

  const goToAdjacentChapter = useCallback((direction) => {
    if (!openedChapter || loadingChapter) return
    const target = direction < 0 ? openedChapter.previous : openedChapter.next
    if (target) openChapter({ url: target })
  }, [loadingChapter, openChapter, openedChapter])

  const closeReader = useCallback(() => {
    // Fecha so leitor fullscreen. Painel da obra continua aberto com capitulos.
    clearReaderSession()
    setOpenedChapter(null)
    setLoadingChapter(false)
    setFirstChapterPageLoaded(false)
    setOpenChapterError("")
  }, [])

  useEffect(() => {
    if (!openedChapter) return undefined
    const onKeyDown = (event) => {
      if (event.key === "ArrowLeft") {
        event.preventDefault()
        goToAdjacentChapter(-1)
      } else if (event.key === "ArrowRight") {
        event.preventDefault()
        goToAdjacentChapter(1)
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [goToAdjacentChapter, openedChapter])

  useEffect(() => {
    const images = openedChapter?.images ?? []
    if (!images.length) return undefined
    const preloads = images.slice(0, 2).map((image) => {
      const img = new Image()
      img.decoding = "async"
      img.src = resolveApiUrl(image.src)
      return img
    })
    return () => {
      preloads.forEach((img) => {
        img.src = ""
      })
    }
  }, [openedChapter])

  if (!manga) return null

  return (
    <>
      <aside className="fixed inset-y-0 right-0 z-20 flex w-full max-w-xl flex-col border-l border-line bg-panel shadow-2xl">
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 className="line-clamp-1 text-lg font-black text-zinc-50">{manga.title}</h2>
            <p className="text-xs text-muted">
              {sourceLabel}
              {sourceChanged ? " - fonte completa escolhida" : ""}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onToggleFavorite?.(manga)}
              aria-label={isFavorite ? "Remover dos favoritos" : "Adicionar aos favoritos"}
              title={isFavorite ? "Remover dos favoritos" : "Adicionar aos favoritos"}
              className={`grid h-9 w-9 place-items-center rounded border transition ${
                isFavorite
                  ? "border-rose-300/50 bg-rose-300/15 text-rose-200"
                  : "border-line bg-soft text-zinc-300 hover:border-zinc-500 hover:text-rose-200"
              }`}
            >
              <Heart size={17} fill={isFavorite ? "currentColor" : "none"} strokeWidth={1.8} aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-line bg-soft px-3 py-1.5 text-sm text-zinc-100 hover:border-zinc-500"
            >
              Fechar
            </button>
          </div>
        </div>

        <div className="overflow-y-auto px-5 py-5">
          <div className="grid grid-cols-[132px_1fr] gap-4">
            {detail.cover_path || detail.cover_url ? (
              <img
                src={resolveApiUrl(detail.cover_path || detail.cover_url)}
                alt={`Capa de ${manga.title}`}
                className="h-48 w-32 rounded object-cover"
                loading="eager"
                decoding="async"
              />
            ) : (
              <div className="flex h-48 w-32 items-center justify-center rounded bg-soft text-4xl font-black text-zinc-700">
                {manga.title?.slice(0, 1)}
              </div>
            )}

            <div className="space-y-3 text-sm">
              <div className="flex flex-wrap gap-2">
                {detail.genres?.slice(0, 6).map((genre) => (
                  <button
                    key={genre}
                    type="button"
                    onClick={() => onGenreSelect?.(genre)}
                    title={`Ver obras de ${genre}`}
                    className="rounded border border-line bg-soft px-2 py-1 text-xs text-muted transition hover:border-zinc-500 hover:text-zinc-100"
                  >
                    {genre}
                  </button>
                ))}
              </div>
              <div>
                {descriptions.length > 1 && (
                  <div className="mb-2 flex flex-wrap gap-1.5">
                    {descriptions.map((d) => (
                      <button
                        key={d.lang}
                        type="button"
                        onClick={() => setDescLang(d.lang)}
                        title={d.auto ? "Traduzido automaticamente" : ""}
                        className={`rounded px-2 py-0.5 text-[11px] font-semibold transition ${
                          d.lang === descLang
                            ? "bg-accent text-app"
                            : "border border-line bg-soft text-muted hover:border-zinc-500"
                        }`}
                      >
                        {langLabel(d.lang)}{d.auto ? "*" : ""}
                      </button>
                    ))}
                  </div>
                )}
                <p className="text-muted">
                  {activeDesc?.text || "Sem sinopse disponivel nessa fonte."}
                </p>
              </div>
              {authorNames.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="text-zinc-500">Autores:</span>
                  {authorNames.slice(0, 4).map((name) => (
                    <button
                      key={name}
                      type="button"
                      onClick={() => openAuthor(name)}
                      className="rounded border border-line bg-soft px-2 py-0.5 font-semibold text-zinc-300 transition hover:border-zinc-500 hover:text-white"
                      title={`Ver informacoes sobre ${name}`}
                    >
                      {name}
                    </button>
                  ))}
                </div>
              )}
              {detail.mangaupdates_url && (
                <a
                  href={detail.mangaupdates_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs font-semibold text-zinc-500 transition hover:text-zinc-300"
                >
                  Dados: MangaUpdates
                </a>
              )}
            </div>
          </div>

          <section className="mt-5 border-y border-line/70 py-4" aria-label="Minha lista">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-bold text-zinc-100">Minha lista</h3>
              {libraryEntry && <span className="text-[10px] font-semibold uppercase text-accent">Salvo</span>}
            </div>
            <div className="mt-3 grid grid-cols-[1fr_84px] gap-2">
              <label className="text-xs text-muted">
                Status
                <select value={libraryStatus} onChange={(event) => setLibraryStatus(event.target.value)} className="mt-1 h-9 w-full rounded border border-line bg-soft px-2 text-xs text-zinc-100 outline-none focus:border-accent">
                  <option value="COMPLETED">Concluido</option>
                  <option value="CURRENT">Lendo</option>
                  <option value="PLANNING">Planejo ler</option>
                  <option value="PAUSED">Pausado</option>
                  <option value="REPEATING">Relendo</option>
                  <option value="DROPPED">Abandonado</option>
                </select>
              </label>
              <label className="text-xs text-muted">
                Nota
                <input type="number" min="0" max="10" step="0.5" value={libraryScore} onChange={(event) => setLibraryScore(event.target.value)} placeholder="0-10" className="mt-1 h-9 w-full rounded border border-line bg-soft px-2 text-xs text-zinc-100 outline-none focus:border-accent" />
              </label>
            </div>
            <label className="mt-3 block text-xs text-muted">
              Resenha
              <textarea value={libraryReview} onChange={(event) => setLibraryReview(event.target.value)} maxLength={4000} rows={3} placeholder="O que voce achou desta obra?" className="mt-1 w-full resize-y rounded border border-line bg-soft px-2 py-2 text-xs text-zinc-100 outline-none focus:border-accent" />
            </label>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button type="button" onClick={saveLibrary} disabled={savingLibrary || deletingLibrary} className="flex h-9 items-center gap-1 rounded border border-accent/40 bg-accent/10 px-3 text-xs font-bold text-accent hover:bg-accent/20 disabled:opacity-60">
                {savingLibrary ? <Loader2 size={13} className="animate-spin" /> : <BookText size={13} />} {libraryEntry ? "Atualizar lista" : "Salvar na minha lista"}
              </button>
              {libraryEntry && (
                <button type="button" onClick={deleteLibrary} disabled={savingLibrary || deletingLibrary} title="Remover da minha lista" className="grid h-9 w-9 place-items-center rounded border border-rose-300/40 bg-rose-300/10 text-rose-200 transition hover:bg-rose-300/20 disabled:opacity-60">
                  {deletingLibrary ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} aria-hidden="true" />}
                </button>
              )}
            </div>
            {libraryMessage && <p className="mt-2 text-xs text-accent">{libraryMessage}</p>}
          </section>

          <div className="mt-6">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-base font-bold text-zinc-100">Capitulos</h3>
              <div className="flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => setChaptersDescending((current) => !current)}
                  title={chaptersDescending ? "Mostrar primeiro capítulo no topo" : "Mostrar último capítulo no topo"}
                  aria-label={chaptersDescending ? "Mostrar primeiro capítulo no topo" : "Mostrar último capítulo no topo"}
                  className="grid h-7 w-7 place-items-center rounded border border-line bg-soft text-muted transition hover:border-zinc-500 hover:text-zinc-100"
                >
                  <ArrowUpDown size={14} aria-hidden="true" />
                </button>
                {chapterLangs.length > 1 && (
                  <>
                  <span className="text-xs text-muted">Idioma:</span>
                  {chapterLangs.map((l) => (
                    <button
                      key={l}
                      type="button"
                      onClick={() => setChapterLang(l)}
                      className={`rounded px-2 py-0.5 text-[11px] font-semibold transition ${
                        l === chapterLang
                          ? "bg-accent text-app"
                          : "border border-line bg-soft text-muted hover:border-zinc-500"
                      }`}
                    >
                      {langLabel(l)}
                    </button>
                  ))}
                  </>
                )}
              </div>
            </div>
            {loadingChapters ? (
              <div
                className="mt-3 flex min-h-[260px] flex-col items-center justify-center py-6"
                role="status"
                aria-label="Carregando capitulos"
                aria-live="polite"
              >
                {showChaptersLoader && (
                  <>
                    <p className="text-sm text-muted">Carregando capitulos...</p>
                    <img
                      key={`${manga?.source_url ?? manga?.id ?? "manga"}-${chapterLang}`}
                      src={CHAPTER_LOADER_SRC}
                      alt=""
                      aria-hidden="true"
                      width="352"
                      height="531"
                      fetchPriority="high"
                      decoding="async"
                      className="mt-3 h-48 w-auto max-h-[35vh] max-w-[65vw]"
                    />
                  </>
                )}
              </div>
            ) : chapterError ? (
              <p className="mt-3 text-sm text-red-300">{chapterError}</p>
            ) : (
              <div className="mt-3 grid gap-2">
                {orderedChapters.map((chapter) => (
                  <button
                    key={chapter.url}
                    type="button"
                    onClick={() => openChapter(chapter)}
                    disabled={loadingChapter}
                    className="rounded border border-line bg-app px-3 py-2 text-left text-sm text-zinc-200 transition hover:border-zinc-500 disabled:cursor-wait disabled:opacity-60"
                  >
                    {chapter.label}
                    {chapter.title ? <span className="text-muted"> - {chapter.title}</span> : null}
                    {chapter.external_url ? <span className="ml-1 text-[11px] text-accent">↗ oficial</span> : null}
                  </button>
                ))}
                {orderedChapters.length === 0 && <p className="text-sm text-muted">Nenhum capitulo encontrado.</p>}
              </div>
            )}
            {openChapterError && <p className="mt-3 text-sm text-red-300">{openChapterError}</p>}
          </div>
        </div>
      </aside>

      <AuthorInfoModal panel={authorPanel} onClose={() => setAuthorPanel(null)} />

      {(loadingChapter || openedChapter) && (
        <div
          className="fixed inset-0 z-30 flex flex-col bg-app"
          onTouchStart={(event) => {
            const touch = event.touches[0]
            readerSwipeStart.current = touch ? { x: touch.clientX, y: touch.clientY } : null
          }}
          onTouchEnd={(event) => {
            const start = readerSwipeStart.current
            const touch = event.changedTouches[0]
            readerSwipeStart.current = null
            if (!start || !touch) return
            const dx = touch.clientX - start.x
            const dy = touch.clientY - start.y
            if (Math.abs(dx) > 80 && Math.abs(dx) > Math.abs(dy) * 1.4) {
              goToAdjacentChapter(dx > 0 ? -1 : 1)
            }
          }}
        >
          <header className="sticky top-0 z-30 flex min-h-14 items-center justify-between border-b border-line bg-app/95 px-3 backdrop-blur sm:px-5">
            <button
              type="button"
              onClick={closeReader}
              aria-label="Fechar leitor"
              title="Fechar leitor"
              className="grid h-9 w-9 shrink-0 place-items-center text-xl font-black text-accent transition-all duration-200 hover:text-emerald-300 hover:drop-shadow-[0_0_10px_rgba(52,211,153,0.95)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            >
              K
            </button>
            <div className="min-w-0 px-3 text-center">
              <h2 className="truncate text-sm font-black text-zinc-50 sm:text-base">
                {manga.title}
              </h2>
              <p className="truncate text-[11px] text-muted">
                {openedChapter?.chapter?.label || "Carregando capitulo"}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setReaderSidebarOpen((open) => !open)}
                aria-expanded={readerSidebarOpen}
                aria-controls="reader-controls"
                className="grid h-8 w-8 place-items-center rounded border border-line bg-soft text-sm text-zinc-100 hover:border-zinc-500 lg:hidden"
                title="Controles de leitura"
              >
                =
              </button>
              <button
                type="button"
                onClick={closeReader}
                aria-label="Fechar leitor"
                title="Fechar leitor"
                className="grid h-9 w-9 place-items-center rounded border border-line bg-soft text-zinc-100 transition hover:border-zinc-500 hover:bg-zinc-800"
              >
                <X size={17} strokeWidth={2} aria-hidden="true" />
              </button>
            </div>
          </header>

          {readerSidebarOpen && (
            <button
              type="button"
              aria-label="Fechar controles de leitura"
              onClick={() => setReaderSidebarOpen(false)}
              className="fixed inset-0 z-20 bg-black/60 lg:hidden"
            />
          )}
          <aside
            id="reader-controls"
            className={`fixed bottom-0 left-0 top-14 z-20 flex w-72 flex-col border-r border-line bg-panel shadow-2xl transition-transform duration-200 ${
              readerSidebarOpen ? "translate-x-0" : "-translate-x-full"
            } ${readerSidebarCollapsed ? "lg:-translate-x-full" : "lg:translate-x-0"}`}
          >
            <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-4">
              <div className="min-w-0">
                <p className="text-xs font-bold uppercase text-zinc-500">Leitura</p>
                <p className="mt-1 truncate text-sm font-bold text-zinc-100">
                  {openedChapter?.chapter?.label || "Carregando capitulo"}
                </p>
                <p className="mt-1 text-xs text-muted">
                  {openedChapter?.mode === "text"
                    ? "Texto"
                    : openedChapter?.count
                      ? `${openedChapter.count} paginas`
                      : openedChapter?.provider || sourceLabel}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setReaderSidebarCollapsed(true)}
                aria-label="Minimizar controles de leitura"
                aria-controls="reader-controls"
                className="hidden h-8 w-8 shrink-0 place-items-center rounded border border-line bg-soft text-zinc-100 transition hover:border-zinc-500 lg:grid"
                title="Minimizar controles"
              >
                <PanelLeftClose size={17} strokeWidth={1.8} aria-hidden="true" />
              </button>
            </div>

            <div className="space-y-5 overflow-y-auto px-4 py-4">
              <label className="block text-xs font-semibold text-zinc-300" htmlFor="reader-chapter">
                Capitulo
              </label>
              <select
                id="reader-chapter"
                value={openedChapter?.chapter?.url || ""}
                disabled={loadingChapter || !openedChapter}
                onChange={(event) => {
                  const chapter = orderedChapters.find((item) => item.url === event.target.value)
                  if (chapter) openChapter(chapter)
                  setReaderSidebarOpen(false)
                }}
                className="-mt-3 h-10 w-full rounded border border-line bg-app px-3 text-sm text-zinc-100 outline-none focus:border-zinc-500"
              >
                <option value="" disabled>Escolher capitulo</option>
                {orderedChapters.map((chapter) => (
                  <option key={chapter.url} value={chapter.url}>{chapter.label}</option>
                ))}
              </select>

              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => goToAdjacentChapter(-1)}
                  disabled={!openedChapter?.previous || loadingChapter}
                  className="h-9 rounded border border-line bg-soft text-xs font-semibold text-zinc-200 transition hover:border-zinc-500 disabled:cursor-not-allowed disabled:opacity-35"
                >
                  Anterior
                </button>
                <button
                  type="button"
                  onClick={() => goToAdjacentChapter(1)}
                  disabled={!openedChapter?.next || loadingChapter}
                  className="h-9 rounded border border-line bg-soft text-xs font-semibold text-zinc-200 transition hover:border-zinc-500 disabled:cursor-not-allowed disabled:opacity-35"
                >
                  Proximo
                </button>
              </div>

              <div>
                <div className="mb-2 flex items-center justify-between text-xs font-semibold text-zinc-300">
                  <label htmlFor="reader-brightness">Brilho</label>
                  <output htmlFor="reader-brightness">{readerBrightness}%</output>
                </div>
                <input
                  id="reader-brightness"
                  type="range"
                  min="50"
                  max="150"
                  step="5"
                  value={readerBrightness}
                  onChange={(event) => setReaderBrightness(Number(event.target.value))}
                  className="w-full accent-zinc-100"
                />
              </div>

              <div>
                <div className="mb-2 flex items-center justify-between text-xs font-semibold text-zinc-300">
                  <label htmlFor="reader-zoom">{openedChapter?.mode === "text" ? "Fonte" : "Zoom"}</label>
                  <output htmlFor="reader-zoom">{readerZoom}%</output>
                </div>
                <input
                  id="reader-zoom"
                  type="range"
                  min="35"
                  max="180"
                  step="5"
                  value={readerZoom}
                  onChange={(event) => setReaderZoom(Number(event.target.value))}
                  className="w-full accent-zinc-100"
                />
              </div>

              <button
                type="button"
                onClick={() => {
                  setReaderBrightness(100)
                  setReaderZoom(100)
                }}
                className="h-9 w-full rounded border border-line bg-soft text-xs font-semibold text-zinc-300 transition hover:border-zinc-500 hover:text-white"
              >
                Restaurar leitura
              </button>
            </div>
          </aside>

          {readerSidebarCollapsed && (
            <button
              type="button"
              onClick={() => setReaderSidebarCollapsed(false)}
              aria-label="Mostrar controles de leitura"
              aria-controls="reader-controls"
              title="Mostrar controles"
              className="fixed left-3 top-20 z-10 hidden h-9 w-9 place-items-center rounded border border-white/10 bg-black/60 text-zinc-100 backdrop-blur transition hover:border-white/30 lg:grid"
            >
              <PanelLeftOpen size={18} strokeWidth={1.8} aria-hidden="true" />
            </button>
          )}

          <button
            type="button"
            onClick={() => goToAdjacentChapter(-1)}
            disabled={!openedChapter?.previous || loadingChapter}
            aria-label="Capitulo anterior"
            className={`fixed left-3 top-1/2 z-10 grid h-12 w-10 -translate-y-1/2 place-items-center rounded border border-white/10 bg-black/60 text-lg font-black text-zinc-100 backdrop-blur transition hover:border-white/30 disabled:pointer-events-none disabled:opacity-20 ${
              readerSidebarCollapsed ? "lg:left-3" : "lg:left-[19rem]"
            }`}
          >
            {"<"}
          </button>
          <button
            type="button"
            onClick={() => goToAdjacentChapter(1)}
            disabled={!openedChapter?.next || loadingChapter}
            aria-label="Proximo capitulo"
            className="fixed right-3 top-1/2 z-10 grid h-12 w-10 -translate-y-1/2 place-items-center rounded border border-white/10 bg-black/60 text-lg font-black text-zinc-100 backdrop-blur transition hover:border-white/30 disabled:pointer-events-none disabled:opacity-20"
          >
            {">"}
          </button>

          <main className={`relative min-h-0 flex-1 overflow-auto px-2 py-4 transition-[margin] duration-200 ${
            readerSidebarCollapsed ? "lg:ml-0" : "lg:ml-72"
          }`}>
            {(loadingChapter || (openedChapter?.images?.length > 0 && !firstChapterPageLoaded)) && (
              <div
                className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-app"
                role="status"
                aria-label="Carregando imagens do capitulo"
                aria-live="polite"
              >
                <img
                  key={loaderTick}
                  src={READER_LOADER_SRC}
                  alt=""
                  aria-hidden="true"
                  width="216"
                  height="404"
                  decoding="async"
                  className="h-48 w-auto max-h-[35vh] max-w-[65vw]"
                />
                <p className="mt-3 text-sm text-muted">Carregando capitulo...</p>
              </div>
            )}
            {openedChapter?.mode === "text" && (
              <article
                className="mx-auto w-full max-w-[820px] px-4 pb-24 pt-8 text-zinc-200 sm:px-8"
                style={{
                  fontSize: `${Math.max(14, Math.round(readerZoom * 0.18))}px`,
                  lineHeight: 1.85,
                  filter: `brightness(${readerBrightness}%)`,
                }}
              >
                <h1 className="mb-10 text-2xl font-black text-zinc-50">
                  {openedChapter?.chapter?.label || openedChapter?.title || "Capitulo"}
                </h1>
                {richTextBlocks.length ? richTextBlocks.map((block, index) => (
                  block.type === "image" ? (
                    <figure key={`${index}-${block.src}`} className="mb-8 overflow-hidden rounded border border-line/70 bg-panel">
                      <img
                        src={resolveReaderContentImage(block.src)}
                        alt={block.alt || "Ilustracao do capitulo"}
                        loading="lazy"
                        decoding="async"
                        className="mx-auto h-auto max-h-[85vh] max-w-full object-contain"
                      />
                    </figure>
                  ) : (
                    <p key={`${index}-${String(block.text || "").slice(0, 24)}`} className="mb-6 whitespace-pre-line">
                      {block.text}
                    </p>
                  )
                )) : textBlocks.map((block, index) => (
                  <p key={`${index}-${block.slice(0, 24)}`} className="mb-6 whitespace-pre-line">
                    {block}
                  </p>
                ))}
                {!richTextBlocks.length && !textBlocks.length && <p className="text-muted">Capitulo sem texto.</p>}
              </article>
            )}
            {openedChapter?.images?.length > 0 && (
              <div
                className="mx-auto flex flex-col items-center"
                style={{ width: `${readerZoom}%`, minWidth: `${readerZoom}%`, filter: `brightness(${readerBrightness}%)` }}
              >
                {openedChapter.images.map((image, index) => (
                  <img
                    key={`${image.index}-${image.src}`}
                    src={resolveApiUrl(image.src)}
                    alt={`Pagina ${image.index}`}
                    loading={index < 2 ? "eager" : "lazy"}
                    decoding="async"
                    fetchPriority={index === 0 ? "high" : "auto"}
                    draggable="false"
                    onLoad={index === 0 ? () => setFirstChapterPageLoaded(true) : undefined}
                    onError={(e) => {
                      // Fallback: se a carga direta (ex: MangaDex) falhar, tenta via proxy backend.
                      const el = e.currentTarget
                      const raw = image.src || ""
                      if (!el.dataset.proxied && /^https?:\/\//.test(raw)) {
                        el.dataset.proxied = "1"
                        el.src = `${API_BASE_URL}/api/image?url=${encodeURIComponent(raw)}`
                      } else if (index === 0) {
                        setFirstChapterPageLoaded(true)
                      }
                    }}
                    className="w-full bg-zinc-950 object-contain"
                  />
                ))}
              </div>
            )}
          </main>
        </div>
      )}
    </>
  )
}

function AuthPanel({ onClose, onSubmit }) {
  const [mode, setMode] = useState("login")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [email, setEmail] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [discordOn, setDiscordOn] = useState(false)
  const [googleOn, setGoogleOn] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE_URL}/api/auth/providers`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d || cancelled) return
        setDiscordOn(Boolean(d.discord?.configured))
        setGoogleOn(Boolean(d.google?.configured))
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  const loginExternal = async (provider, label) => {
    setError("")
    try {
      const r = await fetch(`${API_BASE_URL}/api/auth/${provider}/start`)
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setError(d.detail || `Login com ${label} indisponivel.`); return }
      const popup = window.open(d.authorize_url, `kari-${provider}`, "width=520,height=760")
      if (!popup) setError(`Habilite popups para entrar com o ${label}.`)
    } catch {
      setError(`Nao consegui abrir o ${label}.`)
    }
  }

  const submit = async (event) => {
    event.preventDefault()
    setError("")
    setBusy(true)
    try {
      await onSubmit(mode, { username: username.trim(), password, email: email.trim() })
    } catch (err) {
      setError(err.message || "Falha na autenticacao.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 py-6"
      role="dialog"
      aria-modal="true"
      aria-label="Autenticacao"
      onMouseDown={onClose}
    >
      <form
        onSubmit={submit}
        onMouseDown={(event) => event.stopPropagation()}
        className="w-full max-w-sm rounded-md border border-line bg-panel p-6 shadow-2xl"
      >
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-lg font-black text-zinc-50">{mode === "register" ? "Criar conta" : "Entrar"}</h2>
          <button type="button" onClick={onClose} className="grid h-8 w-8 place-items-center rounded border border-line text-zinc-300 hover:border-zinc-500" aria-label="Fechar">
            <X size={16} strokeWidth={2} aria-hidden="true" />
          </button>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-1 rounded bg-soft p-1 text-xs font-semibold">
          <button type="button" onClick={() => { setMode("login"); setError("") }} className={`h-8 rounded transition ${mode === "login" ? "bg-accent text-app" : "text-zinc-300 hover:text-white"}`}>Entrar</button>
          <button type="button" onClick={() => { setMode("register"); setError("") }} className={`h-8 rounded transition ${mode === "register" ? "bg-accent text-app" : "text-zinc-300 hover:text-white"}`}>Cadastrar</button>
        </div>

        <label className="mt-4 block text-xs font-semibold text-zinc-300">Usuario</label>
        <input
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          autoComplete="username"
          maxLength={32}
          className="mt-1 h-10 w-full rounded border border-line bg-app px-3 text-sm text-zinc-100 outline-none focus:border-accent"
        />

        {mode === "register" && (
          <>
            <label className="mt-3 block text-xs font-semibold text-zinc-300">Email (opcional)</label>
            <input
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              type="email"
              autoComplete="email"
              className="mt-1 h-10 w-full rounded border border-line bg-app px-3 text-sm text-zinc-100 outline-none focus:border-accent"
            />
          </>
        )}

        <label className="mt-3 block text-xs font-semibold text-zinc-300">Senha</label>
        <input
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          type="password"
          autoComplete={mode === "register" ? "new-password" : "current-password"}
          maxLength={128}
          className="mt-1 h-10 w-full rounded border border-line bg-app px-3 text-sm text-zinc-100 outline-none focus:border-accent"
        />
        {mode === "register" && <p className="mt-1 text-[11px] text-muted">Minimo 6 caracteres.</p>}

        {error && <p className="mt-3 text-sm text-red-300">{error}</p>}

        <button
          type="submit"
          disabled={busy || !username.trim() || !password}
          className="mt-5 flex h-10 w-full items-center justify-center gap-2 rounded bg-accent text-sm font-black text-app transition hover:bg-emerald-200 disabled:cursor-wait disabled:opacity-60"
        >
          {busy && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
          {mode === "register" ? "Criar conta" : "Entrar"}
        </button>

        {(discordOn || googleOn) && (
          <>
            <div className="my-4 flex items-center gap-3 text-[11px] text-muted">
              <span className="h-px flex-1 bg-line" /> ou <span className="h-px flex-1 bg-line" />
            </div>
            <div className="grid gap-2">
              {googleOn && (
                <button
                  type="button"
                  onClick={() => loginExternal("google", "Google")}
                  className="flex h-10 w-full items-center justify-center gap-2 rounded border border-zinc-300 bg-white text-sm font-bold text-zinc-800 transition hover:bg-zinc-100"
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
                    <path fill="#4285F4" d="M21.6 12.23c0-.71-.06-1.4-.18-2.07H12v3.91h5.38a4.6 4.6 0 0 1-2 3.02v2.54h3.24c1.9-1.75 2.98-4.33 2.98-7.4Z" />
                    <path fill="#34A853" d="M12 22c2.7 0 4.98-.9 6.63-2.37l-3.24-2.54c-.9.6-2.05.96-3.39.96-2.61 0-4.82-1.76-5.61-4.13H3.04v2.62A10 10 0 0 0 12 22Z" />
                    <path fill="#FBBC05" d="M6.39 13.92A6 6 0 0 1 6.08 12c0-.67.11-1.32.31-1.92V7.46H3.04A10 10 0 0 0 2 12c0 1.63.39 3.17 1.04 4.54l3.35-2.62Z" />
                    <path fill="#EA4335" d="M12 5.95c1.47 0 2.79.5 3.82 1.5l2.88-2.88A9.65 9.65 0 0 0 12 2a10 10 0 0 0-8.96 5.46l3.35 2.62C7.18 7.71 9.39 5.95 12 5.95Z" />
                  </svg>
                  Continuar com Google
                </button>
              )}
              {discordOn && (
                <button
                  type="button"
                  onClick={() => loginExternal("discord", "Discord")}
                  className="flex h-10 w-full items-center justify-center gap-2 rounded text-sm font-bold text-white transition hover:brightness-110"
                  style={{ backgroundColor: "#5865F2" }}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <path d="M20.317 4.369a19.79 19.79 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.211.375-.445.865-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.6 12.6 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.369a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.1 13.1 0 0 1-1.872-.892.077.077 0 0 1-.008-.128c.126-.094.252-.192.371-.291a.074.074 0 0 1 .078-.01c3.927 1.793 8.18 1.793 12.061 0a.074.074 0 0 1 .079.009c.12.099.245.198.372.292a.077.077 0 0 1-.006.127c-.598.35-1.22.645-1.873.891a.076.076 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.84 19.84 0 0 0 6.002-3.03.077.077 0 0 0 .032-.056c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.028zM8.02 15.331c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.211 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.211 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/>
                  </svg>
                  Continuar com Discord
                </button>
              )}
            </div>
          </>
        )}
      </form>
    </div>
  )
}


export default function App() {
  const [query, setQuery] = useState("")
  const [debouncedQuery, setDebouncedQuery] = useState("")
  const [selectedManga, setSelectedManga] = useState(() => readReaderSession()?.manga ?? null)
  const [catalogView, setCatalogView] = useState(false)
  const [libraryView, setLibraryView] = useState("")
  const [genreFilter, setGenreFilter] = useState("")
  const [catalogOffset, setCatalogOffset] = useState(0)
  const [catalogPages, setCatalogPages] = useState([])
  const [favorites, setFavorites] = useState(() => readStoredMangaList(FAVORITES_STORAGE_KEY))
  const [history, setHistory] = useState(() => readStoredMangaList(HISTORY_STORAGE_KEY))
  const [historyChapterUpdates, setHistoryChapterUpdates] = useState({})
  const [profile, setProfile] = useState(null)
  const [profileReady, setProfileReady] = useState(false)
  const [profilePanelOpen, setProfilePanelOpen] = useState(false)
  const [pluginView, setPluginView] = useState("")
  const [headerCollapsed, setHeaderCollapsed] = useState(false)
  const [headerHidden, setHeaderHidden] = useState(false)
  const [authToken, setAuthToken] = useState(() => window.localStorage.getItem(AUTH_TOKEN_KEY) || "")
  const [authOpen, setAuthOpen] = useState(false)
  const authed = Boolean(authToken)

  const headerHideTimer = useRef(null)
  const headerScrolled = useRef(false)

  const revealHeader = useCallback(() => {
    if (headerHideTimer.current) window.clearTimeout(headerHideTimer.current)
    setHeaderHidden(false)
  }, [])

  const requestHeaderHide = useCallback(() => {
    // Painel de obra ocupa viewport; navbar so reaparece enquanto mouse fica no topo.
    if (!selectedManga && window.scrollY <= 90) return
    if (headerHideTimer.current) window.clearTimeout(headerHideTimer.current)
    headerHideTimer.current = window.setTimeout(() => setHeaderHidden(true), 650)
  }, [selectedManga])

  const updateHeaderForScroll = useCallback((y) => {
    if (!headerScrolled.current && y > 90) {
      headerScrolled.current = true
      setHeaderCollapsed(true)
      setHeaderHidden(true)
    } else if (headerScrolled.current && y < 40) {
      headerScrolled.current = false
      setHeaderCollapsed(false)
      setHeaderHidden(false)
    }
  }, [])

  // Home usa scroll da janela; catalogo, busca e listas usam containers.
  // Captura ambos para navbar manter mesmo gesto em toda sessao.
  useEffect(() => {
    let ticking = false
    let pendingY = window.scrollY
    const evaluate = () => {
      ticking = false
      updateHeaderForScroll(pendingY)
    }
    const schedule = (y) => {
      pendingY = y
      if (ticking) return
      ticking = true
      window.requestAnimationFrame(evaluate)
    }
    const onWindowScroll = () => schedule(window.scrollY)
    const onContainerScroll = (event) => {
      const target = event.target
      if (target instanceof Element && target !== document.documentElement) {
        schedule(target.scrollTop)
      }
    }
    window.addEventListener("scroll", onWindowScroll, { passive: true })
    document.addEventListener("scroll", onContainerScroll, { capture: true, passive: true })
    schedule(window.scrollY)
    return () => {
      window.removeEventListener("scroll", onWindowScroll)
      document.removeEventListener("scroll", onContainerScroll, true)
      if (headerHideTimer.current) window.clearTimeout(headerHideTimer.current)
    }
  }, [updateHeaderForScroll])

  useEffect(() => {
    if (selectedManga) {
      if (headerHideTimer.current) window.clearTimeout(headerHideTimer.current)
      setHeaderCollapsed(true)
      setHeaderHidden(true)
      return
    }
    const scrolled = headerScrolled.current || window.scrollY > 90
    setHeaderCollapsed(scrolled)
    setHeaderHidden(scrolled)
  }, [selectedManga])
  const profileBootstrapStarted = useRef(false)

  useEffect(() => {
    window.localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(favorites))
  }, [favorites])

  useEffect(() => {
    window.localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(history))
  }, [history])

  useEffect(() => {
    if (profileBootstrapStarted.current) return undefined
    profileBootstrapStarted.current = true
    let cancelled = false

    const bootstrap = async () => {
      const localFavorites = readStoredMangaList(FAVORITES_STORAGE_KEY)

      // 1) Conta logada: usa o perfil da conta (token Bearer).
      const token = window.localStorage.getItem(AUTH_TOKEN_KEY) || ""
      if (token) {
        try {
          const meResp = await fetch(`${API_BASE_URL}/api/auth/me`, {
            headers: { Authorization: `Bearer ${token}` },
          })
          if (meResp.ok) {
            const me = await meResp.json()
            if (cancelled) return
            setFavorites(me.profile.favorites ?? [])
            setProfile(me.profile)
            setProfileReady(true)
            return
          }
          window.localStorage.removeItem(AUTH_TOKEN_KEY)
          if (!cancelled) setAuthToken("")
        } catch {
          window.localStorage.removeItem(AUTH_TOKEN_KEY)
        }
      }

      // 2) Convidado: perfil anonimo local.
      const storedId = window.localStorage.getItem(PROFILE_STORAGE_KEY)
      let response = storedId
        ? await fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(storedId)}`)
        : null
      if (!response?.ok) {
        response = await fetch(`${API_BASE_URL}/api/profiles`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: "Leitor" }),
        })
      }
      if (!response.ok) throw new Error("profile")
      let data = await response.json()
      window.localStorage.setItem(PROFILE_STORAGE_KEY, data.id)

      const mergedFavorites = mergeMangaLists(data.favorites, localFavorites)
      if (mergedFavorites.length !== (data.favorites ?? []).length) {
        const sync = await fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(data.id)}/favorites`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ favorites: mergedFavorites }),
        })
        if (sync.ok) data = await sync.json()
      }
      if (cancelled) return
      setFavorites(mergedFavorites)
      setProfile(data)
      setProfileReady(true)
    }

    bootstrap().catch(() => {
      if (!cancelled) setProfileReady(true)
    })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!profileReady || !profile?.id) return undefined
    const controller = new AbortController()
    void fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(profile.id)}/favorites`, {
      method: "PUT",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ favorites }),
    })
      .then((response) => response.ok ? response.json() : null)
      .then((data) => {
        if (data && !controller.signal.aborted) {
          setProfile((current) => current?.id === data.id ? data : current)
        }
      })
      .catch(() => {})
    return () => controller.abort()
  }, [favorites, profile?.id, profileReady])

  // Debounce do termo digitado (180ms) -> uma query por pausa, nao por tecla.
  // A queryKey usa o valor "debounced"; o input continua refletindo `query`.
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQuery(query.trim()), 180)
    return () => clearTimeout(id)
  }, [query])

  useEffect(() => {
    setCatalogOffset(0)
    setCatalogPages([])
  }, [debouncedQuery, genreFilter, catalogView])

  const pagedCatalog = catalogView || genreFilter.length > 0
  const requestOffset = pagedCatalog ? catalogOffset : 0

  // Fonte unica do catalogo (home/busca) via React Query.
  // queryKey distingue home ("") de cada termo de busca -> cada um tem seu cache.
  const catalogQuery = useQuery({
    queryKey: ["catalog", debouncedQuery, genreFilter, catalogView, requestOffset],
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams({ limit: String(CATALOG_PAGE_SIZE) })
      if (requestOffset) params.set("offset", String(requestOffset))
      if (genreFilter) params.set("genre", genreFilter)
      let endpoint = "/api/home"
      if (debouncedQuery) {
        params.set("q", debouncedQuery)
        endpoint = "/api/search"
      }
      const response = await fetch(`${API_BASE_URL}${endpoint}?${params}`, {
        signal,
        headers: { Accept: "application/json" },
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return response.json()
    },
    // Enquanto o backend aquece o catalogo (refreshing=true) na home, repoll 2.5s.
    refetchInterval: (q) =>
      !debouncedQuery && !genreFilter && q.state.data?.refreshing ? 2500 : false,
    // Ao pedir proxima pagina, conserva cards atuais. Busca/filtro diferentes
    // continuam com estado proprio, sem mostrar resultados de outra consulta.
    placeholderData: (previousData, previousQuery) => {
      const previousKey = previousQuery?.queryKey ?? []
      return (
        previousKey[1] === debouncedQuery
        && previousKey[2] === genreFilter
        && previousKey[3] === catalogView
      ) ? previousData : undefined
    },
  })

  const payload = catalogQuery.data
  const mangas = payload?.items ?? []
  const sections = payload?.sections ?? []
  const hydratedHistory = useMemo(() => {
    const profileByTitle = new Map()
    for (const item of profile?.library ?? []) {
      const key = mangaTitleKey(item)
      if (key) profileByTitle.set(key, item)
      const aliasKey = mangaTitleAliasKey(item)
      if (aliasKey) profileByTitle.set(aliasKey, item)
    }
    const catalogBySource = new Map()
    for (const item of [...mangas, ...sections.flatMap((section) => section.items ?? [])]) {
      const key = mangaStorageKey(item)
      if (key) catalogBySource.set(key, item)
    }
    return history.map((item) => {
      const currentProfileItem = profileByTitle.get(mangaTitleKey(item))
        ?? profileByTitle.get(mangaTitleAliasKey(item))
      const currentCatalogItem = catalogBySource.get(mangaStorageKey(item))
      return currentProfileItem
        ? { ...item, ...currentProfileItem }
        : currentCatalogItem
          ? { ...item, ...currentCatalogItem }
          : item
    })
  }, [history, mangas, profile?.library, sections])
  const refreshedHistory = useMemo(() => hydratedHistory.map((item) => ({
    ...item,
    ...(historyChapterUpdates[mangaStorageKey(item)] ?? {}),
  })), [historyChapterUpdates, hydratedHistory])
  const libraryItems = libraryView === "favorites"
    ? favorites
    : libraryView === "library"
      ? (profile?.library ?? [])
      : refreshedHistory
  const visibleMangas = libraryView ? libraryItems : pagedCatalog ? catalogPages : mangas
  const total = libraryView ? libraryItems.length : (payload?.total ?? mangas.length)
  // Skeleton SO no primeiro carregamento (sem dado em cache). Voltar do modal
  // serve o cache -> isPending=false -> aparece instantaneo.
  const loading = catalogQuery.isPending && !payload
  const error = catalogQuery.isError ? "Nao consegui carregar o catalogo." : ""

  const heroSection = (sections ?? []).find(
    (s) => s.layout === "carousel" && s.title === "Em alta",
  )
  const heroItems = heroSection?.items?.length ? heroSection.items : mangas.slice(0, 8)
  const catalogSections = (sections ?? []).filter(
    (s) => !(s.layout === "carousel" && s.title === "Em alta"),
  )

  const isSearching = debouncedQuery.length > 0 || genreFilter.length > 0
  const canLoadMore =
    !libraryView
    && pagedCatalog
    && catalogPages.length < total
    && catalogOffset < CATALOG_MAX_LIMIT - CATALOG_PAGE_SIZE

  useEffect(() => {
    if (!pagedCatalog || !payload || catalogQuery.isPlaceholderData) return
    if (Number(payload.offset ?? 0) !== requestOffset) return
    const incoming = payload.items ?? []
    setCatalogPages((current) => {
      if (requestOffset === 0) return incoming
      const seen = new Set(current.map(mangaStorageKey))
      return [...current, ...incoming.filter((item) => !seen.has(mangaStorageKey(item)))]
    })
  }, [catalogQuery.isPlaceholderData, pagedCatalog, payload, requestOffset])

  const handleHome = useCallback(() => {
    clearReaderSession()
    setQuery("")
    setDebouncedQuery("")
    setCatalogView(false)
    setLibraryView("")
    setPluginView("")
    setGenreFilter("")
    setSelectedManga(null)
    window.scrollTo({ top: 0, behavior: "smooth" })
  }, [])

  const handleCatalog = useCallback(() => {
    setQuery("")
    setDebouncedQuery("")
    setCatalogView(true)
    setLibraryView("")
    setPluginView("")
    setGenreFilter("")
    setSelectedManga(null)
    window.scrollTo({ top: 0, behavior: "smooth" })
  }, [])

  const handleGenreSelect = useCallback((genre) => {
    setQuery("")
    setDebouncedQuery("")
    setGenreFilter(String(genre || ""))
    setCatalogView(true)
    setLibraryView("")
    setPluginView("")
    setSelectedManga(null)
    window.scrollTo({ top: 0, behavior: "smooth" })
  }, [])

  const handleQueryChange = useCallback((value) => {
    setQuery(value)
    if (String(value || "").trim()) {
      setLibraryView("")
      setPluginView("")
      setGenreFilter("")
    }
  }, [])

  const showLibrary = useCallback((view) => {
    setQuery("")
    setDebouncedQuery("")
    setCatalogView(false)
    setGenreFilter("")
    setPluginView("")
    setLibraryView(view)
    setSelectedManga(null)
    if ((view === "library" || view === "favorites" || view === "history") && profile?.id) {
      void fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(profile.id)}`)
        .then((response) => response.ok ? response.json() : null)
        .then((data) => {
          if (!data) return
          setProfile(data)
          if (Array.isArray(data.favorites)) setFavorites(data.favorites)
        })
        .catch(() => {})
    }
    window.scrollTo({ top: 0, behavior: "smooth" })
  }, [profile?.id])

  useEffect(() => {
    if (libraryView !== "history" || !hydratedHistory.length) return undefined
    let cancelled = false
    let retryTimer = null
    let attempts = 0

    const refreshCards = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/chapter-cards/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: hydratedHistory.slice(0, 80) }),
        })
        if (!response.ok || cancelled) return
        const data = await response.json()
        if (data?.items) setHistoryChapterUpdates((current) => ({ ...current, ...data.items }))
        // Auditoria roda em fila compartilhada com home. Continua consultando sem
        // bloquear UI; antes parava apos 20s e deixava skeleton congelado.
        if (data?.refreshing && attempts++ < 40 && !cancelled) {
          retryTimer = window.setTimeout(refreshCards, 1500)
        }
      } catch {
        // Mantem ultimo snapshot; nova abertura tenta novamente.
      }
    }

    void refreshCards()
    return () => {
      cancelled = true
      if (retryTimer) window.clearTimeout(retryTimer)
    }
  }, [hydratedHistory, libraryView])

  const showPlugin = useCallback((view) => {
    setQuery("")
    setDebouncedQuery("")
    setCatalogView(false)
    setGenreFilter("")
    setLibraryView("")
    setPluginView(view)
    setSelectedManga(null)
    window.scrollTo({ top: 0, behavior: "smooth" })
  }, [])

  const openProfile = useCallback(() => {
    setProfilePanelOpen(true)
  }, [])

  const handleAuth = useCallback(async (mode, fields) => {
    const endpoint = mode === "register" ? "register" : "login"
    const body = mode === "register"
      ? { username: fields.username, password: fields.password, email: fields.email || "" }
      : { username: fields.username, password: fields.password }
    const resp = await fetch(`${API_BASE_URL}/api/auth/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) throw new Error(data.detail || "Falha na autenticacao.")
    window.localStorage.setItem(AUTH_TOKEN_KEY, data.token)
    setAuthToken(data.token)
    setProfile(data.profile)
    setFavorites(data.profile.favorites ?? [])
    setAuthOpen(false)
  }, [])

  const handleLogout = useCallback(() => {
    const token = window.localStorage.getItem(AUTH_TOKEN_KEY)
    if (token) {
      void fetch(`${API_BASE_URL}/api/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      }).catch(() => {})
    }
    window.localStorage.removeItem(AUTH_TOKEN_KEY)
    window.localStorage.removeItem(PROFILE_STORAGE_KEY)
    setAuthToken("")
    window.location.reload()
  }, [])

  // Recebe o token de login externo (Discord) via popup postMessage.
  const handleAuthToken = useCallback(async (token) => {
    window.localStorage.setItem(AUTH_TOKEN_KEY, token)
    setAuthToken(token)
    try {
      const resp = await fetch(`${API_BASE_URL}/api/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (resp.ok) {
        const me = await resp.json()
        setProfile(me.profile)
        setFavorites(me.profile.favorites ?? [])
      }
    } catch {
      /* ignore */
    }
    setAuthOpen(false)
  }, [])

  useEffect(() => {
    const onMessage = (event) => {
      const data = event.data
      if (!data || data.source !== "kari-auth") return
      if (data.ok && data.token) handleAuthToken(data.token)
    }
    window.addEventListener("message", onMessage)
    return () => window.removeEventListener("message", onMessage)
  }, [handleAuthToken])

  const saveProfile = useCallback(async (displayName) => {
    if (!profile?.id) throw new Error("profile")
    const response = await fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(profile.id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: displayName }),
    })
    if (!response.ok) throw new Error("profile")
    setProfile(await response.json())
  }, [profile?.id])

  const applyProfileChange = useCallback((nextProfile) => {
    if (!nextProfile) return
    setProfile(nextProfile)
    if (Array.isArray(nextProfile.favorites)) setFavorites(nextProfile.favorites)
  }, [])

  const refreshProfile = useCallback(async () => {
    if (!profile?.id) return
    const response = await fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(profile.id)}`)
    if (!response.ok) return
    applyProfileChange(await response.json())
  }, [applyProfileChange, profile?.id])

  const rememberRead = useCallback((manga) => {
    const key = mangaStorageKey(manga)
    if (!key) return
    setHistory((items) => [manga, ...items.filter((item) => mangaStorageKey(item) !== key)].slice(0, 100))
  }, [])

  const toggleFavorite = useCallback((manga) => {
    const key = mangaStorageKey(manga)
    if (!key) return
    setFavorites((items) => (
      items.some((item) => mangaStorageKey(item) === key)
        ? items.filter((item) => mangaStorageKey(item) !== key)
        : [manga, ...items]
    ))
  }, [])

  const saveLibraryEntry = useCallback(async (manga, entry) => {
    if (!profile?.id) throw new Error("profile")
    const response = await fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(profile.id)}/library`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item: manga, ...entry }),
    })
    const data = await response.json().catch(() => null)
    if (!response.ok) throw new Error(data?.detail || "Nao consegui salvar na lista.")
    applyProfileChange(data?.profile ?? data)
    return data
  }, [applyProfileChange, profile?.id])

  const deleteLibraryEntry = useCallback(async (entry) => {
    if (!profile?.id) throw new Error("profile")
    const response = await fetch(`${API_BASE_URL}/api/profiles/${encodeURIComponent(profile.id)}/library`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item: entry }),
    })
    const data = await response.json().catch(() => null)
    if (!response.ok) throw new Error(data?.detail || "Nao consegui remover da lista.")
    applyProfileChange(data?.profile ?? data)
    return data
  }, [applyProfileChange, profile?.id])

  const selectedIsFavorite = Boolean(selectedManga && favorites.some(
    (item) => mangaStorageKey(item) === mangaStorageKey(selectedManga),
  ))

  const handleDetailClose = useCallback(() => {
    clearReaderSession()
    setSelectedManga(null)
    void refreshProfile()
    void catalogQuery.refetch()
  }, [catalogQuery, refreshProfile])

  const homeBackground = profile?.home_background_url
    ? resolveApiUrl(profile.home_background_url)
    : ""

  return (
    <div className="relative min-h-screen bg-app text-zinc-100">
      {homeBackground && (
        isVideoUrl(homeBackground) ? (
          <video
            src={homeBackground}
            autoPlay
            muted
            loop
            playsInline
            aria-hidden="true"
            className="pointer-events-none fixed inset-0 h-screen w-screen object-cover"
            style={{ zIndex: 0 }}
          />
        ) : (
          <img
            src={homeBackground}
            alt=""
            aria-hidden="true"
            className="pointer-events-none fixed inset-0 h-screen w-screen object-cover"
            style={{ zIndex: 0 }}
          />
        )
      )}
      <div className="relative" style={{ zIndex: 1 }}>
      <Header
        query={query}
        onQueryChange={handleQueryChange}
        onHome={handleHome}
        onCatalog={handleCatalog}
        onPluginSelect={showPlugin}
        onHistory={() => showLibrary("history")}
        onFavorites={() => showLibrary("favorites")}
        onProfile={openProfile}
        profile={profile}
        libraryView={libraryView}
        pluginView={pluginView}
        activeGenre={genreFilter}
        onClearGenre={handleCatalog}
        total={total}
        isSearching={isSearching}
        collapsed={headerCollapsed}
        hidden={headerHidden}
        onReveal={revealHeader}
        onRequestHide={requestHeaderHide}
      />
      {headerHidden && (
        <div
          className="fixed inset-x-0 top-0 z-20 h-3"
          onMouseEnter={revealHeader}
          aria-hidden="true"
        />
      )}
      {!pluginView && error && (
        <div className="mx-5 mt-4 rounded-md border border-red-900 bg-red-950/30 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}
      {pluginView ? (
        pluginView === "hq" ? (
          <HQNowPluginPage onOpen={setSelectedManga} />
        ) : pluginView === "novels" ? (
          <WebNovelsPluginPage
            onOpen={setSelectedManga}
            onChanged={() => void catalogQuery.refetch()}
          />
        ) : (
          <PluginLibraryPage
            kind="hq"
            onOpen={setSelectedManga}
            onChanged={() => void catalogQuery.refetch()}
          />
        )
      ) : loading ? (
        <SkeletonGrid />
      ) : isSearching || catalogView || libraryView ? (
        visibleMangas.length ? (
          debouncedQuery ? (
            <SearchSourceColumns mangas={visibleMangas} onSelect={setSelectedManga} />
          ) : (
            <VirtualMangaGrid
              mangas={visibleMangas}
              onSelect={setSelectedManga}
              canLoadMore={canLoadMore}
              loadingMore={catalogQuery.isFetching && requestOffset > 0}
              onLoadMore={() => setCatalogOffset((offset) => Math.min(
                offset + CATALOG_PAGE_SIZE,
                CATALOG_MAX_LIMIT - CATALOG_PAGE_SIZE,
              ))}
            />
          )
        ) : (
          <SearchEmptyState query={libraryView === "history" ? "historico" : libraryView === "favorites" ? "favoritos" : libraryView === "library" ? "minha lista" : catalogView ? "catalogo" : debouncedQuery} />
        )
      ) : (
        <>
          <HeroCarousel items={heroItems} onSelect={setSelectedManga} onGenreSelect={handleGenreSelect} />
          <SectionedCatalog sections={catalogSections} items={mangas} onSelect={setSelectedManga} />
        </>
      )}
      <MangaDetailPanel
        manga={selectedManga}
        onClose={handleDetailClose}
        onGenreSelect={handleGenreSelect}
        isFavorite={selectedIsFavorite}
        onToggleFavorite={toggleFavorite}
        onRead={rememberRead}
        libraryEntry={selectedManga ? (profile?.library ?? []).find((item) => mangaStorageKey(item) === mangaStorageKey(selectedManga)) : null}
        onSaveLibrary={saveLibraryEntry}
        onDeleteLibrary={deleteLibraryEntry}
      />
      {profilePanelOpen && (
        <ProfilePanel
          profile={profile}
          historyCount={history.length}
          onClose={() => setProfilePanelOpen(false)}
          onSave={saveProfile}
          onProfileChange={applyProfileChange}
          onShowHistory={() => { setProfilePanelOpen(false); showLibrary("history") }}
          onShowFavorites={() => { setProfilePanelOpen(false); showLibrary("favorites") }}
          onShowLibrary={() => { setProfilePanelOpen(false); showLibrary("library") }}
          authed={authed}
          onOpenAuth={() => { setProfilePanelOpen(false); setAuthOpen(true) }}
          onLogout={handleLogout}
        />
      )}
      {authOpen && (
        <AuthPanel onClose={() => setAuthOpen(false)} onSubmit={handleAuth} />
      )}
      </div>
    </div>
  )
}
