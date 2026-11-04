#!/usr/bin/env node
/**
 * Compile Tailwind 4 (+ the project's globals.css) to generated artifacts.
 * Current @palettelab/cli versions collect CSS bundle output, scope it under
 * the plugin root, and expose it as `__palettePluginCss` for Palette OS to
 * inject. The CSS file is the runtime source; the TS string export is retained
 * as a compatibility artifact for tooling that imports it.
 *
 * Run via `npm run build:css` after editing globals.css or whenever new
 * Tailwind utility classes need to be picked up.
 */
import { execFileSync } from "node:child_process"
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { themeColors } from "./theme-css.mjs"

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = resolve(__dirname, "..")

const inputCss = resolve(__dirname, "app/globals.css")
const tmpCss = resolve(__dirname, "app/.tw.css")
// globals.css with its colours turned into light/dark tokens (see theme-css.mjs).
// Lives next to globals.css so its relative @imports still resolve.
const themedCss = resolve(__dirname, "app/.themed.css")
const outCss = resolve(__dirname, "app/compiled.css")
const outTs = resolve(__dirname, "app/compiled-css.ts")

function extractExistingCompiledCss() {
  if (!existsSync(outTs)) return null
  const source = readFileSync(outTs, "utf8")
  const match = source.match(/export const COMPILED_CSS = (.*)\s*$/s)
  if (!match) return null
  try {
    return JSON.parse(match[1])
  } catch {
    return null
  }
}

/**
 * Ship every Tailwind utility UNLAYERED, plus an unlayered themed border
 * default for bare `border` elements.
 *
 * The Palette OS renders plugins into the shared, host-styled DOM (see
 * `@palettelab/cli` `lib/css-scope.js`, which scopes plugin CSS under
 * `[data-palette-plugin-root]`). The host shell ships its own preflight whose
 * `* { border-color: … }` reset is UNLAYERED, and unlayered rules outrank
 * anything in a cascade layer. Our themed colors lived in `@layer base` /
 * `@layer utilities`, so the host's reset won and borders rendered as the
 * host's light `#e5e7eb` — including the `color-mix` opacity variants
 * (`border-border/60` etc.), whose color only applies through a rule in the
 * utilities layer.
 *
 * Lifting utilities out of the layer gives each class its real `(0,1,0)`
 * specificity, which beats the host's universal `*` `(0,0,0)` for any color
 * and any opacity. The trailing unlayered `* { border-color }` covers bare
 * `border` utilities (no color class) — same specificity as the host's `*`,
 * but our stylesheet is injected after the host's, so source order wins.
 * Explicit color utilities `(0,1,0)` still outrank it, so intentional accents
 * are preserved. No-op standalone: utilities already outranked base/components.
 */
function unwrapUtilitiesLayer(src) {
  const marker = "@layer utilities{"
  const start = src.indexOf(marker)
  if (start === -1) return src
  const contentStart = start + marker.length
  let depth = 1
  let i = contentStart
  for (; i < src.length && depth > 0; i++) {
    const c = src[i]
    if (c === "{") depth++
    else if (c === "}") depth--
  }
  if (depth !== 0) {
    throw new Error("build-css: could not brace-match the @layer utilities block")
  }
  // src[i-1] is the layer's closing brace; drop the wrapper, keep the contents.
  return src.slice(0, start) + src.slice(contentStart, i - 1) + src.slice(i)
}

/**
 * The reference UI sizes the shell with viewport units, which inside a
 * Palette OS window mean the whole display. Point them at the app window's
 * real size (`--pv-app-h` / `--pv-app-w`, kept current by app/layout.tsx).
 */
function useAppViewport(src) {
  return src
    .replace(/(?<![\w-])100(dvh|svh|lvh|vh)\b/g, "var(--pv-app-h,100$1)")
    .replace(/(?<![\w-])100(dvw|svw|lvw|vw)\b/g, "var(--pv-app-w,100$1)")
}

/**
 * The app is laid out on a fixed-width stage that app/layout.tsx scales to the
 * window, so breakpoints must test the stage, not the display. Turn every
 * width/height media query into a query on the `pv-app` stage container.
 */
function useStageBreakpoints(src) {
  return src.replace(/@media\s*([^{;]+?)\s*\{/g, (rule, params) => {
    const features = params.split(/\s+and\s+/i)
    const sizeOnly = features.every((f) => /^\((?:(?:min|max)-)?(?:width|height)\s*:[^()]+\)$|^\((?:width|height)\s*[<>]=?[^()]+\)$/.test(f.trim()))
    return sizeOnly ? `@container pv-app ${params}{` : rule
  })
}

const themed = themeColors(readFileSync(inputCss, "utf8"))
writeFileSync(themedCss, themed.css)
console.log(`build-css: ${themed.tokens} colour tokens with light/dark values`)

let compiled
try {
  execFileSync("npx", ["@tailwindcss/cli@latest", "-i", themedCss, "-o", tmpCss, "--minify"], {
    cwd: root,
    stdio: "inherit",
    timeout: 20_000,
  })
  compiled = unwrapUtilitiesLayer(readFileSync(tmpCss, "utf8"))
} catch (error) {
  const fallback = extractExistingCompiledCss()
  if (!fallback) throw error
  console.warn("build-css: Tailwind CLI unavailable; reused existing compiled-css.ts")
  compiled = fallback
}
const css = useStageBreakpoints(useAppViewport(compiled))
const ts = `// AUTO-GENERATED. Run \`npm run build:css\` to regenerate.
// Compiled Tailwind 4 output for the Flow Video 2.0 plugin.
export const COMPILED_CSS = ${JSON.stringify(css)}
`
mkdirSync(dirname(outTs), { recursive: true })
writeFileSync(outCss, css)
writeFileSync(outTs, ts)
console.log(`compiled.css and compiled-css.ts written (${css.length} bytes of CSS)`)
