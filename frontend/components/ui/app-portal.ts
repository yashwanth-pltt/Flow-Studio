"use client"

import * as React from "react"

// Radix portals default to document.body, which sits outside the plugin root
// that Palette OS scopes this app's CSS (and CSS variables) under — portalled
// dialogs, menus and popovers would render unstyled. Every portal in
// components/ui reads this container instead (set by app/layout.tsx).
const PortalContainerContext = React.createContext<HTMLElement | null>(null)

function PortalContainerProvider({ container, children }: { container: HTMLElement | null; children: React.ReactNode }) {
  return React.createElement(PortalContainerContext.Provider, { value: container }, children)
}

function usePortalContainer(): HTMLElement | undefined {
  return React.useContext(PortalContainerContext) ?? undefined
}

export { PortalContainerProvider, usePortalContainer }
