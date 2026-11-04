/**
 * Centralized API client for the Flow Video 2.0 plugin (ported from the
 * storyboard app).
 *
 * All plugin backend routes live under `/api/v1/plugins/pltt-video-studio-2/*`.
 *
 * IMPORTANT: we deliberately do *not* import `apiFetch` from `@palettelab/sdk`
 * here. The SDK's free `apiFetch` defaults to `http://localhost:8000` (and
 * triggers a host login refresh on 401), which doesn't match the pltt CLI
 * simulator (which serves the backend on a dynamic port like `127.0.0.1:8733`
 * and exposes a working fetch via `platform.apiFetch`).
 *
 * Instead, `app/layout.tsx` calls `registerApiFetch(platform.apiFetch)` at
 * mount time and we route every helper through that. Production uses the
 * same code path: the OS sets `platform.apiFetch` to its own credentialed
 * fetcher, which already handles auth refresh and same-origin proxying.
 */

export type ApiFetch = (path: string, init?: RequestInit) => Promise<Response>

export const PLUGIN_ID = "pltt-video-studio-2"
export const BASE = `/api/v1/plugins/${PLUGIN_ID}`

let _platformFetch: ApiFetch | null = null
// Absolute origin of the backend (e.g. "http://127.0.0.1:8736" under
// `pltt dev`, or "" when same-origin in production). Discovered lazily by
// `discoverApiOrigin` so `EventSource` — which cannot be routed through
// `platform.apiFetch` — can reach the right port.
let _apiOrigin: string | null = null
let _originProbe: Promise<string> | null = null

export function registerApiFetch(fn: ApiFetch | undefined | null) {
  _platformFetch = fn ?? null
  _apiOrigin = null
  _originProbe = null
}

function platformFetch(): ApiFetch {
  if (_platformFetch) return _platformFetch
  // Fallback for very early code paths before layout mounts. Use a plain
  // fetch and let same-origin handle it; if same-origin doesn't reach the
  // backend, the caller sees a real network error rather than a silent
  // proxy to localhost:8000.
  return (path, init) =>
    fetch(path, { credentials: "include", ...init })
}

export async function apiRequest(endpoint: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers || {})
  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }

  const path = endpoint.startsWith("/") ? endpoint : `/${endpoint}`
  const method = (options.method || "GET").toUpperCase()
  const fullPath = `${BASE}${path}`

  let response: Response
  try {
    response = await platformFetch()(fullPath, { ...options, headers })
  } catch (err) {
    // Browser fetch and Node undici both surface low-level transport errors
    // as a vague TypeError ("Failed to fetch" / "fetch failed") with the
    // useful detail tucked away on `cause`. Promote those into the error
    // message so users see the real reason in the UI instead of just
    // "fetch failed".
    const cause = (err as { cause?: unknown })?.cause
    const baseMsg = err instanceof Error ? err.message : String(err)
    const causeMsg =
      cause instanceof Error
        ? cause.message
        : typeof cause === "string"
          ? cause
          : ""
    const msg = causeMsg ? `${baseMsg}: ${causeMsg}` : baseMsg
    console.error(`[apiRequest] ${method} ${fullPath} transport error:`, err)
    throw new Error(`${method} ${path} failed: ${msg}`)
  }

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    const detail = errorData.detail
    console.error(
      `[apiRequest] ${method} ${fullPath} → HTTP ${response.status}`,
      errorData,
    )
    if (Array.isArray(detail)) {
      const messages = detail
        .map((e: { loc?: string[]; msg?: string }) =>
          e.loc ? `${e.loc.join(".")} — ${e.msg}` : e.msg,
        )
        .join("; ")
      throw new Error(messages || `Request failed with status ${response.status}`)
    }
    throw new Error(
      typeof detail === "string" ? detail : `Request failed with status ${response.status}`,
    )
  }

  return response.json()
}

/** Multipart upload variant. */
export async function apiUploadFile(
  endpoint: string,
  file: File,
  fieldName = "file",
  extraFields?: Record<string, string>,
) {
  const formData = new FormData()
  formData.append(fieldName, file)
  if (extraFields) {
    for (const [k, v] of Object.entries(extraFields)) formData.append(k, v)
  }
  const path = endpoint.startsWith("/") ? endpoint : `/${endpoint}`
  const response = await platformFetch()(`${BASE}${path}`, {
    method: "POST",
    body: formData,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body?.detail ?? `Upload failed (${response.status})`)
  }
  return response
}

// Full resolved API base the platform routes our calls to, discovered by
// probing `/status` through `platform.apiFetch` and reading the *resolved*
// Response URL. In a hosted preview this is the preview-scoped form
// `https://…/api/v1/appstore/previews/<id>/plugins/pltt-video-studio-2` plus a
// `?preview_token=…` query; in production it's the plain same-origin base.
// EventSource (SSE) cannot go through `platform.apiFetch`, so it must rebuild
// its URL from this resolved base — otherwise it hits the bare
// `/api/v1/plugins/pltt-video-studio-2/events/…` path, which 404s in a preview.
let _apiBase: { origin: string; prefix: string; query: string } | null = null

/** Probes the backend via `platform.apiFetch` and caches the resolved API
 * origin + path prefix + query so `EventSource` can target the right URL
 * (preview-scoped, with preview_token, when hosted). Safe to call repeatedly. */
export function discoverApiOrigin(): Promise<string> {
  if (_apiOrigin !== null) return Promise.resolve(_apiOrigin)
  if (_originProbe) return _originProbe
  _originProbe = (async () => {
    try {
      const res = await platformFetch()(`${BASE}/status`, { method: "GET" })
      const resolved = res.url || ""
      if (resolved) {
        const u = new URL(resolved, window.location.href)
        _apiOrigin = u.origin
        // Strip the trailing `/status` segment to get the base prefix the
        // platform actually routes plugin calls through (keeps any preview
        // scoping like `/appstore/previews/<id>/plugins/pltt-video-studio-2`).
        _apiBase = {
          origin: u.origin,
          prefix: u.pathname.replace(/\/status\/?$/, ""),
          query: u.search, // includes the leading "?", e.g. "?preview_token=…"
        }
      } else {
        _apiOrigin = ""
      }
    } catch {
      _apiOrigin = ""
    }
    return _apiOrigin
  })()
  return _originProbe
}

/** Returns the absolute URL + the correct `withCredentials` flag for a plugin
 * SSE endpoint, built from the *resolved* API base so it stays preview-scoped
 * (and carries `preview_token`) in a hosted preview.
 *
 * EventSource cannot go through `platform.apiFetch`, so it can only authenticate
 * via the session cookie (`withCredentials`) or the `preview_token` query param —
 * never the credentialed fetch that normal calls rely on. In production the API
 * is served from a *different* origin than the page (e.g. `apps-api.pltt.xyz`
 * vs the OS shell), so `withCredentials` MUST be `true` for the cookie to ride
 * along on the cross-origin connection — the platform gateway allows credentialed
 * CORS (the SDK's own broker stream always uses `withCredentials: true`). The
 * lone exception is the local `pltt dev` simulator, which serves CORS as
 * `allow_origins=*, allow_credentials=false`: there credentials must be `false`
 * or the browser refuses the cross-origin connection. */
export async function sseUrl(
  projectId: string,
  type: "image" | "video" = "image",
): Promise<{ url: string; withCredentials: boolean }> {
  await discoverApiOrigin()

  if (_apiBase) {
    const tokenQuery = _apiBase.query ? `&${_apiBase.query.slice(1)}` : ""
    const url = `${_apiBase.origin}${_apiBase.prefix}/events/${projectId}?type=${type}${tokenQuery}`
    const sameOrigin = _apiBase.origin === window.location.origin
    // Only the local simulator (cross-origin localhost) can't accept credentials.
    let host = ""
    try {
      host = new URL(_apiBase.origin).hostname
    } catch {
      host = ""
    }
    const isLocalSim = !sameOrigin && (host === "127.0.0.1" || host === "localhost")
    return { url, withCredentials: !isLocalSim }
  }

  // Fallback: no resolved base (very early call) — use the bare same-origin path.
  return { url: `${BASE}/events/${projectId}?type=${type}`, withCredentials: true }
}

/** Resolves a plugin-relative media URL (e.g. the merged Long Video served by
 * `/story/media/...` when local dev storage is in play) to an absolute URL on
 * the discovered API base, keeping any preview scoping/token. `<video>`/`<img>`
 * tags can't go through `platform.apiFetch`, so — like `sseUrl` — they need
 * the resolved base. Hosted-storage URLs pass through unchanged. */
export async function resolveMediaUrl(url: string): Promise<string> {
  if (!url.startsWith(`${BASE}/`)) return url
  await discoverApiOrigin()
  if (!_apiBase) return url
  const rest = url.slice(BASE.length)
  const tokenQuery = _apiBase.query
    ? (rest.includes("?") ? `&${_apiBase.query.slice(1)}` : _apiBase.query)
    : ""
  return `${_apiBase.origin}${_apiBase.prefix}${rest}${tokenQuery}`
}

/** Synchronous variant of `resolveMediaUrl` for render paths. Uses the API
 * base cached by `discoverApiOrigin()` (awaited once during bootstrap);
 * before that it returns the plugin-relative URL unchanged. */
export function mediaUrl(url: string | undefined | null): string {
  if (!url) return ""
  if (!url.startsWith(`${BASE}/`) || !_apiBase) return url
  const rest = url.slice(BASE.length)
  const tokenQuery = _apiBase.query
    ? (rest.includes("?") ? `&${_apiBase.query.slice(1)}` : _apiBase.query)
    : ""
  return `${_apiBase.origin}${_apiBase.prefix}${rest}${tokenQuery}`
}

/** JSON helpers. */
export function apiGet<T = any>(endpoint: string): Promise<T> {
  return apiRequest(endpoint)
}
export function apiSend<T = any>(endpoint: string, method: "POST" | "PUT" | "PATCH" | "DELETE", body?: unknown): Promise<T> {
  return apiRequest(endpoint, { method, body: body === undefined ? undefined : JSON.stringify(body) })
}

/** Multipart upload that returns the parsed JSON body. */
export async function apiUpload<T = any>(endpoint: string, file: File, fields?: Record<string, string>): Promise<T> {
  const res = await apiUploadFile(endpoint, file, "file", fields)
  return res.json()
}
