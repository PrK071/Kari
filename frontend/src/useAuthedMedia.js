import { useEffect, useState } from "react"
import { authenticatedHeaders, classifyMediaUrl, isVideoMedia } from "./profileMedia.js"

// Resolve midia de perfil: URLs /api/ (bucket privado) sao baixadas com fetch
// autenticado e servidas via URL.createObjectURL; o blob e revogado quando a
// midia muda ou o componente desmonta. URLs absolutas/static passam direto.
export function useAuthedMedia(url) {
  const [media, setMedia] = useState({ src: "", isVideo: false })

  useEffect(() => {
    const kind = classifyMediaUrl(url)
    if (kind === "empty") {
      setMedia({ src: "", isVideo: false })
      return undefined
    }
    if (kind === "direct") {
      setMedia({ src: url, isVideo: isVideoMedia("", url) })
      return undefined
    }

    let cancelled = false
    let objectUrl = ""
    setMedia({ src: "", isVideo: isVideoMedia("", url) })

    const resolve = async () => {
      const resolved = `${import.meta.env.VITE_DESKTOP_BUILD === "1" ? window.location.origin : (import.meta.env.VITE_API_BASE_URL || window.location.origin)}${url}`
      try {
        const response = await fetch(resolved, { headers: authenticatedHeaders() })
        if (!response.ok) return
        const blob = await response.blob()
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        const contentType = response.headers.get("content-type") || ""
        setMedia({ src: objectUrl, isVideo: isVideoMedia(contentType, url) })
      } catch {
        // Falha silenciosa: sem src. Nenhum segredo e exposto.
      }
    }

    resolve()

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [url])

  return media
}
