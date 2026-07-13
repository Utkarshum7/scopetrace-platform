# Frontend Design System (`FRONTEND_DESIGN_SYSTEM.md`)

Phase 8 (8a–8e) — the reference for the shared UI primitives, design tokens,
and accessibility/UX conventions that came out of the enterprise UX pass.
This is a map to what exists and why, not a component API reference (read
the component source for that — every primitive below is short and has its
own docstring).

---

## 1. Shared UI primitives (`frontend/src/components/ui/`)

All new UI work should reach for one of these before hand-rolling markup.
Each was extracted from real, verified duplication (see the file's own
docstring for the specific "found in N places" story).

| Component | Purpose |
|---|---|
| `Card` | The base dark glassmorphic panel (`bg-slate-800/40 backdrop-blur-xl border-slate-700/50 rounded-xl shadow-lg`). Every top-level content panel — page sections, dashboard widgets (via `WidgetFrame`), `KpiCard`, `SelectableCard`, `FilterBar` — renders through this. Padding/gap/extra classes are supplied by the caller via `className`. |
| `PageHeader` | The `<h1>` + description block at the top of every routed page. `size="lg"` (default, `text-2xl`) for full pages, `size="md"` (`text-xl`) for a narrower context like `ESGAssistantPage`'s sidebar. Accepts an `actions` node (e.g. `DashboardFilters`) rendered alongside the title. |
| `Spinner` | The `animate-spin` SVG. Size/color via `className` (e.g. `className="h-4 w-4 text-white"`). |
| `SelectableCard` | A `role="radio"` card for radio-group-style single-select UI (UploadPage's 3 adapter cards). Owns its own Enter/Space keyboard handling. |
| `Modal` | Dialog shell: overlay, `role="dialog"`/`aria-modal`, Tab/Shift+Tab focus trap, initial focus, focus restoration on close, Escape-to-close. `ApprovalModal` is its first (and currently only) consumer — any future dialog should use this instead of re-deriving the same mechanics. |
| `ConfidenceBadge` | The AI confidence pill (LOW/MEDIUM/HIGH). Used by `AIInsightsPanel`, `ESGAssistantPage`, and `CommonWidgets`' report narration. **Note:** its color scale is deliberately inverted from typical severity coloring — HIGH confidence renders in the danger/rose family, not success/green, because a high-confidence AI claim is meant to draw more scrutiny, not less. Don't "fix" this to match `success`/`warning`/`danger` semantics; it isn't a status indicator. |
| `AIAdvisoryBadge` | The "AI Advisory" pill marking AI-generated content as advisory-only. Used by `AIInsightsPanel`, `CommonWidgets`' report narration, and `ESGAssistantPage`. |
| `Skeleton` (+ `KpiSkeleton`, `ChartSkeleton`, `ListSkeleton`) | Loading placeholders. `WidgetFrame`'s `skeleton` prop and every page-level loading row/list use one of these instead of ad-hoc "Loading…" text. |
| `EmptyState` | Centered icon + title + optional message, for "the fetch succeeded but there's nothing to show." |
| `ErrorState` | Centered icon + message + optional `onRetry` button, for "the fetch failed." Every page-level and widget-level error state in the app goes through this — if you're about to hand-roll an error box with a retry affordance, use this instead. |

Dashboard widgets never implement loading/error/empty handling directly —
they pass a `status` (`'loading' | 'error' | 'empty' | 'success'`) into
`WidgetFrame` (`components/dashboard/WidgetFrame.jsx`), which renders the
right one of `Skeleton`/`ErrorState`/`EmptyState` automatically. The
`useWidgetData` hook (`components/dashboard/useWidgetData.js`) derives that
status from a TanStack Query result plus an optional `isEmpty` predicate.

## 2. Design tokens (`frontend/tailwind.config.js`)

- **Color:** `brand` (the ScopeTrace green, `brand-500` = `#2ebb72`) is the
  primary/interactive color — buttons, links, focus rings, active nav
  state. `success` / `warning` / `danger` / `info` are semantic aliases for
  `emerald` / `amber` / `rose` / `sky` respectively (exact value aliases —
  `bg-success-950` and `bg-emerald-950` compile to byte-identical CSS).
  Prefer the semantic name for anything representing a literal status
  (approved/completed → success, suspicious/pending → warning,
  failed/rejected/error → danger). `slate` is the muted/neutral family for
  body text, borders, and de-emphasized captions — there's no separate
  `muted` token because `slate` already fills that role consistently.
  **Documented exceptions** (raw color kept deliberately, not oversights):
  - `StatusBadge`'s non-bucket workflow states (`DRAFT`=blue, `VALIDATED`=sky,
    `SUBMITTED`=violet, `REJECTED`=orange, `PROCESSING`=indigo) — these are
    distinct workflow-stage identities, not reducible to the 4 generic
    semantic buckets without losing information (there are 9 distinct
    `RecordStatus`/`BatchStatus` values, more than 4 buckets can hold).
  - `ConfidenceBadge`'s inverted rose/amber/slate scale (see above).
  - The AI-feature indigo/violet accents (`AIInsightsPanel`, `AIAdvisoryBadge`,
    the assistant message bubble) — brand/feature identity for "this is AI
    content," not a status indicator.
  - `components/charts/chartTheme.js`'s scope palette (`SCOPE_1`/`SCOPE_2`/
    `SCOPE_3` colors) — categorical chart colors, not status semantics.
- **Typography:** `fontSize` defines `3xs`/`2xs`/`xxs` (9px/10px/11px,
  each paired with an explicit line-height) matching this app's dense
  micro-label sizes. **Not yet adopted**: ~107 call sites still use the
  arbitrary `text-[9px]`/`text-[10px]`/`text-[11px]` syntax instead of these
  tokens. Reviewed in 8e and deliberately not migrated — the token's
  explicit line-height would be a real (if small) rendering change at every
  one of those sites, unlike the color-token rename (an exact alias), and
  this session had no reliable way to visually verify zero regression across
  that many locations. Treat this as open follow-up work with visual QA.
- **Layout:** `width.sidebar` (260px) and `width.drawer` (380px) name the
  two fixed structural dimensions in the app (the nav sidebar, the
  record-detail drawer).
- **Animation:** `fadeIn` / `slideIn` / `shake` keyframes+animations back
  the `animate-fadeIn` / `animate-slideIn` / `animate-shake` utility classes
  used for page-entry, drawer-entry, and error-banner attention respectively.
  These were silently-broken no-ops before 8a.1 (Tailwind drops unknown
  `animate-*` utilities without warning) — they're real now.

## 3. Accessibility conventions

- **Focus rings:** use `focus-visible:` (not bare `focus:`) on every custom
  interactive element, so the ring only appears for keyboard focus, not
  mouse clicks. `focus:` is still fine on native `<input>`/`<textarea>`
  elements where showing a ring on any focus (including click) is
  conventional and expected.
- **Decorative icons:** any SVG icon that's paired with adjacent text (i.e.
  purely decorative, not the sole content of a control) gets
  `aria-hidden="true"`.
- **Non-native interactive elements** (`<tr role="button">` for a clickable
  table row, `<div role="radio">` for `SelectableCard`) own their own
  Enter/Space keyboard handling and get a descriptive `aria-label` — used
  only where the real semantic element (`<button>`, `<input type="radio">`)
  can't host the required layout without breaking it. Prefer the real
  element first.
- **Live regions:** use `aria-live="polite"` explicitly rather than relying
  on a role's implicit live-region semantics (e.g. `role="log"`,
  `role="status"`) — AT support for the implicit value is inconsistent, the
  explicit attribute is the defensive, robust choice.
- **Headings:** follow document order — no skipped levels. Each routed
  page has exactly one `<h1>` (via `PageHeader`, or `LoginPage`'s own for
  the unauthenticated screen). Dashboard widget titles are `<h2>`
  (`WidgetFrame`) since they sit directly under the page's `<h1>` with no
  intermediate level. A modal dialog's own title (`ApprovalModal`) doesn't
  need to chain from the underlying page's outline — it's a distinct,
  temporarily-overlaid context per WAI-ARIA dialog authoring practice.
  Don't mark something a heading just because it's visually bold/prominent
  — a radio option's label (`SelectableCard`) or a KPI's caption
  (`KpiCard`) isn't a document section, even styled the same way.
- **Landmarks:** `<nav>` and `<aside>` get an `aria-label` whenever more
  than one instance of that landmark type can exist on a page at once
  (e.g. `ESGAssistantPage`'s own conversation-list `<aside>`, nested inside
  `App.jsx`'s sidebar `<aside>`).

## 4. UX conventions

- **Loading:** skeletons over spinners for content that occupies real
  layout space (tables, lists, KPI grids); a small inline `Spinner` for
  in-place indicators (submit buttons, transient "waiting for status").
- **Empty vs. error vs. loading are three distinct states**, never
  collapsed into one "couldn't show anything" catch-all — a failed fetch
  always offers `onRetry` where a retry is meaningful; a genuinely empty
  result never looks like a failure.
- **Status colors always carry redundant text**, never color alone —
  every badge/chip pairs its color with a label (WCAG 1.4.1, Use of Color).
- **Corner radius is two-tier**: `rounded-xl` for `Card`-scale top-level
  panels, `rounded-lg` for buttons/inputs/badges/nested sub-panels. A
  handful of "solid elevated surface" components (`ApprovalModal`,
  `LoginPage`'s form card) intentionally use a different, more opaque
  background (`bg-slate-900` vs `Card`'s translucent `bg-slate-800/40`) for
  a spotlight/modal feel — they still follow the same `rounded-xl` radius
  as `Card`, just not `Card`'s background treatment.

## 5. Known remaining debt

- Typography-scale adoption (section 2, above) — deferred pending visual QA.
- No toast/notification framework exists. Reviewed in 8d and deliberately
  not built — every current success/failure confirmation is either an
  in-context state change (a table row's status badge updating, a modal
  closing) or an inline banner (`ErrorState`, the hand-rolled form-error
  boxes), and that was judged adequate rather than inventing new
  architecture for this pass. If a real cross-page notification need shows
  up later, build it as an addition, not a retrofit of existing pages.
