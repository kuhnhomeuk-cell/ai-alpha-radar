# Product

## Register

product

## Users

Primary: judges of the JAX school community competition. They glance at the dashboard cold, with no context, and decide in seconds whether it reads as serious work.

Secondary: Dean (the operator who built it) using it after the competition to actually track emerging AI trends, keywords, and creator signals before the crowd arrives.

The interface has to clear the judge bar without compromising the operator-tool bar.

## Product Purpose

AI Alpha Radar surfaces emerging AI trends in real time — keyword velocity, hidden gem creators, demand signals, and stage progression (whisper → builder → creator → hype → commodity) — so an operator can act on signal before it becomes consensus. Success is winning the JAX competition with a dashboard that reads as a serious operator tool, not a class project.

## Brand Personality

Bold. Confident. Operator-grade.

The voice is the way a Bloomberg terminal would talk if it had taste. Direct, never apologetic, never quirky. Numbers do the talking. Copy is sparse and load-bearing — every label has earned its place. No exclamation marks, no "oops!", no over-explaining. When something is rare or fast-moving, the design says so quietly through emphasis (typography, motion, color saturation), not through screaming labels.

## Anti-references

Four traps to actively refuse:

- **Generic SaaS** — purple/blue gradients, big hero with a vague headline, three identical feature cards, "Get started" CTA. The default AI startup aesthetic.
- **Trading platform** — Robinhood-green up arrows, candlestick chart language, neon-on-black, "to the moon" energy. This is intelligence, not gambling.
- **School project** — Material UI, Bootstrap, Roboto, default form controls, generic dashboard template. Reads as low effort.
- **ChatGPT clone** — rounded chat bubbles, sidebar with conversations, default dark mode, the conversational-AI reflex. This is a watchtower, not a chatbot.

## Design Principles

1. **Judge-readable in three seconds.** First impression has to land before any explanation. Hierarchy and typography do the work — not tooltips, not onboarding.

2. **Earn the bold.** Confidence comes from decisive typography, sharp grids, and committed color choices. Not from visual noise, glows, or oversized headlines. Bold is a stance, not a style.

3. **Refuse the four reflexes.** Every design decision passes the anti-reference filter before it ships. If it could be mistaken for SaaS, trading, school project, or chatbot — rebuild it.

4. **Operator density, observatory breath.** Pack real signal into the surface, but leave the kind of negative space that says the operator is in control of the data, not drowning in it. Dashboards that pack everything in look frantic. Bloomberg-grade density without the Bloomberg-grade clutter.

5. **A11y is dignity, not compliance.** WCAG 2.1 AA is the floor. Focus rings, contrast ratios, keyboard navigation, reduced-motion respect, aria-live for streaming updates — treat each as a quality signal that separates the dashboard from a class project.

## Accessibility & Inclusion

- **Target:** WCAG 2.1 AA across all surfaces.
- **Contrast:** All text and meaningful UI elements meet 4.5:1 (normal text) or 3:1 (large text and UI components).
- **Keyboard:** Full keyboard navigation. Visible focus rings on every interactive element. No `outline: none` without a replacement.
- **Motion:** Respect `prefers-reduced-motion` — twinkles, gem-flash, transitions all collapse to instant.
- **Screen readers:** Aria-labels on icon-only buttons. `aria-live` regions for streaming trend updates and watchlist changes. Modals announce themselves and trap focus.
- **Color:** Never carry information by color alone. Stage colors (whisper / builder / creator / hype / commodity) are also encoded in position and label.
- **Type:** No body text below 13px. Champagne-on-dark and accent-cyan-on-dark have been contrast-checked.
