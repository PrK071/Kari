import { readBrowserStorage } from "./browserStorage.js"

export const AUTH_TOKEN_KEY = "kari:auth-token:v1"

export function authenticatedHeaders(headers = {}) {
  const token = readBrowserStorage(AUTH_TOKEN_KEY) || ""
  return token ? { ...headers, Authorization: `Bearer ${token}` } : headers
}

// "authed": caminho /api/ servido pela aplicacao -> fetch com Authorization (bucket privado).
// "direct": URL absoluta http(s) ou /static/ publico -> sem token.
// "empty": sem midia.
export function classifyMediaUrl(url) {
  const raw = (url || "").trim()
  if (!raw) return "empty"
  if (raw.startsWith("/api/")) return "authed"
  return "direct"
}

export function isVideoMedia(contentType, url) {
  if (/^(video\/)/i.test(contentType || "")) return true
  return /\.(mp4|webm|mov|m4v)(\?|$)/i.test(url || "")
}
