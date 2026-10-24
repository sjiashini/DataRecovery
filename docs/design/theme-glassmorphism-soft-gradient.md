# Mobile App Theme — Glassmorphism & Soft Gradient

A complete, implementable design-system spec for a frosted-glass UI layered over smooth multi-color gradients. Written for hand-off to a designer or an AI coding agent. Stack snippets included for SwiftUI, Flutter, React Native, and CSS/web-view.

**Best for:** modern SaaS, weather, finance, health/fitness, music, and creative lifestyle apps.
**Avoid for:** dense data tables, long-form reading, accessibility-critical forms, low-end Android targets (blur cost).

---

## 1. Core idea (the three-layer rule)

Every screen is built from a strict bottom-up stack. Never skip a layer, never exceed four glass layers on screen at once.

| # | Layer | Name | Contents | Opacity / effect |
|---|-------|------|----------|------------------|
| 0 | Base | `gradient-mesh` | 2–4 stop linear/radial gradient, fixed to viewport | 100%, no blur |
| 1 | Ambient | `color-blobs` | 3–5 oversized blurred circles, slowly drifting | 40–70% fill, blur 60–120 px |
| 2 | Grain | `noise` | Tileable SVG/PNG fractal noise, `multiply`/`soft-light` | 3–6% alpha, repeat |
| 3 | Surface | `glass-card` | Translucent panel: backdrop blur + saturate + 1 px light border + drop shadow | see §3 |
| 4 | Content | text/icons/controls | Solid, high-contrast, **never blurred** | 100% |

Rule: the glass only reads as glass if there is *something colorful and moving beneath it*. A glass card on a flat background is just a grey box.

---

## 2. Gradient palettes

Use one palette per app, one variant per screen family. Angles are clockwise from "to top". Blobs inherit their own hue from the palette at 60–100% saturation.

### A. `Aurora` — SaaS, finance, productivity (default)
- Base linear 160°: `#0F1B3D` → `#1E3A6E` → `#3B2F71` → `#122045`
- Blobs (radial, screen blend): `#5B8DEF` @ (15%, 10%), `#9A6BFF` @ (85%, 25%), `#22D3C5` @ (70%, 88%), `#F06AC0` @ (20%, 75%)
- Accent: `#7DD3FC` · success `#34D399` · warning `#FBBF24` · danger `#FB7185`

### B. `Sunfuse` — weather, lifestyle, wellness
- Base linear 145°: `#FFD3A5` → `#FD6585` → `#7F5AF0`
- Blobs: `#FF9A62`, `#FF5E9C`, `#6C63FF`, `#48CFAD`
- Ink: `#2B1B3D` (light-mode text on glass)

### C. `Deepglass` — music, night mode, premium
- Base linear 180°: `#05060C` → `#0B1024` → `#160B28`
- Blobs: `#3B82F6` @ 45%, `#8B5CF6` @ 50%, `#22D3EE` @ 35%
- Ink: `#EDF1FF` · dim ink `#9AA6C8`

### D. `Meadow` — health, habits, education
- Base linear 155°: `#E8FFF4` → `#C7F2E4` → `#D9E4FF`
- Blobs: `#57C99B`, `#7BB6FF`, `#FFC7A2`
- Ink: `#12312A`

### E. `Dusk` — social, creator, evening modes
- Base linear 170°: `#2A1147` → `#6B2B6E` → `#D6543F`
- Blobs: `#F472B6`, `#FB923C`, `#A78BFA`

**Gradient rules**
- 3–4 stops max; adjacent stops should be analogous or a soft complementary pair.
- Total hue sweep across one base: 60–140°. Beyond that it looks like a rainbow, not a mood.
- Never animate the base gradient's hue; animate blob position only (see §8).
- Blobs sit 30–60% off-canvas so their edges never appear as visible circles.

---

## 3. Glass surface tokens

| Token | Light mode | Dark mode | Notes |
|-------|-----------|-----------|-------|
| `glass.bg` | `rgba(255,255,255,0.62)` | `rgba(255,255,255,0.07)` | the tint, not the blur |
| `glass.bg-strong` (sheets, modals) | `rgba(255,255,255,0.78)` | `rgba(18,22,40,0.62)` | more legibility, less color bleed |
| `glass.blur` | `18px` | `22px` | see budgets §9 |
| `glass.saturate` | `150%` | `180%` | makes the gradient beneath look richer |
| `glass.border` | `rgba(255,255,255,0.55)` top / `rgba(0,0,0,0.06)` bottom | `rgba(255,255,255,0.18)` / `transparent` | 1 px, hairline |
| `glass.inner-highlight` | `inset 0 1px 0 rgba(255,255,255,0.5)` | same @ `0.25` | the "edge of a pane" cue |
| `glass.shadow` | `0 12px 32px rgba(10,14,40,0.18)` | `0 16px 40px rgba(0,0,0,0.45)` | soft, wide, low-alpha |
| `glass.radius` | `24px` cards · `18px` rows · `999px` pills | same | never < 14 px |
| `glass.padding` | `20px` · `16px` compact | same | ≥ 16 px so blur reads |
| `glass.gap` | `14px` between stacked cards | same | gap must show gradient, else layers merge |
| `ink` | `#101528` | `#EEF2FF` | body |
| `ink.dim` | `rgba(16,21,40,0.62)` | `rgba(238,242,255,0.62)` | captions |

**Never** put text on `glass.bg` at less than blur 12 px. Blur < 12 px + low-alpha tint = text sitting on a noisy gradient.

---

## 4. Typography

- Display/headline: one geometric or humanist sans — `SF Pro Display`, `Inter`, `Plus Jakarta Sans`, `Manrope`.
- Body: same family, regular weight. Do not mix serif into glass surfaces; the translucency already adds visual texture.
- Weights: 600/700 for card titles (thin strokes vanish against blur), 400 for body.
- Scale (mobile, dp/pt): `34 / 28 / 22 / 17 / 15 / 13` with line-height `1.15` display, `1.45` body.
- Optical treatment: on dark glass add `letter-spacing: -0.02em` ≥ 22 px; `+0.04em` for all-caps labels ≤ 13 px.
- Max measure: 42 characters per line inside a glass card.

---

## 5. Component specs

**Screen container** — full-bleed base gradient; blobs absolutely positioned; content column with `16px` side padding, safe-area aware; scroll indicators `rgba(255,255,255,0.2)`.

**Top bar / large title** — no solid bar. Title over the gradient; on scroll, a glass bar (`glass.bg-strong`, `blur 20px`, no radius) fades in above the content. Fade threshold: first 40 px of scroll.

**Tab bar (bottom)** — floating pill, `margin 12px` from screen edges, `radius 999px`, `glass.bg-strong`, `blur 24px`, height `56–64dp`; active item = solid accent-filled circle `36dp` with white glyph; inactive = `ink.dim`. Home indicator contrast ≥ 3:1 against the bar.

**Card (default)** — `glass.bg`, `blur 18px`, `radius 24px`, hairline border, shadow, `padding 20px`, `gap 12px` internal. Elevation tiers: `L1` no shadow (nested), `L2` shadow at 50%, `L3` full shadow + `blur +4px`.

**List row** — `radius 18px`, `padding 14px 16px`, min height `56dp`, chevron + `ink.dim` trailing; one glass surface per row, never a glass row inside a glass card.

**Buttons**
- Primary: solid accent, `radius 999px`, no glass (needs to be the most opaque thing on screen), `height 50dp`, shadow `0 8px 20px accent@35%`, pressed = scale `0.97` + shadow 40%.
- Secondary: `1.5px` accent border on `rgba(255,255,255,0.10)` + `blur 12px`.
- Tertiary/glass: `glass.bg` only, `ink` label.
- Destructive: solid danger, never translucent.

**Text field** — `glass.bg`, `radius 16px`, `blur 12px`, `1px` `ink@12%` border; focus = border `accent 1.5px` + outer ring `accent@25% 6px`; placeholder `ink.dim`. Label above field in `ink` (never floating over glass).

**Chip / tag** — `radius 999px`, `padding 6px 12px`, `glass.bg` + `blur 10px`, `13px` label; selected = solid accent tint at 25% + accent text.

**Sheet / modal** — `glass.bg-strong`, `blur 28px`, top `radius 28px`, drag handle `36x5dp` `ink@30%`, scrim `rgba(5,8,20,0.45)` + `blur 6px`. Content inset from scrim, not touching it.

**Segmented control** — container `radius 999px` `glass.bg` `blur 12px`; sliding thumb solid `rgba(255,255,255,0.85)` dark / `accent@15%` light with `0 4px 12px rgba(0,0,0,0.12)`.

**Toggle** — track `ink@15%`, on-track solid accent, knob solid white `28dp` + shadow. No glass on the knob (it reads as a smudge).

**Avatar / icon tile** — `radius 16px` (squircle) or `999px`; gradient fill from palette with a `2px` inner highlight ring; glyph white 90%.

**Charts** — area fill = vertical gradient `accent@35% → accent@0%`; grid lines `ink@8%`; no glass behind plot, glass only on the chart *card*. Tooltip = `glass.bg-strong`, `radius 12px`, `blur 16px`.

**Toast / snackbar** — `radius 18px`, `blur 20px`, `glass.bg-strong`, 3 dp left accent bar, `max 2 lines`, auto-dismiss 4 s, appears `12dp` above the tab bar (never over it).

**Skeleton loader** — `ink@8%` blocks with a moving specular sweep (`linear-gradient 100° transparent → white@25% → transparent`, 1.6 s loop). Do not blur skeletons.

---

## 6. Light vs dark behaviour

| | Light | Dark |
|---|---|---|
| Glass tint | white 55–80% | white 6–12% **plus** a dark base |
| Blur | 14–20 px | 20–30 px |
| Border | white top hairline | white 15–20% all round |
| Shadow | colored, low alpha (never pure black) | black, higher alpha, larger spread |
| Text on glass | `#101528` | `#EEF2FF` |
| Pitfall | glass goes invisible — raise tint alpha | blur turns muddy — raise saturate, lighten blobs |

Ship both. A single mid-grey glass tint that is used for both modes always fails contrast in one of them.

---

## 7. Accessibility gates (blocking, not advisory)

1. Body text on glass: **≥ 4.5:1**; large text ≥ 3:1; icons/interaction targets ≥ 3:1.
2. Compute contrast against the *worst* pixel the gradient can show through the glass, not the average. Test each palette corner. If it fails, raise `glass.bg` alpha or add a `linear-gradient` scrim behind text — do not reduce blur to "see more".
3. Never place text over a blob's brightest 30% without a stronger surface.
4. Minimum tap target `44x44dp`; adjacent glass targets ≥ 8 dp apart (blur hides hit-area edges).
5. Respect `reduceTransparency` / `KEY_BLUR` / `prefers-reduced-transparency`: fall back to opaque surface at `glass.bg` lightness + 6%, keep radius and shadow.
6. Respect `prefers-reduced-motion`: freeze blobs, disable parallax and sheen sweeps (§8).
7. Grain/noise layer must be `pointer-events: none` and decorative-only (excluded from accessibility trees).
8. Don't signal state with tint alpha alone; pair with color + glyph.

---

## 8. Motion

| Motion | Spec |
|--------|------|
| Blob drift | 20–40 s loop, translate ≤ 12% of viewport, `ease-in-out`, no hue change |
| Parallax | on scroll, blobs move at 0.2–0.35× content speed; cards at 0.9× |
| Card enter | fade 0→1 + `translateY 12px→0`, 240 ms, `cubic-bezier(0.22,1,0.36,1)`, stagger 40 ms |
| Sheet | spring, damping 0.8, stiffness 260; backdrop blur animates *with* it, never snaps |
| Press | scale 0.97, 90 ms; release 140 ms |
| Specular sheen | only on hero card, 1.2 s once on enter, never looping |
| Tab switch | thumb slides 200 ms; glass bar does not re-blur |

Blur radius must **not** animate per-frame (it re-rasterizes the whole layer). Animate alpha and transform instead.

---

## 9. Performance budgets (mobile)

- ≤ 4 backdrop-blur surfaces on screen; ≤ 2 that cover more than half the viewport.
- Blur radius ≤ 30 dp. Cost scales roughly with radius × area; on 2–3 year-old Android, one full-screen blur at 30 dp already drops frames.
- Pre-render blobs into a single bitmap/texture rather than blurring live circles; if blurring circles, cap at 4 and clamp their layer size.
- Grain: one 128–256 px tile, repeated — never a full-screen PNG.
- Flatten/snapshot a glass card once its parent sheet is stationary.
- Prefer platform materials (see below) over custom blur — they are GPU-native and battery-cheap.
- Lottie/video backgrounds: disable when battery-saver is on; cap at 24 fps.
- Target: 60 fps mid-tier Android, 120 Hz ProMotion on iOS.

---

## 10. Implementation snippets

### CSS / web-view
```css
.screen {
  position: relative; isolation: isolate; min-height: 100dvh; overflow: hidden;
  background: linear-gradient(160deg,#0F1B3D,#1E3A6E 34%,#3B2F71 68%,#122045);
}
.screen::before {           /* blobs */
  content:""; position:absolute; inset:-25%; z-index:-1; filter:blur(90px); opacity:.65;
  background:
    radial-gradient(32% 32% at 18% 12%, #5B8DEF, transparent),
    radial-gradient(30% 30% at 84% 26%, #9A6BFF, transparent),
    radial-gradient(28% 28% at 72% 88%, #22D3C5, transparent);
  animation: drift 32s ease-in-out infinite alternate;
}
.screen::after {            /* grain */
  content:""; position:absolute; inset:0; z-index:-1; pointer-events:none; opacity:.045;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.8' numOctaves='3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
}
.glass {
  background: rgba(255,255,255,.07);
  -webkit-backdrop-filter: blur(22px) saturate(180%);
  backdrop-filter: blur(22px) saturate(180%);
  border: 1px solid rgba(255,255,255,.18);
  border-radius: 24px;
  box-shadow: 0 16px 40px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.25);
}
@keyframes drift { to { transform: translate3d(6%, -4%, 0) scale(1.08); } }
@supports not (backdrop-filter: blur(1px)) { .glass { background: rgba(20,26,48,.72); } }
@media (prefers-reduced-transparency: reduce) { .glass { background:#141A30; box-shadow:0 16px 40px #000 } }
```

### SwiftUI (iOS)
```swift
ZStack {
    LinearGradient(colors: [Color(hex: "0F1B3D"), Color(hex: "1E3A6E"),
                            Color(hex: "3B2F71"), Color(hex: "122045")],
                   startPoint: .topLeading, endPoint: .bottomTrailing)
    Canvas { ctx, size in            // blobs
        for (c, p) in [("5B8DEF", CGPoint(x: .16, y: .12)), ("9A6BFF", CGPoint(x: .84, y: .26)),
                       ("22D3C5", CGPoint(x: .72, y: .88))] {
            ctx.addFilter(.blur(radius: 60))
            ctx.fill(Path(ellipseIn: CGRect(x: p.x*size.width-90, y: p.y*size.height-90,
                                            width: 180, height: 180)),
                     with: .color(Color(hex: c).opacity(0.7)))
        }
    }
    Card().padding(16)
}
.ignoresSafeArea()

struct Card: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) { /* content */ }
            .padding(20)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous)
                .strokeBorder(.white.opacity(0.18), lineWidth: 1))
            .shadow(color: .black.opacity(0.45), radius: 20, y: 12)
}
```
`.ultraThinMaterial` / `.thinMaterial` are the native frosted layers — prefer them over a hand-rolled blur. Honor `@Environment(\.accessibilityReduceTransparency)` by swapping to a solid `Color(0x141A30)`.

### Flutter
```dart
Stack(fit: StackFit.expand, children: [
  const DecoratedBox(decoration: BoxDecoration(gradient: LinearGradient(
    begin: Alignment.topLeft, end: Alignment.bottomRight,
    colors: [Color(0xFF0F1B3D), Color(0xFF1E3A6E), Color(0xFF3B2F71), Color(0xFF122045)]))),
  const BlobField(),                       // CustomPainter + ImageFiltered.blur(60)
  Padding(padding: const EdgeInsets.all(16), child: GlassCard()),
]);

class GlassCard extends StatelessWidget {
  Widget build(BuildContext context) => ClipRRect(
    borderRadius: BorderRadius.circular(24), child: BackdropFilter(
      filter: ImageFilter.blur(sigmaX: 11, sigmaY: 11),   // css px / ~2
      child: DecoratedBox(decoration: BoxDecoration(
        color: Colors.white.withAlpha(18),
        border: Border.all(color: Colors.white.withAlpha(46)),
        borderRadius: BorderRadius.circular(24)),
        child: Padding(padding: const EdgeInsets.all(20), child: child)),
    ),
  );
}
```
Wrap blobs in `RepaintBoundary`, cache the noise tile, and keep `BackdropFilter` count low — each one forces an offscreen pass.

### React Native
```tsx
<LinearGradient colors={['#0F1B3D','#1E3A6E','#3B2F71']} style={StyleSheet.absoluteFill}>
  <Canvas>{/* react-native-skia blurred circles */}</Canvas>
  <BlurView intensity={22} tint="dark" style={styles.glass}>
    <ThemedCard/>
  </BlurView>
</LinearGradient>
```
`expo-blur` on iOS; on Android use `blurjs`/Skia `BackdropFilter` or accept a solid fallback — platform blur is API 31+. Ship the opaque fallback with `Platform.select`.

---

## 11. Anti-patterns

1. Grey-glass on grey-background: no gradient beneath → no glass.
2. More than 4 blur surfaces, or nested glass-in-glass (compounds into mush).
3. Blur as decoration on text itself, or `opacity: 0.5` on a card instead of tinting its background.
4. Thin (300–400 weight) type on low-tint glass.
5. Full-opacity black/white scrim behind glass "for readability" — that's a solid card with a pointless blur pass.
6. Hard 1 px dark borders (glass edges are light-catching, not outlined).
7. Looping sheen animations, animated blur radius, or 60 fps blob drift.
8. Rainbow base gradients (hue sweep > 180°).
9. Translucent primary CTA on a translucent card — the two most important layers must differ in opacity.
10. Dropping the grain layer: without it, large blur areas show banding on OLED.

---

## 12. Paste-into-agent prompt (condensed)

> Build this screen with the Glassmorphism + Soft Gradient theme.
> Screen container: `linear-gradient(160deg,#0F1B3D,#1E3A6E,#3B2F71,#122045)`, 3 blurred color blobs (`#5B8DEF`,`#9A6BFF`,`#22D3C5`) at 60 px blur, 65% opacity, drifting on a 30 s loop, plus a 4%-alpha grain overlay.
> Content sits in `.glass` surfaces: `rgba(255,255,255,.07)` + `backdrop-filter: blur(22px) saturate(180%)` + `1px rgba(255,255,255,.18)` border + `24px` continuous radius + `inset 0 1px 0 rgba(255,255,255,.25)` + `0 16px 40px rgba(0,0,0,.45)`.
> Max 4 glass layers, none nested. Primary CTA is solid `#7DD3FC` pill, height 50 dp. Type: Inter 600 titles `#EEF2FF`, 400 body `rgba(238,242,255,.62)`; all text ≥ 4.5:1 against the worst-case gradient pixel.
> Honor reduce-transparency (solid `#141A30` fallback) and reduce-motion (frozen blobs, no sheen). Bottom nav is a floating glass pill, 56 dp, 12 dp from edges.

---

## 13. QA checklist

- [ ] Both light + dark verified on the same screen
- [ ] Contrast measured against worst-case gradient pixel (all 4 corners)
- [ ] ≤ 4 blur surfaces; blur radius ≤ 30 dp
- [ ] Text legible at 100% in sunlight simulation (max brightness, no filter)
- [ ] Reduce-transparency fallback renders (iOS/Android/web)
- [ ] Reduce-motion fallback renders
- [ ] Tap targets ≥ 44 dp, ≥ 8 dp apart
- [ ] Safe-area insets respected under floating nav
- [ ] Scroll at 60 fps on a mid-tier Android device — no blur-per-frame
- [ ] Screenshots reviewed at 1× on a real device, not only in a design frame
