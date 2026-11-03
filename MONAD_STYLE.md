# Monad — Style Reference
> editorial tech journal on warm parchment

**Theme:** light

Monad renders its entire interface on a warm parchment canvas (#f6f3f1) that immediately separates it from the typical pure-white SaaS template. Headlines carry editorial weight in Untitled Serif at weight 400 — never bold — while every body, nav, and UI string runs in ABC Diatype Mono, giving the site the texture of a technical manual typeset for a literary magazine. A single vivid Lake Blue (#2b59d1) is the only chromatic accent for primary actions; everything else lives in a warm grayscale, so a periwinkle card surface (#cfdaf5) and soft pastel gradient washes (coral, sky, mint, gold) read as deliberate punctuation rather than noise. Components lean on hairline borders and 100px pill containers rather than shadows, and the rhythm stays calm and spacious with 40px card padding and generous vertical breathing room between sections.

## Tokens — Colors

| Name | Value | Token | Role |
|------|-------|-------|------|
| Parchment | `#f6f3f1` | `--color-parchment` | Page canvas and the majority of surface fills |
| Lake Blue | `#2b59d1` | `--color-lake-blue` | Primary action accent |
| Periwinkle Mist | `#cfdaf5` | `--color-periwinkle-mist` | Elevated card surface / highlight block |
| Sky Blue | `#a0b5eb` | `--color-sky-blue` | Flow line gradient / atmospheric wash |
| Mint | `#a7fccd` | `--color-mint` | Small decorative accent |
| Coral | `#ff9473` | `--color-coral` | Supporting accent |
| Gold | `#ecda98` | `--color-gold` | Warm accent |
| Crimson | `#f37a0a` | `--color-crimson` | Deep warm tone |
| Off-Black | `#242424` | `--color-off-black` | High-contrast neutral action fill |
| Ink | `#000000` | `--color-ink` | Announcement bar surface / dark text |
| Graphite | `#4e4d4d` | `--color-graphite` | Secondary text |
| Smoke | `#797776` | `--color-smoke` | Muted helper text |
| Ash | `#cecac8` | `--color-ash` | Hairline borders (1px solid) |

## Tokens — Typography

* **Display / Headlines:** `Untitled Serif`, Georgia, "Times New Roman", serif (Weight: 400 locked, letter-spacing: -0.02em)
* **Functional UI / Body:** `ABC Diatype Mono`, JetBrains Mono, monospace (Weight: 400, 500)

## Tokens — Shapes & Radii

* **Pills / Tags:** `9999px`
* **Buttons:** `100px`
* **Cards:** `24px - 40px`
* **Borders:** `1px solid #cecac8` (Ash)
