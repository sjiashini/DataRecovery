# Aura — No-Gradient Glass Theme · Brand Palette Spec

Mobile design system: frosted panels whose depth comes from **pattern refraction, content-derived ambience, and a tonal ladder** — never a gradient. Built on five brand tokens; every derived value below was contrast-measured, not eyeballed.

**Companion preview:** `visualizations/aura-brand-frag.html` (6 screens + ratio board).
**Previous, gradient-based spec:** `theme-glassmorphism-soft-gradient.md` — superseded by this file for any screen that ships.

---

## 1. The five brand tokens, and what they are allowed to do

| Token | Value | Verdict | Measured |
|-------|-------|---------|----------|
| `--primaryColor` | `#2596be` | **UI-only.** Fill, border, icon, focus ring, selection. *Not* a text colour on any light surface. | 3.40 on `#fff`, **2.83 on `#ebeaea`** — fails the 3:1 non-text floor on the page colour |
| `--secondaryColor` | `#666666` | Muted text ≥ 13 px. No headroom, so it is banned below 12 px. | 5.74 on `#fff`, **4.78** on `#ebeaea`, 5.36 on glass |
| `--tertiaryColor` | `#ebeaea` | **Page canvas only.** Never a card surface — a card must be lighter than what it sits on. | ink on it 15.70 |
| `--darkColor` | `#000000` | **Headings and button labels only.** See §2.3. | 21.00 on `#fff` |
| `--lightColor` | `#ffffff` | Card fill, and label on dark/primary fills. | 19.23 on dark page |

Your palette has **no dark surface and no semantic colours**. Those are derived in §2 and §4 and are the only invented values in this system.

---

## 2. Derived ramps

### 2.1 Primary (sampled from `#2596be`, same hue 197°, rising luminance steps)
```
--p50  #eaf4f9   selected/behind-text wash (the ONLY brand wash allowed)
--p100 #d3e9f2   disabled primary fill
--p200 #a9d4e6   decorative shape on dark
--p300 #6fb8d3   decorative shape, non-informational
--p400 #3d9fc3   icon glyph on dark fills (3.02 on #fff → never text)
--p500 #2596be   BRAND. fill / border / ring / icon
--p600 #1a7ca0   CTA fill when the label must be white → 4.74
--p700 #155f7b   ACCENT TEXT + selected chip fill → 7.11 on #fff, 5.92 on #ebeaea
--p800 #124c62   pressed accent text
--p900 #0e3b4d   dark-mode-tinted deep panel, illustration base
```
**Rule:** accent-coloured *text* is always `--p700`. Accent-coloured *thing you click* is `--p500`. White label on a primary button is illegal — see §2.2.

### 2.2 The label-on-primary decision
`#fff` on `#2596be` = **3.40 → fails AA** for the 12–13 px bold label a mobile button actually uses. Three compliant options; pick one and never mix them:
- **(a) Ink on brand — recommended.** `#000`/`#0b1218` on `#2596be` = **5.55**, keeps brand blue at full saturation, reads as the intentional choice rather than a compromise.
- (b) Darken the fill to `--p600 #1a7ca0` → white label = **4.74**. Use when brand rules demand white-on-blue.
- (c) Keep `#2596be` fill + white label and treat every CTA as *large text* (≥ 18.66 px bold) — only true for tablet/TV layouts.

### 2.3 Neutrals
```
--ink        #000000   headings ≥ 17 px, button labels on tints
--ink-body   #0b1218   body text → 18.85 on #fff. Pure #000 at body size on OLED blooms against anti-aliased glass edges.
--ink-muted  #666666   ≥ 13 px only
--sec-700    #4d4d4d   captions 10–12 px, placeholder, disabled label → 8.45
--sec-800    #3d3d3d   pressed muted
--bd         rgba(11,18,24,.10)   hairline on light; note: borders at this alpha are decorative (1.37:1), not the sole state indicator
--bd-strong  rgba(11,18,24,.16)   input borders
```

### 2.4 Dark family (derived — nothing in your palette covers it)
```
--d-page     #0b0f13   neutral with 6% blue cast
--d-scaffold #11171d   tonal step 2
--d-raise    #1b232b   tonal step 3
--d-ink      #e6edf3   16.28 on dark page
--d-muted    #9aa6b2   7.3 — safe for captions
--d-acc      #4fb4dc   BRAND BLUE LIFTED. #2596be itself is 3.94 on dark — too dim for a control. Lifted value = 8.15.
--d-acc-strong #8ed3ee 11.63, accent text on dark
--d-on-acc   #08130d   label on the lifted accent (7.98)
```

---

## 3. Depth model — three mechanisms, no gradient

### 3.1 Pattern refraction (system C — the global skin)
A 24 px SVG dot pattern in `--p500` at **7.5% / 5.5% alpha** tiles the page. It is the layer the blur refracts, which is what licenses translucency at all. Above ~9% alpha it reads as noise behind body text; below ~4% the glass stops reading as glass.

```svg
<pattern id="dotb" width="24" height="24" patternUnits="userSpaceOnUse">
  <circle cx="2"  cy="2"  r="1.15" fill="#2596be" fill-opacity=".075"/>
  <circle cx="14" cy="14" r="1.15" fill="#2596be" fill-opacity=".055"/>
</pattern>
```

### 3.2 Content-derived ambient (system A — hero zones only)
Where artwork, thumbnails or a photo card genuinely scrolls beneath a panel, the panel goes glass. Solid flat colour blocks + one scrim (page colour at 42%) replace the gradient. This is the only place blur is *free* to be expressive.

### 3.3 Tonal ladder (system B — everywhere else)
Where nothing moves behind the surface, translucency is a lie. Use solid steps whose luminance separates monotonically:

| Tier | Light | Dark | Use |
|------|-------|------|-----|
| page | `#ebeaea` | `#0b0f13` | canvas |
| s1 | `#f7f7f7` | `#11171d` | nested / inactive |
| s2 | `#ffffff` | `#1b232b` | standard row |
| s3 | `#ffffff` + `--sh` | `#1b232b` + shadow | elevated / new attention |
| glass | `.66` white + blur | `.055` white + blur | over pattern or content |

### 3.4 Glass recipe
```css
--glass:   rgba(255,255,255,.66);   /* composites to #f7f7f7 over the page → ink 17.60, #666 5.36 */
--glass-s: rgba(255,255,255,.84);   /* sheets, toasts, anything under body text */
--blur: 16px;                        /* budget; no saturate() — see below */
border: 1px solid rgba(11,18,24,.07);
border-top: 1px solid rgba(255,255,255,.95);        /* rim = the light source */
box-shadow: 0 10px 28px rgba(11,18,24,.10), 0 2px 6px rgba(11,18,24,.05),
            inset 0 1px 0 rgba(255,255,255,.9);
```
**`saturate()` is removed** from the recipe. It existed to make a gradient punch through glass; over neutral grey content it desaturates brand blue and shifts any UI-adjacent skin tones. **Blur is cut 22 → 16 px**: with a monochrome base there is less chroma to diffuse, and 16 px is ~35% cheaper per layer on mid-tier Android.
**Rim light is mandatory.** Without a colourful base, the brightened top border carries most of the "this is glass" signal.

Dark mode: `rgba(255,255,255,.055)` → composites to `#1c2024`; `--d-ink` on it = 13.87, `--d-acc` = 6.94.

---

## 4. Semantic colours (derived, none existed)
```
--success #1f7a50  5.31 on #fff  · on glass ≥ 5.0
--warning #a65e00  4.97 on #fff  · fill + dark ink label (never tint-only)
--danger  #b03024  6.37 on #fff  · error border + label
```
Rejected: `#1f8a5b` (4.33), `#b4740a` (3.86), `#c0392b` (5.44 — passes, but too warm against brand blue 197°). State is always **colour + glyph + text**, never alpha or hue alone.

---

## 5. Scale
- **Radius** `20` cards · `14` fields/rows · `999` pills · `9` avatars. Never under 9 — a sharp corner on a translucent panel exposes the blur seam.
- **Spacing** 4 / 8 / 12 / 16 / 20 / 28. Card padding 14; row 10×12; gap between stacked cards ≥ 9 so the pattern shows through, or two panels merge into one.
- **Type** Inter / SF Pro. `24 / 20 / 17 / 13 / 11 / 10`. Weights 700 headings, 600 labels, 400 body — no 300 except the numeric hero (`.555` on brand tint, ≥ 40 px, decorative).
- **Elevation shadow** light: `0 10px 28px rgba(11,18,24,.10)`; dark: `0 16px 36px rgba(0,0,0,.55)`. No primary-coloured shadows — they muddy the only hue in the system.

---

## 6. Components
- **Primary CTA** `height 44`, `--p500` fill, **`--darkColor` label** (§2.2a), shadow `0 6px 16px rgba(37,150,190,.30)` — the one place brand-blue glow is allowed. Pressed: `--p700` fill, scale .97.
- **Secondary** `1.5px --p600` border, `--p700` label, `rgba(255,255,255,.6)` + 12 px blur.
- **Tertiary** bare `--p700` label, no surface. Minimum 44 dp hit area.
- **Field** s2 solid `#fff`, `--bd-strong` border, `--radius-sm`. Focus `1.5px --p500` + `0 0 0 4px rgba(37,150,190,.18)`. Error `--danger` border **and** label. Placeholder `--sec-700`.
- **Chip** pill, `rgba(255,255,255,.7)` + 10% ink border, `--sec-700` label. Selected = `--p700` fill + white label (5.92 → AA at 10 px bold passes as large text; if your audit disagrees, use `--p600` fill).
- **Nav bar** floating pill, 50 dp, 13 dp from edges, `--glass-s` + 20 px blur, rim top border. Active = `--p500` solid circle + `#000` glyph.
- **Sheet** `rgba(255,255,255,.84)` + 24 px blur, top radius 26, 32×4 handle at ink 16%, scrim `rgba(11,18,24,.42)` — the scrim is page-coloured, not black, which keeps it on-palette.
- **Toast** `--glass-s` + 18 px blur, 3 px status bar in the semantic colour, 12 dp above the nav, 4 s.
- **Chart** bars solid `--p500` at 90% opacity — no vertical fade-to-transparent fill (that technique only existed to blend a gradient away). Grid/track `rgba(11,18,24,.09)`.
- **Skeleton** `rgba(11,18,24,.08)` block; sweep uses a `repeating-linear-gradient` dash mask, not a specular sheen.
- **Empty state** icon tile = `--p500` square, radius 14, inside a glass panel with `inset 0 2px 0 rgba(255,255,255,.4)`.

---

## 7. Accessibility gates
1. Body text ≥ 4.5:1 measured against the **composited** glass colour (`#f7f7f7` / `#fbfafa` / `#1c2024`), never against the token.
2. `--p500` may not be used for text, links, or a placeholder on light surfaces. `--p700` is the text value.
3. `--p500` borders on the `#ebeaea` page are **2.83:1** → below the 3:1 non-text minimum. Any control whose *only* affordance is a brand-blue outline must additionally change fill, weight or glyph on focus/selection.
4. `--secondaryColor #666` is legal ≥ 13 px. Below that, use `--sec-700`.
5. Non-text contrast ≥ 3:1 for icons, focus indicators, chart bars, switch knobs.
6. Tap targets ≥ 44×44 dp, ≥ 8 dp apart (blur hides hit-area edges).
7. `prefers-reduced-transparency` / iOS *Reduce Transparency* / Android *Remove animations+blur*: glass → its composite hex (`#f7f7f7` / `#1c2024`), pattern layer off, radius and rim kept.
8. `prefers-reduced-motion`: pattern is static already; kill skeleton sweep and toast slide.
9. Pattern + grain are decorative: `pointer-events:none`, excluded from the a11y tree.
10. Don't ship the primary CTA with a white label unless §2.2b fill swap is applied.

---

## 8. Performance
- ≤ 4 blurred surfaces on screen; ≤ 1 full-bleed.
- Blur 16 px fixed — **never animate the radius**, animate alpha/transform.
- Pattern = one 24 px SVG tile referenced by `<pattern>`/`repeat`, not a full-screen raster.
- Sheets: freeze blur once the spring settles.
- Prefer native materials where the stack has them (iOS `UIVisualEffectView`, Compose `Modifier.haze`/`Modifier.blur` API 31+), with the §7.7 solid fallback for Android < 12 — `--glass` composited hex is a single constant, so the fallback costs nothing to specify.

---

## 9. Paste-into-agent prompt
> Theme: no-gradient glass, brand blue on neutral. Page `#ebeaea` with a 24 px SVG dot pattern in `#2596be` at 7.5% alpha. Panels: `rgba(255,255,255,.66)` + `backdrop-filter: blur(16px)`, **no saturate**, `border:1px rgba(11,18,24,.07)` with **top border** `rgba(255,255,255,.95)`, radius 20, shadow `0 10px 28px rgba(11,18,24,.10)` + inset top highlight.
> Text: headings `#000`, body `#0b1218`, muted `#666` (≥13 px) else `#4d4d4d`. Accent text/links **`#155f7b`** — never `#2596be`.
> Primary button: `#2596be` fill with **black label** (`#2596be` + white is only 3.40:1). Secondary: `1.5px #1a7ca0` border, `#155f7b` label.
> Surfaces with nothing moving behind them are solid tonal: `#f7f7f7` → `#ffffff` → `#ffffff`+shadow. Glass only over the pattern or over scrolling content.
> Semantics: `#1f7a50` / `#a65e00` / `#b03024`. Dark mode: page `#0b0f13`, ink `#e6edf3`, accent `#4fb4dc`, glass `rgba(255,255,255,.055)`.
> Reduce-transparency fallback: panel = `#f7f7f7` solid (dark `#1c2024`), no pattern, keep radius and rim.

---

## 10. Anti-patterns specific to this palette
1. White label on `#2596be` (3.40). The single most common way this brand fails an audit.
2. `#2596be` body/link text on `#ebeaea` (2.83).
3. `#666` at 10–11 px (4.78 on the page — passes, but sub-pixel rendering eats it).
4. `saturate()` left in the glass recipe.
5. A card filled `#ebeaea` sitting on the `#ebeaea` page — invisible.
6. Brand-blue shadows behind every panel; the glow belongs to the CTA only.
7. Gradient reintroduced "just for the chart fill" — use solid `--p500` at 90%.
8. Glass nested in glass; the second layer composites to near-white and the pattern refraction is gone anyway.
9. Pattern alpha > 9% (text vibration) or < 4% (glass reads as flat grey).
10. Pure `#000` for body copy.

---

## 11. QA
- [ ] Primary CTA label contrast re-measured against the *rendered* fill (color-managed displays shift `#2596be`)
- [ ] Every text token checked against the composite, not the swatch
- [ ] No `--p500` in any text role
- [ ] ≤ 4 blur layers; blur radius constant
- [ ] Reduce-transparency fallback = solid composites
- [ ] Pattern visible at 100% zoom but not behind body text
- [ ] Android < 12 build shows the solid fallback, not an unblurred translucent box (the classic `BlurView` failure)
- [ ] Dark mode: `--d-acc` used for all interactive affordances, `--p500` reserved for nothing
- [ ] Screenshot pass on a real device at 1×

---

*Contrast values computed with the WCAG 2.1 relative-luminance formula, sRGB, and alpha-blend compositing; ratios are to 2 dp.*
