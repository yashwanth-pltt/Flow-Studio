/**
 * The app renders on a fixed-width "stage" that is scaled to fill the window,
 * so the layout keeps the reference proportions (a 1920px-wide window showing
 * the design at 110%) at every window size. Below SCALE_MIN_WIDTH the stage is
 * left unscaled and the responsive mobile layout takes over.
 *
 * `width`/`height` are the stage size in CSS px (what breakpoints should test);
 * `scale` converts screen px (pointer deltas) into stage px.
 */
export const DESIGN_WIDTH = 1920 / 1.1
export const SCALE_MIN_WIDTH = 768

export type AppViewport = { scale: number; width: number; height: number }

export function computeAppViewport(windowWidth: number, windowHeight: number): AppViewport {
  const scale = windowWidth >= SCALE_MIN_WIDTH ? windowWidth / DESIGN_WIDTH : 1
  return { scale, width: windowWidth / scale, height: windowHeight / scale }
}

let current: AppViewport =
  typeof window === "undefined"
    ? { scale: 1, width: DESIGN_WIDTH, height: 1080 / 1.1 }
    : computeAppViewport(window.innerWidth, window.innerHeight)
const listeners = new Set<() => void>()

export function getAppViewport(): AppViewport {
  return current
}

export function setAppViewport(next: AppViewport) {
  if (next.scale === current.scale && next.width === current.width && next.height === current.height) return
  current = next
  listeners.forEach((fn) => fn())
}

export function subscribeAppViewport(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
