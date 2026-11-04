"use client"

import "./compiled.css"
import * as React from "react"
import { usePlatform, isSandboxRuntime } from "@palettelab/sdk"

import { registerApiFetch } from "@/lib/api-helper"
import { I18nProvider } from "@/lib/i18n"
import { ColorModeProvider, useColorMode } from "@/lib/theme"
import { PortalContainerProvider } from "@/components/ui/app-portal"
import { computeAppViewport, setAppViewport } from "@/lib/app-viewport"

// Keep multipart uploads intact through the OS `apiFetch` bridge: it sets
// `Content-Type: application/json` on every request, which would clobber the
// browser's `multipart/form-data; boundary=…` for FormData bodies.
if (typeof window !== "undefined") {
  const w = window as unknown as { __pvsFetchPatched?: boolean }
  if (!w.__pvsFetchPatched) {
    w.__pvsFetchPatched = true
    const originalFetch = window.fetch.bind(window)
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      if (init && init.body instanceof FormData) {
        const headers = new Headers(init.headers || {})
        headers.delete("Content-Type")
        return originalFetch(input, { ...init, headers })
      }
      return originalFetch(input, init)
    }
  }
}

/** Shows a visible error panel instead of a blank window when a descendant throws. */
class LayoutErrorBoundary extends React.Component<{ label: string; children: React.ReactNode }, { error: unknown }> {
  state = { error: null as unknown }
  static getDerivedStateFromError(error: unknown) {
    return { error }
  }
  componentDidCatch(error: unknown, info: React.ErrorInfo) {
    console.error(`[flow-video-2] ${this.props.label} threw`, error, info)
  }
  render() {
    if (!this.state.error) return this.props.children
    const e = this.state.error as Error
    return (
      <div style={{ padding: 16, margin: 16, background: "#2a1414", border: "1px solid #dc8d85", color: "#f4d4d0", fontFamily: "ui-monospace, monospace", fontSize: 13, whiteSpace: "pre-wrap" }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Flow Video 2.0 — {this.props.label} crashed</div>
        <div>{(e && (e.message || String(e))) || "unknown error"}</div>
        {e?.stack && <pre style={{ marginTop: 8, maxHeight: 240, overflow: "auto", fontSize: 11 }}>{e.stack}</pre>}
      </div>
    )
  }
}

function ApiFetchBridge({ children }: { children: React.ReactNode }) {
  const platform = usePlatform()
  const fetchFn = (platform as unknown as { apiFetch?: typeof fetch }).apiFetch
  if (fetchFn) registerApiFetch(fetchFn as never)
  return <>{children}</>
}

function AppRoot({ children }: { children: React.ReactNode }) {
  // Light/dark follows the OS; globals.css colours resolve per `data-theme` (see theme-css.mjs).
  const colorMode = useColorMode()
  const [portalContainer, setPortalContainer] = React.useState<HTMLElement | null>(null)
  const rootRef = React.useRef<HTMLDivElement>(null)
  // Fill the host container (OS window) when it has a definite height; under
  // the bare `pltt dev` mount `height:100%` collapses, so fall back to 100dvh.
  const [fillViewport, setFillViewport] = React.useState(() => typeof window !== "undefined" && !isSandboxRuntime())
  React.useLayoutEffect(() => {
    const el = rootRef.current
    const parent = el?.parentElement
    if (!el || !parent) return
    const probe = () => {
      const p = document.createElement("div")
      p.style.cssText = "height:100%;width:0;position:static;flex:none;margin:0;padding:0;border:0;"
      parent.appendChild(p)
      const resolves = p.offsetHeight > 0
      parent.removeChild(p)
      setFillViewport(!resolves)
    }
    probe()
    window.addEventListener("resize", probe)
    return () => window.removeEventListener("resize", probe)
  }, [])
  // Lay the UI out on a fixed-width stage and scale it to the window, so every
  // window size shows the same proportions (see lib/app-viewport.ts). The
  // reference UI's viewport units and breakpoints are rewritten by
  // build-css.mjs to follow this stage (`--pv-app-*`, `@container pv-app`).
  const stageRef = React.useRef<HTMLDivElement>(null)
  React.useLayoutEffect(() => {
    const el = rootRef.current
    const stage = stageRef.current
    if (!el || !stage) return
    const sync = () => {
      const view = computeAppViewport(el.clientWidth, el.clientHeight)
      const scaled = view.scale !== 1
      stage.style.width = scaled ? `${view.width}px` : "100%"
      stage.style.height = `${view.height}px`
      stage.style.transform = scaled ? `scale(${view.scale})` : "none"
      stage.style.setProperty("--pv-app-w", `${view.width}px`)
      stage.style.setProperty("--pv-app-h", `${view.height}px`)
      setAppViewport(view)
    }
    sync()
    const ro = new ResizeObserver(sync)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return (
    <div
      ref={rootRef}
      className={`${colorMode} pv-app-root antialiased`}
      data-pv-root
      data-theme={colorMode}
      style={{
        position: "relative",
        height: fillViewport ? "100dvh" : "100%",
        width: "100%",
        minHeight: 0,
        overflow: "hidden",
        // Anchor the reference UI's `position: fixed` drawers to this window.
        transform: "translateZ(0)",
        background: "var(--background, #090909)",
        color: "var(--foreground, #d4d3ce)",
      }}
    >
      <div
        ref={stageRef}
        data-pv-stage
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          transformOrigin: "0 0",
          overflow: "hidden",
          containerType: "size",
          // Stage-relative breakpoints; see build-css.mjs.
          containerName: "pv-app",
        }}
      >
        <PortalContainerProvider container={portalContainer}>
          <LayoutErrorBoundary label="page">{children}</LayoutErrorBoundary>
          <style>{`
            [data-pv-portal-root] > * { pointer-events: auto; max-height: 100%; }
            [data-pv-portal-root] [data-radix-popper-content-wrapper] { max-width: 100%; max-height: 100%; }
          `}</style>
          <div
            ref={setPortalContainer}
            data-pv-portal-root
            style={{ position: "absolute", inset: 0, zIndex: 2147483000, overflow: "hidden", pointerEvents: "none", contain: "layout paint", transform: "translateZ(0)" }}
          />
        </PortalContainerProvider>
      </div>
    </div>
  )
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <LayoutErrorBoundary label="root">
      <ApiFetchBridge>
        <I18nProvider>
          <ColorModeProvider>
            <AppRoot>{children}</AppRoot>
          </ColorModeProvider>
        </I18nProvider>
      </ApiFetchBridge>
    </LayoutErrorBoundary>
  )
}
