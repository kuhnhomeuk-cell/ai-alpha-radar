# Session — 2026-05-17 · Radar UI bug fixes + rhythm rebuild

**TL;DR** Fixed 36 specified UI bugs across the Radar dashboard, then a 20-item prioritized design critique focused on composition / rhythm / hierarchy. Six commits on `main`.

## What we discussed
- 36 specific UI bugs across 10 priority groups (real bugs, alignment, leaderboard, chart-side, filter row, visualization, hierarchy, UX gaps, minor, small alignment).
- A follow-up design critique with 20 prioritized items themed around "wrapped, not composed" — the layout looked squeezed rather than designed.
- Live in-browser verification via the preview server at every step.

## What we decided
- **Chart panel header** → "Headroom × Velocity Map" + a rewritten data dictionary + a stage-color legend.
- **X-axis tick labels** were swapped vs the title — fixed to satTick=0→NICHE / satTick=100→MAINSTREAM with end/start anchors so MAINSTREAM no longer clips.
- **Label collision pass** now uses 30% bbox-overlap threshold; top-N labels capped at 3 with hover `<title>` for full detail.
- **Leaderboard** rebuilt as a true CSS grid with `minmax(0, 1fr)` keyword column and always-emitted spark slot — every row has 6 children, TBTS column has 0 spread.
- **Filter row** split into a deliberate 2-row layout: primary row Stage / Source / Sort (8px-within / 32px-between rhythm), secondary row Overlay + Daily Briefing.
- **Daily Briefing** pulled out of the chip stream into an outlined utility-action — no longer competes with filter chips.
- **Watchlist** deduped — topbar pill is now the single global jump + toggle.
- **Today's Pick panel** sits above the leaderboard, anchored to the #1 TBTS trend (EXPLODING-orange accent, "Open trend detail →" CTA).
- **Nav pills** carry count badges; 0-count sections dim with explanatory tooltips.
- **Source-health** stripped of the heavy glass treatment — it's a status strip, not a panel (blur 30→8, opacity 0.78).
- **Footer** aligned to a 3-col grid sharing the dashboard's outer gutters.
- **Active vs inactive chips** clearly differentiated — inactive chips quieted to ink-3 / 2.5% bg; active chips get a stronger teal fill + inset glow ring.

## What's next
- At narrow viewports (≤1221px) Stage+Source+Sort still wraps to two rows; chip padding 10→8 would land it on one line.
- Pipeline must emit per-trend `hook` / `angles[0]` copy for the Today's Pick panel — TODO is in `renderTodayPick`.
- Open product questions surfaced: bubble color encoding dimension (interim: stage), search scope (interim: trends only), watchlist canonical entry point (interim: topbar pill).
- Possible follow-up: reduce chip padding by 2px globally so the primary filter row fits on one line at 1221px without sacrificing tap target size.

## Commits on `main`
- `2d037e7` GROUP A real bugs #1-3 + GROUP F critical alignment #20-22
- `bca5a37` GROUP G #23-25 + GROUP H #26-29 chart and leaderboard alignment
- `6a965db` GROUP I #30-33 + GROUP J #34-36 + incidental #11/16/18/19
- `8b679b0` GROUP B visualization #4-7
- `95a796b` GROUP C #8-12 + GROUP D #13-16 + GROUP E #17-19
- `d147fd9` verification refinements — nav wrap, label collision, Today's Pick height
- `82ffb5fd` rebuild filter row + topbar rhythm + chart breathing room + leaderboard polish
- `6339622` Phase 5 — quiet source-health, align footer, active states clearer
- `b6ad13c` leaderboard stage line stops wrapping mid-word
