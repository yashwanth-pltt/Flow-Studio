/**
 * Light / dark theming for globals.css, applied at build time (see build-css.mjs).
 *
 * The UI was written dark-first with literal hex colours. Rather than keep two
 * hand-maintained palettes, every hex colour in a declaration becomes a
 * `--pvc-*` custom property whose dark value is the original colour and whose
 * light value is derived from it:
 *   - neutral surfaces flip light, neutral text flips dark, borders stay subtle;
 *   - accents (gold, error reds…) keep their hue, with pale/dark tints swapped;
 *   - translucent dark overlays on media (badges, scrims) stay dark, along with
 *     the text drawn on them;
 *   - shadows keep their colour but get lighter.
 * app/layout.tsx sets `data-theme` on the app root from the OS appearance.
 * Dark output is identical to the source, so dark mode is unchanged.
 */
import postcss from "postcss"

const HEX = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b/g
const ROOT = "[data-pv-root]"
const LIGHT = '[data-pv-root][data-theme="light"]'

function parseHex(hex) {
  let h = hex.slice(1).toLowerCase()
  if (h.length <= 4) h = [...h].map((c) => c + c).join("")
  const n = (i) => parseInt(h.slice(i, i + 2), 16) / 255
  return { r: n(0), g: n(2), b: n(4), a: h.length === 8 ? n(6) : 1, key: h.length === 8 && h.endsWith("ff") ? h.slice(0, 6) : h }
}

function toHsl({ r, g, b }) {
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2
  if (max === min) return { h: 0, s: 0, l }
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4
  return { h: h / 6, s, l }
}

function fromHsl(h, s, l, a = 1) {
  const f = (n) => {
    const k = (n + h * 12) % 12
    return l - s * Math.min(l, 1 - l) * Math.max(-1, Math.min(k - 3, 9 - k, 1))
  }
  const hex = (v) => Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, "0")
  return `#${hex(f(0))}${hex(f(8))}${hex(f(4))}${a < 1 ? hex(a) : ""}`
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))
// Neutral = little actual colour. HSL saturation can't be used: it inflates near
// black and white, so a warm off-white like #f5f3ee would read as an accent.
const chroma = ({ r, g, b }) => Math.max(r, g, b) - Math.min(r, g, b)
const isNeutral = (c) => chroma(c) < 0.08

/** Lightness of a neutral in light mode: surfaces/borders go light, text goes dark. */
function neutralLightness(l) {
  if (l <= 0.38) return 1 - l * 0.6 // surfaces & borders: #101010 → #f6f6f6, #363636 → #dedede
  if (l >= 0.53) return clamp(0.45 - (l - 0.53) * 0.85, 0.08, 0.45) // text: #f4f2ed → ~#1b1a17
  const t = (l - 0.38) / 0.15 // mid greys: blend between the two ramps
  return (1 - 0.38 * 0.6) * (1 - t) + 0.45 * t
}

/** The light-mode version of one colour. `shadow` colours only soften. */
export function lightColor(hex, shadow = false) {
  const c = parseHex(hex)
  const { h, s, l } = toHsl(c)
  if (shadow) return l < 0.3 ? fromHsl(h, s, l, clamp(c.a * 0.35, 0.03, 1)) : fromHsl(0, 0, 0, clamp(c.a * 0.5, 0.02, 1))
  if (c.a < 1) {
    if (l <= 0.2) return hex // dark overlay (badges, scrims): stays dark on media
    if (isNeutral(c) && l >= 0.75) return fromHsl(0, 0, 0, c.a) // white wash → black wash
    return hex // tinted wash (gold highlight…): works on both
  }
  if (isNeutral(c)) return fromHsl(h, Math.min(s, 0.12), neutralLightness(l))
  if (l > 0.62) return fromHsl(h, s, 0.25 + (1 - l) * 0.5) // pale tint (error text) → deep tint
  if (l < 0.25) return fromHsl(h, Math.min(s, 0.6), 0.93 - l * 0.2) // dark tint (error panel) → pale tint
  return hex // accents keep their colour
}

/** Backgrounds that keep their colour in light mode, so text on them must too:
 * translucent dark overlays on media, and opaque accents (gold buttons…). */
const keepsItsColour = (hex) => {
  const c = parseHex(hex)
  const { l } = toHsl(c)
  return c.a < 1 ? l <= 0.2 : !isNeutral(c) && l >= 0.25 && l <= 0.62
}

function inThemeAtRule(node) {
  for (let p = node.parent; p; p = p.parent) if (p.type === "atrule" && p.name === "theme") return true
  return false
}

export function themeColors(src) {
  const root = postcss.parse(src)
  const tokens = new Map() // name -> { dark, light }
  const rootLight = [] // light values for the :root design tokens (--background…)
  const token = (hex, shadow) => {
    const { key } = parseHex(hex)
    const name = `--pvc-${key}${shadow ? "-s" : ""}`
    if (!tokens.has(name)) tokens.set(name, { dark: `#${key}`, light: lightColor(`#${key}`, shadow) })
    return `var(${name})`
  }

  root.walkRules((rule) => {
    if (inThemeAtRule(rule)) return
    if (rule.selector.trim() === ":root") {
      rule.walkDecls((d) => {
        if (d.prop === "color-scheme") return
        if (d.prop.startsWith("--") && HEX.test(d.value)) rootLight.push(`${d.prop}:${d.value.replace(HEX, (x) => lightColor(x))}`)
        HEX.lastIndex = 0
      })
      return
    }
    // Text drawn on a background that keeps its colour (badge on media, gold button) keeps its colour too.
    const overlay = rule.nodes.some((d) => d.type === "decl" && /^background(-color)?$/.test(d.prop) && (d.value.match(HEX) || []).some(keepsItsColour))
    rule.each((d) => {
      if (d.type !== "decl" || !HEX.test(d.value)) return
      HEX.lastIndex = 0
      if (overlay && (d.prop === "color" || /^background/.test(d.prop))) return
      const shadow = /shadow/.test(d.prop) || (d.prop === "filter" && /drop-shadow/.test(d.value))
      d.value = d.value.replace(HEX, (x) => token(x, shadow))
    })
  })

  const decls = (pick) => [...tokens].map(([name, v]) => `${name}:${pick(v)}`).join(";")
  const header = `:root{${decls((v) => v.dark)}}\n${ROOT}{color-scheme:dark}\n${LIGHT}{color-scheme:light;${rootLight.join(";")};${decls((v) => v.light)}}\n`
  // After the @import lines so the stylesheet stays valid.
  let css = root.toString()
  const imports = css.match(/^(?:@import[^;]+;\s*)+/)
  css = imports ? imports[0] + header + css.slice(imports[0].length) : header + css
  return { css, tokens: tokens.size }
}
