---
name: AI Alpha Radar
description: Trend Intelligence Observatory — a dual-register operator dashboard surfacing AI signal before consensus.
colors:
  surface-1: "#07090c"
  surface-2: "#0c1015"
  surface-3: "#11161e"
  panel-solid: "#121821"
  ink: "#d6e1ee"
  ink-2: "#8b9cb0"
  ink-3: "#5a6878"
  accent-cyan: "#00f0ff"
  signal-good: "#00ff9d"
  signal-bad: "#ff4d6d"
  stage-whisper: "#00d4ff"
  stage-builder: "#00ff9d"
  stage-creator: "#ffd84d"
  stage-hype: "#ff8a3d"
  stage-commodity: "#7a8696"
  champagne: "#d4af37"
  void: "#050813"
  void-2: "#0a0f1e"
  observatory-ink: "#f4f1ea"
  observatory-ink-2: "#b3b0a8"
  observatory-ink-3: "#6a6862"
typography:
  display:
    fontFamily: "Geist, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "Fraunces, Georgia, serif"
    fontSize: "20px"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "normal"
  title:
    fontFamily: "Fraunces, Georgia, serif"
    fontSize: "16px"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "normal"
  body:
    fontFamily: "Geist, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "normal"
  label:
    fontFamily: "Geist Mono, ui-monospace, monospace"
    fontSize: "10px"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "0.04em"
rounded:
  xs: "4px"
  sm: "6px"
  md: "18px"
  pill: "100px"
spacing:
  sp-1: "4px"
  sp-2: "8px"
  sp-3: "12px"
  sp-4: "16px"
  sp-5: "24px"
  sp-6: "32px"
components:
  panel:
    backgroundColor: "{colors.panel-solid}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "16px"
  glass-panel:
    backgroundColor: "rgba(255,255,255,0.04)"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "16px"
  chip:
    backgroundColor: "rgba(255,255,255,0.02)"
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "5px 10px"
  chip-active:
    backgroundColor: "rgba(0,240,255,0.05)"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
  gem-card:
    backgroundColor: "{colors.panel-solid}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "16px"
  search-input:
    backgroundColor: "rgba(255,255,255,0.03)"
    textColor: "{colors.observatory-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.xs}"
    padding: "6px 12px"
---

# Design System: AI Alpha Radar

## 1. Overview

**Creative North Star: "The Bloomberg Observatory"**

Two registers in one system. **Bloomberg** is the default — cyan-and-phosphor neon on near-black, terminal density, Geist Mono labels, real-time signal. **Observatory** is the inner sanctum — champagne gold on deep void blue, Fraunces italics, the slower surfaces for demand analysis and reference reading. Both registers share the same substrate, glass material, and typographic stack. The page-scope class `.page-observatory` swaps the palette tokens; everything else inherits.

The atmosphere does the depth work. A deep substrate of four radial-gradient pools (cyan, signal-green, ember-orange, magenta) overlaid on a 32px grid sits behind every surface. Glass panels refract that atmosphere — they are never decorative. The surface is *etched* into the substrate, not pasted on. Flat dark = grey plastic. We don't ship grey plastic.

This system explicitly rejects: generic SaaS purple gradients with three feature cards, trading-platform Robinhood green and candlestick energy, school-project Bootstrap defaults, and ChatGPT-clone chat bubbles in a left sidebar. Every visual decision passes those four anti-references before it ships.

**Key Characteristics:**

- **Dual-register palette** — Bloomberg neon (default) and Observatory champagne (page-scoped) share structure, differ in voice.
- **Atmospheric substrate** — radial gradients + 32px grid + faint scanlines. Glass refracts it, never replaces it.
- **Refined refraction** — every glass surface gets a top highlight and a bottom inset shadow, simulating physical edge refraction.
- **Operator typography** — Fraunces for editorial moments, Geist for body, Geist Mono for data labels, Geist for display.
- **Density with breath** — Bloomberg-grade signal density without Bloomberg-grade clutter.

## 2. Colors

The palette runs cold by default (cyan signal on near-black), with a warm gold register for the Observatory surfaces. Saturation is high on the accents because they are scarce; they earn their volume.

### Primary

- **Watchtower Cyan** (`#00f0ff`): The default accent. Used on active chips, focus rings, gem-card convergence badges, hover edges. Reserved for "this is signal" — never decorative.
- **Almanac Champagne** (`#d4af37`): The Observatory accent and the topbar brand color across all pages (product identity stays gold even in Bloomberg mode). Used on the active nav indicator, the champagne pulse dot, and `.page-observatory` accents.

### Secondary

- **Phosphor Green** (`#00ff9d`): Positive signal — `--good`, builder-stage stage dot, momentum-up indicators. Distinct from Robinhood green because it is paired with cyan and gold, never with red candlesticks.
- **Ember Red** (`#ff4d6d`): Negative signal — `--bad`, momentum-down. Used sparingly. Never as a "delete" or "error" color; reserved for trend collapse.

### Tertiary (stage palette)

The five-stage trend lifecycle has its own scale, encoded in both color and position. Color alone never carries the meaning.

- **Whisper Cyan** (`#00d4ff` Bloomberg / `#a8c4ff` Observatory): Trend just surfacing.
- **Builder Green** (`#00ff9d` / `#ffd97a`): Operators are building.
- **Creator Yellow** (`#ffd84d` / `#f4a623`): Creators are publishing.
- **Hype Orange** (`#ff8a3d` / `#ff7a5a`): Mass-market crossover.
- **Commodity Slate** (`#7a8696` / `#7c98c4`): Saturated, post-alpha.

### Neutral

- **Void Black** (`#07090c` surface-1 / `#050813` observatory): The page substrate. Tinted toward blue, never `#000`.
- **Slate Surface** (`#0c1015` surface-2): Mid-tier surfaces (panel solids).
- **Deep Ash** (`#11161e` surface-3): Elevated panel backgrounds.
- **Ink** (`#d6e1ee` Bloomberg / `#f4f1ea` Observatory): Body text, headings.
- **Ink Subdued** (`#8b9cb0` / `#b3b0a8`): Secondary text, captions.
- **Ink Quiet** (`#5a6878` / `#6a6862`): Meta labels, mono timestamps.

### Named Rules

**The Two-Register Rule.** Bloomberg cyan and Observatory champagne are not interchangeable. The topbar is always Observatory (product identity). Pages adopt Bloomberg or Observatory through the `.page-observatory` class, never partially. Mixing registers within a single page is forbidden.

**The Signal Scarcity Rule.** Watchtower Cyan covers ≤10% of any view. Its saturation is high because it is rare. If cyan appears on more than 10% of the visible surface, demote some of it to ink or remove it.

**The Stage-Color-Plus Rule.** Stage palette colors are always paired with a position cue and a label. Color alone never carries the trend stage.

## 3. Typography

**Display + Body Font:** Geist (Vercel, OFL — variable 300–700)
**Editorial Font:** Fraunces (variable serif — opsz 9–144, weight 400–700, with full italic axis)
**Label/Mono Font:** Geist Mono (OFL — variable 400–600)

**Character:** Three families, one foundry-grade aesthetic. Geist (by Vercel) is the system's workhorse — crisp, technical, opinionated, designed for product UIs at small sizes. Fraunces carries the editorial moments (brand mark, observatory section headings) with its variable optical-size and italic axes for genuine character at every size. Geist Mono handles every data point. The pairing reads as a Vercel-grade product tool with editorial confidence — modern, precise, never default.

### Hierarchy

- **Display** (Geist 700, 16px, line-height 1.1, −0.01em letter-spacing): Gem-card keywords, prominent display labels, chart tooltip values. Tight negative tracking is Geist's signature; lean into it.
- **Headline** (Fraunces 500 italic available, 20px, line-height 1.2, optical-size auto): Topbar product name "AI Alpha Radar" and observatory page section headings. Fraunces' opsz axis adjusts character to size automatically.
- **Title** (Fraunces 500, 16px, line-height 1.3): Panel titles in Observatory pages.
- **Body** (Geist 400, 13px, line-height 1.45): All running text. The 13px floor is deliberate — operator-density, not blog-readability. Geist holds up at small sizes where Inter starts to look generic.
- **Label** (Geist Mono 500, 10–11px, +0.04em letter-spacing): Data labels, timestamps, deltas, watchlist counts, anything tabular. Geist Mono's tabular figures align by default.

### Named Rules

**The Fraunces-for-Identity Rule.** Fraunces italics belong to the product's voice (the topbar brand mark, observatory section headings, blockquote attributions). Never use Fraunces for data labels, button text, or chip labels.

**The Mono-for-Data Rule.** Every number, code, timestamp, percentage, and delta uses Geist Mono. Never proportional figures. Tabular alignment is the point.

**The 13px Floor Rule.** Body text never goes below 13px. Operator density does not require small type; it requires considered hierarchy.

## 4. Elevation

The system uses **atmospheric layering**: a deep substrate of radial gradients + 32px grid + faint scanlines provides the depth field. Surfaces float above the substrate via glass refraction, not via heavy drop shadows. Shadows are ambient — they describe the surface's relationship to the substrate, not a hard light source.

### Shadow Vocabulary

- **Glass refraction** (`box-shadow: 0 10px 30px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.14), inset 0 -1px 0 rgba(0,0,0,0.25)`): Applied to every `.glass` surface. The outer drop is ambient depth; the inset highlight + inset shadow simulate a real glass edge catching the substrate's light.
- **Champagne pulse** (`box-shadow: 0 0 8px var(--champagne)`): Topbar nav indicator dot — the only deliberate glow in the system, signaling product identity.
- **Stage dot glow** (`box-shadow: 0 0 6px var(--stage-*)`): Small luminous markers for trend-stage indicators. Subtle because stage color is also encoded by position and label.
- **Gem flash** (`box-shadow: 0 0 28px rgba(0,240,255,0.55)` at animation midpoint): Used only on the gem-card flash animation when a new hidden gem surfaces. Three-second event, then back to flat.

### Named Rules

**The No-Default-Shadow Rule.** Standard `box-shadow: 0 4px 8px rgba(0,0,0,0.1)` style drop shadows are forbidden. Depth comes from the substrate refracting through glass, or from inset borders.

**The Atmosphere-Does-The-Work Rule.** Before adding a shadow, ask whether the substrate's radial gradients already provide the depth. They usually do. Most surfaces should be flat-with-edge, not lifted-with-shadow.

## 5. Components

### Glass Panel

- **Shape:** 18px radius (`{rounded.md}`)
- **Background:** `rgba(255,255,255,0.04)` with `backdrop-filter: blur(24px) saturate(180%)`
- **Edge:** 1px `rgba(255,255,255,0.08)` border + inset highlight (top) + inset shadow (bottom). The `::before` pseudo adds a top gradient (10% → 2% white) to simulate the curve of refracted light catching the upper edge.
- **Usage:** The hero substrate for chip rails, detail panels, conversation modals, the briefing surface. Never used for buttons or chips except the chip rail which inherits via `.chip.glass`.

### Solid Panel

- **Shape:** 6px radius (`{rounded.sm}`)
- **Background:** `var(--panel-solid)` (`#121821` Bloomberg)
- **Border:** 1px `var(--line)` (cool cyan-tinted at 10% opacity)
- **Padding:** 16px
- **Hover:** border shifts to `var(--line-hi)` (cyan at 28%). Transform translateY(-2px) when card is interactive (gem-card).

### Buttons

- **Nav buttons** (topbar): No background. Geist or Fraunces family per context. Active state: champagne color + underline indicator dot.
- **Star button** (watchlist toggle): Transparent. ★ glyph in Geist Mono. Hover scales to 1.18 with `transition: color 0.15s, transform 0.15s`. Active color is Watchtower Cyan with full opacity.
- **No filled buttons** in the current system. Action affordance comes from chip-style pills and inline links.

### Chips

- **Shape:** Pill (`100px` radius)
- **Default:** transparent background, 1px line border, `--ink-2` text, Geist Mono 10px +0.08em
- **Active:** `rgba(0,240,255,0.05)` background, `--line-hi` border, `--ink` text
- **Hover:** text shifts to `--ink`, border to `--line-hi`. Transition: color, border-color, background — never `all`.
- **Glass variant** (`.chip.glass`): Inherits glass refraction. The only chip variant with `will-change: backdrop-filter`.

### Inputs

- **Search input:** `rgba(255,255,255,0.03)` background, 1px `rgba(244,241,234,0.10)` border, 4px radius, Geist Mono 11px +0.04em, `var(--observatory-ink)` text. Focus: 1px cyan outline + cyan border.
- **Niche input:** Borderless except for a 1px bottom border. Fraunces italic 15px right-aligned. Focus: champagne outline with 2px offset + champagne bottom border.
- **Both:** Placeholder uses `--ink-3` (`#5a6878` / `#6a6862`).

### Navigation

- **Style:** Topbar with Fraunces italic brand mark. Page nav buttons inline, 16px Fraunces italic.
- **Active:** Champagne color, 4×4px champagne dot indicator centered beneath with `box-shadow: 0 0 8px var(--champagne)`.
- **Hover:** color shifts to `#f4f1ea` (observatory ink).

### Signature Component: Gem Card

The hidden-gem surface. Solid panel + a 6px radius + `backdrop-filter: blur(8px)`. When a new gem surfaces, it gets the `.flash` class — a 1.4s cubic-bezier animation that pulses the border to Watchtower Cyan with a 28px diffuse glow, then back to baseline. The flash is the only deliberately spectacular motion in the system; it earns its weight because it fires rarely (when real signal lands).

A convergence variant adds a "CONVERGENCE" badge in the top-right corner (8px Geist Mono, cyan, on translucent cyan background) when multiple trend signals point at the same opportunity.

## 6. Do's and Don'ts

### Do:

- **Do use OKLCH or tinted hex for every neutral.** Surface tokens are tinted toward cool blue (Bloomberg) or warm void (Observatory). Never `#000`, never `#fff`.
- **Do reserve Watchtower Cyan for actual signal.** Active chips, focus rings, convergence badges. ≤10% of any visible surface.
- **Do pair stage color with position and label.** Color alone never carries the trend stage.
- **Do use Fraunces italics for identity moments** (topbar brand mark, observatory section headings, blockquote attributions).
- **Do use Geist Mono for every number, code, timestamp, percentage, and delta.** Tabular alignment is the point.
- **Do specify transition properties** (`transition: color 0.15s, border-color 0.15s, background 0.15s`). Never `transition: all`.
- **Do refract the substrate through glass surfaces** — every `.glass` surface needs the inset highlight + inset shadow + top gradient `::before`. Plain `backdrop-filter` is forbidden.
- **Do respect `prefers-reduced-motion`** — twinkles, gem-flash, and transitions collapse to instant when the user opts out.
- **Do keep body text at 13px or above.** Operator density does not require sub-13px type.
- **Do use `min-height: 100dvh`** for full-viewport sections. `100vh` causes iOS Safari layout jumps.

### Don't:

- **Don't ship generic SaaS aesthetics** — no purple/blue gradients, no big vague hero, no three identical feature cards, no "Get started" CTA. (PRODUCT.md anti-reference.)
- **Don't import trading-platform visual language** — no Robinhood green up arrows, no candlestick chart styling, no neon-on-black gambling energy. This is intelligence, not betting. (PRODUCT.md anti-reference.)
- **Don't default to school-project components** — no Material UI, no Bootstrap, no Roboto, no default form controls. (PRODUCT.md anti-reference.)
- **Don't use ChatGPT-clone patterns** — no rounded chat bubbles, no left sidebar of conversations, no default dark-mode toggle. This is a watchtower, not a chatbot. (PRODUCT.md anti-reference.)
- **Don't mix the two registers within one page.** Bloomberg cyan and Observatory champagne are not interchangeable. The `.page-observatory` class is all-or-nothing.
- **Don't use `transition: all`.** Specify which properties transition. `all` forces every property to recalculate on every frame.
- **Don't use `#000`, `#fff`, or `#ffffff` in hover/active states.** Use `var(--ink)` or `var(--accent)`. The single exception is the topbar brand color hover, which uses `#f4f1ea` (observatory ink).
- **Don't add decorative glassmorphism.** Glass is for hero substrates and the chip rail only. If you're reaching for `backdrop-filter` on a button or a small badge, rebuild it as a solid panel.
- **Don't use default drop shadows** (`0 4px 8px rgba(0,0,0,0.1)`). Depth comes from the substrate, not from heavy ambient shadows.
- **Don't carry stage information by color alone.** Always pair with position and label.
- **Don't go below 13px body text.** Operator-grade does not mean tiny.
- **Don't animate CSS layout properties** (`width`, `height`, `top`, `left`). Animate `transform` and `opacity` exclusively.
