import { memo, useEffect, useMemo, useRef, useState } from "react"
import { BookOpen, Clock3, Globe2 } from "lucide-react"

const API_BASE_URL = import.meta.env.VITE_DESKTOP_BUILD === "1"
  ? window.location.origin
  : (import.meta.env.VITE_API_BASE_URL || window.location.origin)
const PLACEHOLDER_URL = `${API_BASE_URL}/static/placeholder.svg`

function resolveImageUrl(url) {
  if (!url) return ""
  return url.startsWith("/") ? `${API_BASE_URL}${url}` : url
}

export function MangaCardSkeleton() {
  return (
    <div className="flex min-h-[156px] gap-3 rounded-md border border-line bg-panel p-3 shadow-card">
      <div className="kari-skeleton h-32 w-[92px] shrink-0 rounded" />
      <div className="flex-1 space-y-2 py-1">
        <div className="kari-skeleton h-4 w-3/4 rounded" />
        <div className="kari-skeleton h-3 w-full rounded" />
        <div className="kari-skeleton h-3 w-2/3 rounded" />
        <div className="mt-5 flex gap-2"><span className="kari-skeleton h-3 w-16 rounded" /><span className="kari-skeleton h-3 w-10 rounded" /></div>
      </div>
    </div>
  )
}

function MangaCard({ manga, priority = false, compact = false, onSelect }) {
  const [loaded, setLoaded] = useState(false)
  const [srcIndex, setSrcIndex] = useState(0)
  const [broken, setBroken] = useState(false)
  const imageRef = useRef(null)
  // Chave estavel das capas: o repoll da home recria o objeto `manga` (e o array
  // cover_fallbacks) a cada 2.5s. Depender do array cru resetava o estado da
  // imagem -> capa piscava. Uma string derivada so muda quando a capa muda.
  const coverFallbacksKey = (manga.cover_fallbacks ?? []).join("|")
  const coverUrls = useMemo(
    () => [manga.cover_path, manga.cover_url, ...(manga.cover_fallbacks ?? [])].filter(Boolean),
    [manga.cover_path, manga.cover_url, coverFallbacksKey],
  )
  const currentCover =
    !broken && srcIndex < coverUrls.length
      ? resolveImageUrl(coverUrls[srcIndex])
      : PLACEHOLDER_URL

  const handleCoverError = () => {
    if (srcIndex + 1 < coverUrls.length) {
      setSrcIndex((index) => index + 1)
    } else if (!broken) {
      setBroken(true)
    } else {
      setLoaded(true)
    }
  }

  const chapters = (manga.chapter_preview ?? [])
    .map((chapter) => String(chapter).trim())
    .filter(Boolean)
  const detailsMissing = !String(manga?.title || "").trim() || !String(manga?.source || "").trim()

  useEffect(() => {
    setLoaded(false)
    setSrcIndex(0)
    setBroken(false)
  }, [manga.id, manga.cover_path, manga.cover_url, coverFallbacksKey])

  useEffect(() => {
    const image = imageRef.current
    if (image?.complete && image.naturalWidth > 0) {
      setLoaded(true)
    }
  }, [currentCover])

  if (detailsMissing) return <MangaCardSkeleton />

  return (
    <div
      className="group flex min-h-[156px] w-full gap-3 rounded-md border border-line/70 bg-panel/55 p-3 shadow-card backdrop-blur-md transition-colors duration-200 hover:border-emerald-300/45 hover:bg-soft/70"
    >
      {/* Cover thumbnail - clickable */}
      <button
        type="button"
        onClick={() => onSelect?.(manga)}
        className="relative h-32 w-[92px] shrink-0 overflow-hidden rounded bg-soft focus:outline-none focus:ring-1 focus:ring-emerald-200/70"
      >
        {!loaded && <div className="kari-skeleton absolute inset-0" />}
        <img
          ref={imageRef}
          src={currentCover}
          alt={manga.title}
          loading={priority ? "eager" : "lazy"}
          decoding="async"
          fetchPriority={priority ? "high" : "auto"}
          draggable="false"
          onLoad={() => setLoaded(true)}
          onError={handleCoverError}
          className={`h-full w-full object-cover transition ${loaded ? "opacity-100" : "opacity-0"}`}
        />
      </button>

      {/* Info */}
      <div className="flex min-w-0 flex-1 flex-col justify-between py-0.5">
        <div className="min-w-0">
          {/* Title - clickable */}
          <button
            type="button"
            onClick={() => onSelect?.(manga)}
            title={manga.title}
            className="block w-full max-w-full truncate text-left text-[13px] font-bold text-zinc-100 transition hover:text-emerald-100 focus:outline-none"
          >
            {manga.title}
          </button>

          {/* Chapter list - each line is individually hoverable */}
          <div className="mt-2 space-y-0.5">
            {chapters.length > 0 ? chapters.map((ch, i) => (
              <button
                key={i}
                type="button"
                onClick={() => onSelect?.(manga)}
                className="manga-chapter-row flex w-full items-center gap-1.5 rounded px-0.5 py-0.5 text-left text-xs text-zinc-400 transition-colors hover:bg-emerald-300/5 hover:text-zinc-100"
              >
                <span className="text-[10px] text-zinc-600">▊</span>
                <BookOpen size={12} strokeWidth={1.8} className="shrink-0 text-emerald-300/80" aria-hidden="true" />
                <span className="truncate">{/^[\d]/.test(String(ch)) ? `Cap. ${ch}` : ch}</span>
                {i === 0 && manga.updated_at && (
                  <span className="ml-auto flex items-center gap-1 text-[10px] text-zinc-500">
                    <Clock3 size={11} strokeWidth={1.8} aria-hidden="true" />
                    {_relativeShort(manga.updated_at)}
                  </span>
                )}
              </button>
            )) : manga.chapter_status === "loading" || manga.chapter_status === "pending" ? (
              <div className="space-y-2 px-0.5 py-1" aria-label="Carregando capítulos">
                <div className="kari-skeleton h-3 w-full rounded" />
                <div className="kari-skeleton h-3 w-4/5 rounded" />
                <div className="kari-skeleton h-3 w-3/5 rounded" />
              </div>
            ) : (
              <p className="px-0.5 py-0.5 text-xs text-zinc-500">
                Nenhum capitulo encontrado nesta fonte
              </p>
            )}
          </div>
        </div>

        {manga.source && (
          <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-zinc-500">
            <p className="truncate">{manga.source}</p>
            <span className="flex shrink-0 items-center gap-1 uppercase">
              <Globe2 size={11} strokeWidth={1.8} aria-hidden="true" />
              {String(manga.chapter_languages?.[0] || manga.language || "PT").replace("pt-br", "PT")}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

function _relativeShort(iso) {
  if (!iso) return ""
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ""
  const diff = Math.max(0, Date.now() - then)
  const min = Math.floor(diff / 60000)
  if (min < 60) return `${min || 1}m`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h}h`
  const d = Math.floor(h / 24)
  return `${d}d`
}

export default memo(MangaCard, (prev, next) => {
  // Evita re-render quando o repoll da home traz um objeto novo com o MESMO
  // conteudo relevante (identidade nova de array/objeto nao deve repintar o card).
  const a = prev.manga
  const b = next.manga
  return (
    prev.priority === next.priority
    && prev.compact === next.compact
    && prev.onSelect === next.onSelect
    && a.id === b.id
    && a.title === b.title
    && a.cover_path === b.cover_path
    && a.cover_url === b.cover_url
    && (a.cover_fallbacks ?? []).join("|") === (b.cover_fallbacks ?? []).join("|")
    && a.source === b.source
    && a.chapter_status === b.chapter_status
    && a.updated_at === b.updated_at
    && (a.chapter_preview ?? []).join("|") === (b.chapter_preview ?? []).join("|")
    && (a.chapter_languages ?? []).join("|") === (b.chapter_languages ?? []).join("|")
    && (a.language ?? "") === (b.language ?? "")
  )
})
