# UI revision after the UAT (Phase 1: audit and proposal)

> **Status (2026-11-21): the current design is section 9** (ASSUMPTIONS 413, `b-*.png`): the original identity again
> (navy -> blue gradient frame, rounded panel, glass, pills, navy logo, the Dashboard's filled banner and spotlight) in
> the calmer colours of option B (`../options/`), a neutral grey panel, the type scale, red-text destructive buttons, one
> primary per page or card, and Needs attention / pending approvals with an accent bar. Sections 1–8 are history: the
> audit (1–3), the rejected flat look (5), the blue revisions (6–7) and the rejected teal / cream palette (8).

NAVIGATU tester, UAT 2026-11-17: *"The colors are too bright. The spacing is inconsistent. Finding the important buttons
is hard, and there is no hierarchy on the level of importance on every page."*

Visual and layout only: no business rule, URL, permission or database change. Every button stays, with the same
link / form / behaviour; only its style, position or grouping changes.

Files here: `before-*.png` (current pages, 1366 px wide, full content height, demo data, as the demo Director),
`after-*.png` (the same pages with `prototype.css` + `prototype.js` injected by Playwright: a working preview of
Phases 2–3, **not loaded by the app**).

---

## 1. Audit of the current UI

### 1.1 Colours actually used

| Where | Value | Problem |
|---|---|---|
| Whole-screen frame (`.app-bg`) | gradient #22378F → #60CDEE, 135° | The loudest thing on every page; the content panel floats on it, so every page is "blue frame + light panel". |
| Sign-in page (`.auth-bg`) | same gradient, full screen + glass card | Fine for a one-off landing screen; keep (see 2.1). |
| Welcome banner (dashboard) | gradient #1D4ED8 → #60CDEE | A second big saturated block. |
| Spotlight "Next activity" / date tiles | gradient #22378F → #1D4ED8 | A third saturated block, side by side with the banner. |
| Primary (buttons, links, active tab, selected pill) | #1D4ED8 | Bright royal blue; used for the active tab, segmented controls and many buttons, so it no longer marks "the" action. |
| KPI numbers | teal #0D9488 | A fourth accent colour (3.7:1, large text only). |
| Accent | #14B8A6 | Donut, focus decoration. |
| Page / panel | #F4F6FB / #F7F9FC | OK. |
| Card border | #E6EAF2 (70 %) + blue-tinted shadow `0 10px 30px rgb(34 55 143/.08)` | Heavy floating shadow on every card. |
| Text | ink #0F172A, muted #475569 | OK (17.9:1, 7.6:1). |
| Input border | slate-300 #CBD5E1 | **1.5:1 on white: below the 3:1 WCAG non-text minimum.** |
| Badges | slate-100/sky-100/amber-100/green-100/red-100/blue-100 with 700–800 text | Saturated "-100" fills; many badges per row. |
| Buttons | primary #1D4ED8, danger #DC2626 (solid), warning amber-100 fill + amber-300 border, secondary white, ghost primary text | Solid red and amber buttons are used for normal, frequent actions. |
| Sidebar badge | amber-400 #FBBF24 | Very bright. |
| Hard-coded hex in templates | 29 different values (#CBD5E1 ×13, #F1F5F9 ×12, #5EEAD4, #0B1A5C, #fbf6e9, …) | Mostly charts and the illustration; a few should become tokens. |

### 1.2 Spacing actually used

| What | Values found | Problem |
|---|---|---|
| Page padding (`main`) | 20 px, 24 px from `sm`, 28 px from `xl` | Three values; 28 px is off any 4/8 scale. |
| Card padding | `p-6` ×75, `p-5` ×25, `p-8` ×20, `p-10` ×8, `p-9`, `p-4`, `p-3.5` | 7 values; side-by-side cards with 20 and 24 px. |
| Gap between cards / sections | `space-y-5` ×26, `space-y-4` ×25, `space-y-6`, `gap-5`, `gap-4`, `gap-6` | 16, 20 and 24 px mixed on the same kind of layout. |
| Gaps in rows | `gap-2` ×164, `gap-3` ×122, `gap-1.5` ×43, `gap-2.5` ×16, `gap-3.5` ×7 | Half-steps (6, 10, 14 px) everywhere. |
| Vertical margins | `mt-0.5` ×100, `mt-1.5` ×69, `mt-1`, `mt-2`, `mt-3`, `mt-4`, `mt-5`, `mt-6`, `mt-7`, `mt-8` | 10 different steps. |
| Card corners | cards 24 px, KPI 20 px, panel 28 px, attention box 16 px, inputs 8 px, buttons pill (9999 px) | 6 radii. |
| Text sizes | `text-sm` ×456, `text-xs` ×182, `text-[13px]`, `text-[15px]`, `text-[28px]`, `text-lg`, `text-xl`, `text-2xl` | Card titles are `text-lg` (18 px), page titles `text-2xl` (24 px) in the top bar AND again in the page (activity page: the title twice). |

### 1.3 Button styles in use (templates)

`btn-sm` 151 · `btn-secondary` 97 · `btn-primary` 66 · `btn-ghost` 55 · `btn-arrow` 15 · `btn-danger` 7 ·
`btn-warning` 3, plus one hand-made style (white button on the banner: `btn btn-sm bg-white text-primary`) and the
glass-white buttons in the spotlight. Two heights (40 px, 36 px), pill-shaped in the app, 8 px on the sign-in card.

**"Ask AI" is a solid primary button in the top bar of every page**, so every page starts with one loud button that is
not the page's action.

### 1.4 Page by page

| Page | Spacing | Hierarchy problems |
|---|---|---|
| **Dashboard** | Banner and spotlight: 24 px corners on a 28 px panel; cards 24 px apart, KPI row 20 px. | Ask AI (solid) + banner "Review items" (white) + "New activity" (glass) + spotlight "Mark attendance" (white) + **4 solid red "Upload"**, 1 amber "Upload", 1 solid blue "Review" in Needs attention: **8 loud buttons**, and the real page action (New activity) is the quietest one. Two saturated gradient blocks at the top. |
| **Activities list** | `space-y-5`, cards 20 px apart; row padding varies. | "New activity" (primary) is inside the card, under the KPI cards; each Needs-follow-up row has a solid blue "Mark as Conducted" → 7 solid buttons in a column, all louder than New activity. |
| **Activity page (all tabs)** | Header card + tabs pill bar + tab cards. | Title shown twice (top bar and card). Active tab = solid blue pill, as loud as the primary button. The amber Needs attention box is the biggest element above the fold; at 1366×768 the tab content starts at ~500 px. Edit (primary) sits next to Change status / Copy link with the same weight in a pill row. |
| Overview tab | Details card has a large empty area (right column is taller). | "Add resource person" is a solid primary in a sub-form: competes with Edit. |
| Forms tab | Two form cards side by side. | `_form_card.html`: 2 primary + 1 danger + 3 secondary + 4 ghost in one card ("Open registration", "Add field", "Preview", "Remove"…). Main step ("Open registration") not distinguishable. |
| Registrations tab | KPI row then list. | "Print attendance sheet" and "Evaluation links" equal; row "Mark attended" + bulk buttons: 2 primary. |
| Evaluation tab | KPI row then links card. | "Resend to all who haven't answered" is a solid primary pill at the top right of a card; fine, but it is the only one and looks disabled-blue next to the KPI cards. |
| Documents tab | Two columns. | Upload form submit (primary) and each row's Approve (primary) + Return (amber). |
| Certificates tab | `_tab_certificates.html`: 3 primary, 7 secondary, 11 ghost. | "Approve certificates for this activity" is right, but "Apply the current design", "Use the current version", "Save" ×2, "Save the certificate text", Previous/Next, Download PDF, "Replace signature", "Add a person" have no clear grouping. |
| **New activity form** | `max-w-4xl`, `space-y-5`; field gaps 16–24 px mixed. | OK: one primary (Create) at the bottom; but no sticky/visible primary while scrolling a long form. Subtitle says "starts as Draft" (no longer true since item 409: **label to fix**, see 2.7). |
| **Documents page** | Card in a card (page card → activity cards → rows); column positions differ between activity cards (the Title column jumps by ~50 px). | Page title twice ("Documents" in the top bar and as the card title). Every "Waiting for review" row has solid blue **Approve** + amber **Return**: on the first screen 4 primary + 4 amber + 11 "Upload". The view switch and the "Waiting for my review (11)" tab are small. |
| **Certificates page** | `p-6` card, groups with 16/20 px gaps. | "Signatories" link at the top right is the page's way to an important screen but looks like any link. |
| **Reports** | `space-y-6` (24 px) vs 20 px elsewhere. | "Generate" OK; the prompt's send arrow is a light-blue disabled-looking circle; history Download buttons are secondary (OK). |
| **AI Assistant** | Fits the window. | "New chat" solid primary + "Send" primary + "Ask AI" primary in the top bar on the same screen: 3 primary. |
| **Admin Console** | Tabs pill bar (solid blue active), cards 20 px apart. | Members: "Invite" primary (OK) but each row has Save + Reset password + Deactivate (red text): Deactivate is cut off at 1366 px. Roles: the same kind of action is "Turn off" (ghost text) and "Turn on" (outlined pill): two styles for one action. |
| **Sign-in** | Fits 1366×768. | Fine: one primary. Gradient and glass card are a deliberate landing design (items 255–257). |
| **Public registration / evaluation** | Glass card on the gradient, 44 px targets. | One primary each (OK). The glass card on the bright gradient is the "too bright" part on phones. (No activity is open for registration in the demo data today, so only the evaluation page was captured.) |

---

## 2. Proposed design system

One place: the `@theme` block and the component classes of `frontend/tailwind/app.css` (Tailwind v4 utilities read
these variables, so changing them updates every page that uses `bg-primary`, `text-muted`, `border-line`, …).

### 2.1 Colours (all measured, WCAG AA)

| Token | Value | Use | Contrast |
|---|---|---|---|
| `--color-sidebar` | **#22378F** (unchanged brand navy) | Solid sidebar (no gradient behind it) | white 10.4:1 |
| brand accent | gradient #22378F → #60CDEE | **Only a 3 px line** at the top of the content area and of the dashboard welcome card; the sign-in page keeps its full gradient (landing page) | decoration |
| `--color-primary` | **#2B4BA8** (was #1D4ED8) | The ONE primary button per view, links, focus ring, active tab underline | white on it 7.9:1; as text 7.9:1 (7.3:1 on the page) |
| `--color-primary-hover` | #223C8A | | 10.1:1 |
| `--color-primary-soft` | #EEF2FB | Selected states, type tag, tertiary hover | primary text on it 7.0:1 |
| `--color-page` | #F5F7FA | Page background (flat, no floating panel) | |
| card | #FFFFFF, border `--color-line` #E4E7EC, shadow `0 1px 2px rgb(16 24 40/.05)` | All cards | |
| `--color-line-strong` | #7E8899 (final; the prototype used #8D96A7) | Input / select borders | 3.6:1 on white, 3.3:1 on the page (fixes 1.5:1) |
| `--color-ink` / `--color-muted` / `--color-helper` | #101828 / #475467 / #5D6676 | Body / secondary / helper text | 17.8 / 7.7 / 5.8:1 |
| `--color-kpi` | = ink | KPI numbers (colour stays in the small icon chip) | 17.8:1 |
| `--color-danger` | #B42318 (soft #FEF3F2) | Destructive only | white on it 6.6:1; on soft 6.1:1 |
| `--color-warning` | #93370D (soft #FFFAEB) | Warnings, amber notes | 7.2:1 |

**Softer status badges** (tint + 1 px border, text 5.2:1 or more): Draft/neutral #344054 on #F2F4F7 · Scheduled
#175CD3 on #EFF8FF · Ongoing/warning #B54708 on #FFFAEB · Conducted/success #067647 on #ECFDF3 · danger #B42318 on
#FEF3F2 · info #1E40AF on #EFF4FF. Sidebar count badge: white 18 % instead of amber-400. Chart colours unchanged
(they have text tables).

### 2.2 Spacing scale and rules

Scale: **4 · 8 · 12 · 16 · 24 · 32 px** (Tailwind 1, 2, 3, 4, 6, 8). No half steps (`*-1.5`, `*-2.5`, `*-3.5`) and no
5/7 in layout spacing (they may stay inside small components: badge padding, icon gaps).

| Rule | Value |
|---|---|
| Page padding | 32 px from 1280 px, 24 px below, 16 px on phones |
| Card padding | 24 px (compact cards: KPI, list rows: 16 px) |
| Gap between cards / grid columns | 24 px |
| Section spacing (page header → content, between page sections) | 24 px header → content, 32 px between sections |
| Inside a card: header → content | 16 px |
| Between form fields | 16 px; label → input 8 px; field → help/error 4 px |
| Between buttons in a group | 8 px (12 px + divider before a destructive one) |

### 2.3 Button hierarchy (same height and radius everywhere)

| Level | Class | Look | When |
|---|---|---|---|
| Primary | `btn btn-primary` | solid #2B4BA8, white text | **one per page view** (the page's main action, top right) or the submit of a form the page is about (New activity, a confirm dialog) |
| Secondary | `btn btn-secondary` | white, 1 px #D0D5DD border, ink text | other real actions; the main action of a list row (Approve, Upload, Mark attendance) |
| Tertiary | `btn btn-ghost` (alias `btn-tertiary`) | text only, primary colour, soft tint on hover | minor actions: View, Open, Download, Preview, Return, Previous/Next, "Show more"; icon-only with `aria-label` in dense tables |
| Destructive | `btn btn-danger` | white with red text and red border; **solid red only inside the confirm dialog** | Delete, Deactivate, Remove; never directly next to the primary: 12 px + a divider, or in the "More" (⋯) menu |

Sizes: default 40 px (16 px side padding, 14 px semibold), `btn-sm` 32 px (12 px, 13 px). Radius 8 px everywhere
(pill shape dropped in the app; public pages keep 44 px minimum height on phones). No lift/shadow on hover (colour
change only). `btn-warning` (amber fill) is retired: its three uses become Secondary/Tertiary. Ask AI in the top bar
becomes Secondary.

### 2.4 Page header pattern (new `components/page_header.html`)

```
[breadcrumb (optional): Activities /]
Page title (h1)  [status badge]                     [secondary] [secondary] [⋯ More] [PRIMARY]
One-line description (muted)
```

The top bar keeps search, Ask AI and the bell (and the menu button below 1024 px); the **page title moves out of the
top bar into the page header** (no more duplicated title). The primary action is at the far right; secondary ones to
its left; rarely used and destructive ones in "⋯ More". Wraps under the title at narrow widths (390 px: actions full
width below the title).

### 2.5 Typography

| Role | Size / line height | Weight | Colour |
|---|---|---|---|
| Page title (h1) | 24 / 32 px | 600 | ink |
| Page description | 14 / 20 | 400 | muted |
| Section title (h2 between card groups) | 18 / 28 | 600 | ink |
| Card title | 16 / 24 | 600 | ink |
| Body | 14 / 20 | 400 | ink |
| Label | 14 / 20 | 500 | ink |
| Helper / meta / table header | 13 / 18 (table header 12, uppercase, 0.04em) | 400 (header 600) | muted / helper |
| KPI number | 24 / 28 | 600 | ink |

### 2.6 Other shared parts

- **Tabs** (activity page, Admin Console): underline tabs (muted text, active = primary text + 2 px underline), not a
  solid blue pill.
- **Segmented controls** (List/Calendar, By activity/All documents, Repository/Waiting for my review): selected option
  = white with a hairline shadow, not solid blue.
- **Notice boxes** (Needs attention, registration notice): white card with a 3 px amber (or red/blue) left border and
  an icon; links in the primary colour. Same information.
- **Cards**: 12 px corners, hairline border, almost no shadow; no card inside a card (the Documents page wrapper goes).
- **Tables**: 12 px header uppercase muted, 16 px cell padding, one column template per table type (Documents page
  columns line up between activity cards).
- **Forms**: 8 px radius, 40 px height, the stronger border; focus ring stays (2 px primary, 2 px offset).
- `:focus-visible` rings, skip link, `aria-*` labels: unchanged (the ring colour follows `--color-primary`).

### 2.7 Label changes (only where unclear)

| Where | Now | Proposed | Why |
|---|---|---|---|
| New activity, subtitle | "A new activity starts as Draft. Every field except the description is required." | "A new activity is created as Scheduled, with its Activity Design and Request Letter. Every field except the description is required." | No longer true since item 409. |

No other text changes are planned; Phase 3 will list any further ones.

---

## 3. Before / after: three pages

### Dashboard (`before-dashboard.png` → `after-dashboard.png`)

| Button | Before | After |
|---|---|---|
| New activity | glass outline inside the banner | **Primary**, page header top right |
| Ask AI (top bar) | primary | Secondary |
| Review items (banner) | white solid | Tertiary (text link with arrow) |
| Mark attendance / Open activity (spotlight) | white solid on dark gradient | Secondary |
| Needs attention: Upload ×4 (overdue) | solid red | Secondary (the red icon chip keeps the urgency) |
| Needs attention: Upload (due soon) | amber | Secondary |
| Needs attention: Review | solid blue | Secondary |
| View all / Activity history / Show N more | link | Tertiary (unchanged look) |

Banner and spotlight become white cards (banner keeps the brand gradient as a 3 px top line); KPI numbers in ink.

### Activity page, Overview (`before-activity-overview.png` → `after-activity-overview.png`; Ongoing activity)

| Button | Before | After |
|---|---|---|
| Edit | primary (in a pill row) | **Primary**, far right of the page header |
| Change status | secondary | Secondary |
| Copy registration link (when open) | secondary | Secondary |
| ⋯ More → Delete activity… | ghost menu | Tertiary menu, Delete inside (destructive, unchanged) |
| All activities | link above the title | Breadcrumb "Activities /" |
| Tabs | solid blue pill for the active tab | Underline tabs |
| Add resource person | primary | Secondary |
| Needs attention links | amber box, underlined amber links | White notice with amber left border, primary links |

The activity title is the page h1 (no second title in the top bar).

### Documents page (`before-documents.png` → `after-documents.png`)

| Button | Before | After |
|---|---|---|
| Repository / Waiting for my review (11) | small pill tabs inside the card | Segmented control in the page header (right); no page primary: this is a list page |
| Approve (each waiting row) | primary | Secondary (the row's main action) |
| Return | amber | Tertiary |
| Download / Preview (row icons) | secondary icon buttons | Tertiary icon buttons (`aria-label` / tooltip kept) |
| Replace | secondary | Secondary |
| Delete (own upload) | red ghost | Tertiary destructive, 12 px apart from the others |
| Upload (missing rows) | secondary | Secondary |
| Open the activity's Documents tab | link | Tertiary (unchanged look) |

The page wrapper card goes (no card in a card); the duplicated "Documents" title goes. Phase 3 also gives every
activity card the same column template so the columns line up.

---

## 4. What Phases 2 and 3 change (for the plan)

**Phase 2 (global, shared files only):** `frontend/tailwind/app.css` (tokens, `.btn*`, `.card`, `.kpi-card`, `.badge*`,
`.tag*`, `.field-*`, `.chip*`, `.modal`, shell classes `.app-bg` / `.app-panel` / `.app-shell .btn` / `.sidebar-glass`,
`.welcome-banner`, `.spotlight`, `.date-tile-gradient`, `.public-*`), `layouts/app.html` (flat panel + accent line),
`partials/topbar.html` (title out, Ask AI secondary), `partials/sidebar.html` / `nav_item.html`, new
`components/page_header.html`, `components/kpi_card.html`, `section_header.html`, `status_badge.html`, `empty_state.html`,
`form_field.html`, `activities/_tab.html` + console tabs (underline tabs), `activities/_attention.html` (notice style).
Rebuild CSS with the Tailwind CLI into a temp folder, copy to `static/css/app.css`.

**Phase 3 (page by page):** page header + button levels on every page in section 1.4, spacing classes moved onto the
scale, Documents page column template, Admin Console Members row (Deactivate no longer cut off) and Roles switches (one
style), public and portal pages checked at 390 px. Browser tests extended: no two buttons overlap and no button leaves
its container at 1366, 1280, 1024 and 390 px on the audited pages.

**Tests likely to need updates:** tests asserting `btn-primary` / `btn-warning` / `btn-danger` on specific buttons, the
top bar `<h1>` text, tab classes (`bg-primary text-white`), `app-bg` / `.auth-bg` tokens in the CSS test, and the
documents/signatories browser tests' selectors.

---

## 5. Phase 2 result (2026-11-20, owner decisions 1–4 accepted)

`phase2-*.png`: Dashboard, Activity Overview, Documents and sign-in after the global change (real app, no prototype
script). Applied: the tokens and component classes in `frontend/tailwind/app.css`, the flat shell with the navy sidebar
and the 3 px brand line, the page header in `layouts/app.html` (Ask AI secondary, title out of the top bar), underline
tabs, segmented controls, notices, softer badges, 40 px controls, white dashboard cards, the toned-down sign-in and
public pages, the New activity subtitle, and the four bugs (input border 3.6:1, Members row, Roles switches, Documents
columns: a fixed 21rem Actions column). Also found and fixed by the new browser test: the Roles table hid its last
"Turn on" past the card edge at 1366 px (headers now wrap); the Activities list (date range, follow-up buttons) and the
activity Overview (resource-person switch) scrolled sideways at 390 px.

Left for Phase 3 (per page): the button levels of section 3 (Dashboard Needs attention rows and "New activity" in the
page header; Documents row Approve / Return / icons; Overview "Add resource person"), page descriptions (`page_subtitle`)
and `page_actions` on the other pages, the remaining duplicated card titles, `btn-warning` removals, and moving the
other pages' spacing classes onto the scale.

---

## 6. Revised Phase 2 (current design, 2026-11-20)

Owner feedback on section 5: "changed the design too much". Restored: the gradient frame and floating rounded panel, the
frosted glass sign-in / public cards, pill buttons, pill tabs, pill segmented controls, the original top bar style, the
sign-in page as it was. Kept: calmer tokens (primary #2B4BA8, softer badges, KPI numbers in ink, calmer dashboard
blocks), the spacing scale, the button hierarchy (with pills), the page header, the bug fixes.

**Calmer gradient:** `#22378F` (brand navy, unchanged) → `#5E86C0` (muted steel blue, saturation 44% instead of 81% for
`#60CDEE`), same 135° direction and stops (app frame, sign-in, public pages). Dashboard banner `#2B4BA8` → `#6F95CB`;
spotlight and date tiles `#22378F` → `#2B4BA8`. On the glass card, muted text keeps 4.9:1 or more anywhere on the gradient.

Also fixed: every page has its own one-line description (the Dashboard's "Here's what's happening at CSU-TBI" was shown
on every page without one); cards that repeated the page title lost their visible heading; one KPI card structure
(number + badge on one baseline, the "Certificate approval" note no longer moves the number); the welcome card no longer
has a large empty area; Documents rows use the agreed levels (Approve secondary, Return tertiary, Download / Preview
tertiary icons), so they fit on one line.

Screenshots (1366 px): `revised-dashboard.png`, `revised-activity-overview.png`, `revised-documents.png`,
`revised-reports.png`, `revised-signin.png`.

---

## 7. Calmer and balanced (current, 2026-11-20)

Owner feedback on section 6: still not calm enough, too much empty space.

- **Gradient frame** darker, less saturated, less contrast between the ends: `#1F2F6E` -> `#41598C` (was `#22378F` ->
  `#5E86C0`); the frosted sign-in / public card is white 80% so its text keeps 5.2:1 or more.
- **Only the page's primary button is solid primary.** The welcome and "Happening now" cards are white cards (thin brand
  line on the welcome card, green dot / tinted icon on the spotlight, secondary buttons, smaller illustration); current
  tabs, count badges, date tiles, the calendar's today, chat bubbles, checked pills and file buttons are soft or outlined.
- **Balanced layouts.** Dashboard: Needs attention | Recent changes (latest 5, two lines each, "View all") in one row of
  equal height, then Upcoming activities + the three charts in a 2 x 2 grid. Activity Overview: Details + Resource persons
  on the left, Registration + Share + Participants on the right.

Screenshots (1366 px): `final-dashboard-top.png`, `final-dashboard-scrolled.png`, `final-activity-overview.png`,
`final-documents.png`, `final-certificates.png`.

---

## 8. Colour palette (current, 2026-11-20, ASSUMPTIONS 412)

Correction after the UAT (defense Dec 9): the owner's four colours replace the blue, with the same layout, pill shapes,
frosted cards, spacing scale and button hierarchy. Visual only: no business rule, URL, permission or database change.

| Palette colour | Value | Used for |
|---|---|---|
| Teal Waters | `#204654` | brand: gradient frame, sidebar, the solid primary button (white text), page and card headings, focus ring |
| Glacial Sky | `#A9E0F1` | light tints only: current tab / pill, selected states, info badge, icon backgrounds (lighter mixes) |
| Morning Mist | `#F7F9E1` | page background; the content panel behind the white cards is the lighter mix `#FAFBED` (owner's choice) |
| Spring Meadow | `#CAFFA6` | small accents only: the sidebar count badge, the "happening now" dot (with a dark green ring), the Approved tint (a 30% mix) and the "Approved" chip of the sign-in illustration. Never a large background, a button or a gradient end. |

Everything else is derived from them in ONE place, the `@theme` block of `frontend/tailwind/app.css`.

### 8.1 Tokens (old → new)

| Token / place | Old | New |
|---|---|---|
| Gradient frame (app shell, sign-in, public pages; same angles and stops) | `#1F2F6E → #41598C` | `#204654 → #2F6170` |
| `sidebar` / `sidebar-hover` / `sidebar-line` | `#22378F` / `#2B44A6` / `#3A4FA3` | `#204654` / `#2A5866` / `#3A6A78` |
| `sidebar-muted` | `#C7D2FE` | `#A9E0F1` (Glacial Sky) |
| `brand-calm` (light end of the gradient) | `#41598C` | `#2F6170` |
| `primary` / `primary-hover` / `primary-pressed` (new) | `#2B4BA8` / `#223C8A` / – | `#204654` / `#18363F` / `#122A31` |
| `primary-soft` (current tab, selected pill, icon chips) | `#E6ECF8` | `#E5F5FB` (Glacial Sky 30%) |
| `primary-tint` (new: chip on a tinted row) | – | `#D4F0F8` (Glacial Sky 50%) |
| `link` (new: text links) | = primary | `#1B6580` |
| `heading` (new: page and card titles) | = ink | `#204654` |
| `page` / `panel` | `#F4F6FB` / `#F7F9FC` | `#F7F9E1` / `#FAFBED` |
| `ink` / `kpi` / `muted` / `helper` | `#0F172A` / `#0F172A` / `#475569` / `#5D6676` | `#1A2E35` / `#1A2E35` / `#4A5E65` / `#56696F` |
| `line` / `control-line` / `line-strong` (input border) | `#E6EAF2` / `#CBD5E1` / `#7E8899` | `#DCE5E4` / `#C3D1D3` / `#738890` |
| `focus` (new: the 2 px focus ring) | = primary | `#204654` (white on the dark frame) |
| `info` / `info-soft` / `info-line` (new) | Tailwind blue classes | `#1D5A73` / `#EAF7FC` / `#A9E0F1` |
| `success` / `success-soft` / `success-line` (new) | `#067647` / `#ECFDF3` / `#ABEFC6` | `#2E6A12` / `#F1FFE6` / `#B7E894` |
| `warning` / `warning-soft` | `#93370D` / `#FFFAEB` | `#8A5300` / `#FFF8E8` |
| `danger` (+ hover, soft, line) | `#B42318` | unchanged |
| `accent` (decoration) | `#14B8A6` | `#A9E0F1` |
| Neutrals `slate-50 … 950` (300+ uses in the templates) | Tailwind slate | the same lightness tinted toward Teal Waters (e.g. slate-500 `#64748B` → `#62767D`): every contrast within ±0.2 of before |
| Card shadow | `rgb(34 55 143 / .08)` | `rgb(32 70 84 / .07)` |
| Frosted sign-in / public card | white 80% | white 88% |
| Inputs on the frosted card | border white 50% | `#738890` |
| Welcome card top line | `#22378F → #5E86C0` | `#204654 → #A9E0F1` |
| Status chart (Draft, Scheduled, Ongoing, Conducted) / progress bar | `#CBD5E1 #60A5FA #FBBF24 #34D399` / `#F59E0B` | `#C3D1D3 #5BA7C2 #E2B65A #4F8F2E` / `#2F6170` |
| Category donut (`reports.dashboard_page.CATEGORY_COLORS`) | `#1D4ED8 #14B8A6 #93C5FD #F59E0B #7C3AED #64748B #EC4899 #84CC16` | `#204654 #5BA7C2 #4F8F2E #E2B65A #7B6FA6 #8A9AA0 #C47A6A #A9E0F1` |
| Report PDF bars / headings and table header / rating dots | `#2a78d6` / `#1f3a5f` / `#1D4ED8` | `#204654` (all three) |
| Excel table header | `#1D4ED8` | `#204654` |
| Logo tile (`logo-96`, `favicon-32`, `apple-touch-icon-180`) | navy `#22378F` | Teal Waters, same artwork from `docs/design/logo.png` (old files kept as `docs/design/*-navy-old.png`) |

**Badges** (tint + 1 px border of the same hue): Missing / Draft grey `#3D4F55` on `#F1F4F4` (owner: Missing stays grey,
so it differs from Overdue by colour); Overdue red `#B42318` on `#FEF3F2` + its icon; Waiting for review / Scheduled
Glacial blue `#1D5A73` on `#EAF7FC`; Returned / Ongoing amber `#8A5300` on `#FFF8E8`; Approved / Conducted dark green
`#2E6A12` on the Spring Meadow tint `#F1FFE6`.

**Chart colours** were checked with simulated red-green colour blindness: with the first choice the ochre Ongoing and
the green Conducted were too close for protanopia (ΔE 8, side by side in the status bar), so they were separated by
lightness; now the closest pair is ΔE 14 for normal, deutan and protan vision. Every chart also has its text table.

### 8.2 Contrast (WCAG 2, every pair measured; `accounts/tests/test_staff_layout.py` recomputes the main ones from the tokens)

| Pair | Ratio | Needed |
|---|---|---|
| White text on Teal Waters (sidebar, gradient dark end, primary button) | **10.2:1** | 4.5:1 |
| White text on the gradient's light end #2F6170 | **6.9:1** | 4.5:1 |
| White text on primary hover #18363F (pressed #122A31: higher) | **12.8:1** | 4.5:1 |
| Glacial Sky label ("WORKSPACE", role) on the sidebar | **7.1:1** | 4.5:1 |
| Glacial Sky headline ("All in one place.") on the light end | **4.8:1** | 4.5:1 |
| Teal Waters count on the Spring Meadow badge | **8.9:1** | 4.5:1 |
| Ink #1A2E35 on white cards | **14.1:1** | 4.5:1 |
| Ink on the panel #FAFBED | **13.5:1** | 4.5:1 |
| Muted #4A5E65 on white | **6.8:1** | 4.5:1 |
| Muted on the panel | **6.5:1** | 4.5:1 |
| Muted on Morning Mist | **6.4:1** | 4.5:1 |
| Helper #56696F on white | **5.8:1** | 4.5:1 |
| Helper on the panel | **5.5:1** | 4.5:1 |
| Headings / primary text #204654 on white | **10.2:1** | 4.5:1 |
| Headings / primary text on the panel | **9.7:1** | 4.5:1 |
| Primary text on primary-soft #E5F5FB (current tab, selected pill) | **9.1:1** | 4.5:1 |
| Link #1B6580 on white | **6.5:1** | 4.5:1 |
| Link on the panel | **6.2:1** | 4.5:1 |
| Link on primary-soft | **5.8:1** | 4.5:1 |
| Waiting for review / Scheduled #1D5A73 on #EAF7FC | **7.0:1** | 4.5:1 |
| Returned / Ongoing #8A5300 on #FFF8E8 | **6.0:1** | 4.5:1 |
| Approved / Conducted #2E6A12 on #F1FFE6 (Spring Meadow tint) | **6.3:1** | 4.5:1 |
| Overdue / danger #B42318 on #FEF3F2 | **6.0:1** | 4.5:1 |
| Missing / Draft #3D4F55 on #F1F4F4 (grey) | **7.8:1** | 4.5:1 |
| "Happening now" label #2E6A12 on white | **6.6:1** | 4.5:1 |
| White on danger #B42318 (confirm dialogs) | **6.6:1** | 4.5:1 |
| White on success #2E6A12 | **6.6:1** | 4.5:1 |
| Ink on the frosted card (white 88% over #204654) | **11.5:1** | 4.5:1 |
| Muted on the frosted card, darkest spot | **5.6:1** | 4.5:1 |
| Muted on the frosted card, lightest spot | **5.7:1** | 4.5:1 |
| Helper on the frosted card, darkest spot | **4.7:1** | 4.5:1 |
| Link on the frosted card | **5.3:1** | 4.5:1 |
| Error text #991B1B on the frosted card | **6.8:1** | 4.5:1 |
| Input border #738890 on white | **3.7:1** | 3:1 |
| Input border on the panel | **3.6:1** | 3:1 |
| Input border on Morning Mist | **3.5:1** | 3:1 |
| Input border on the frosted card | **3.0:1** | 3:1 |
| Focus ring #204654 on white | **10.2:1** | 3:1 |
| Focus ring on the panel | **9.7:1** | 3:1 |
| Focus ring (white) on the sidebar | **10.2:1** | 3:1 |
| Excel header: white bold on #204654 | **10.2:1** | 4.5:1 |

### 8.3 Found and fixed while doing it

- **Links looked like body text**: Teal Waters (`#204654`) is close to the ink (`#1A2E35`), so text links (Needs
  attention, AI answers, privacy / contact links) get their own colour `#1B6580`; buttons and headings keep Teal Waters.
- **Logo**: the 96 px logo, favicon and touch icon were the artwork on a navy square, a blue patch on the teal sidebar.
  Re-exported from the transparent `docs/design/logo.png` onto a Teal Waters tile, same size and position (not redrawn).
- **Square at the panel's top-left corner (activity page)**: the resource-person pick list was a vertical scroll box
  with its own rounded corners inside the scrolling content panel; Chromium then drew a small square over the panel's
  rounded corner. The rounded border is now on a wrapper and the list scrolls inside it with square corners
  (`activities/_resource_person_options.html`, same look); a test scans the templates for the pattern.
- The report PDFs print Teal Waters as a solid dark grey in black and white (bars, the table header with white text,
  headings), clearly apart from the light grey baseline and the light grey "off" rating dots.

Screenshots (1366 px, the real build): `palette-dashboard-top.png`, `palette-dashboard-scrolled.png`,
`palette-activity-overview.png`, `palette-documents.png`, `palette-reports.png`, `palette-signin.png`.

---

## 9. Back to the original design, calmer (current, 2026-11-21, ASSUMPTIONS 413)

Owner: "We don't like the new design" (section 8, teal / cream). Back to the original identity, calmer, with spacing and
hierarchy fixed. Part 1 (before the defense): colours, type scale, button levels, one primary per page, Needs attention.
Part 2 (next): the spacing sweep across every template.

### 9.1 Colours (option B of `../options/README.md`)

| Token | Value | Measured |
|---|---|---|
| Frame gradient | `#1E2F73` → `#3B8AA6` (135°, dark to 30%) | white 12.3:1 on the dark part; 3.9:1 at the light end (behind the panel, no text) |
| Primary (solid button, focus ring) | `#2A4494` (hover `#243A7E`, pressed `#1E316B`) | white on it 8.9 / 10.6 / 12.3:1; as text 8.2:1 on the panel |
| Primary soft (current tab, chips) | `#EAECF4` | primary text on it 7.6:1 |
| Link | `#2C5AA8` | 6.7:1 on white, 6.2:1 on the panel, 5.7:1 on primary soft |
| Light text on the gradient | `#D3E8F0` (headline accent `#9AD4E4`) | 9.7:1 on the dark part, 7.0:1 on the primary (accent 7.6:1, large text) |
| Panel / page | `#F4F6F9` / `#EEF1F5`, white cards | ink `#1E293B` 14.6:1 on white; muted `#475569` 7.0:1 on the panel |
| Input border | `#7B8798` | 3.7:1 on white, 3.4:1 on the panel, 3.2:1 on the page |
| Accent bars | red `#B42318`, amber `#B87612`, blue `#2B59A8` | 6.6 / 3.7 / 6.8:1 on white; amber 3.5:1 on its tint |
| Status badges | see `../options/README.md` | 5.4:1 or more, five states apart by hue |
| Charts | status `#CBD5E1 #5B84C4 #E0A940 #2F7A50`; categories `#2A4494 #5AAFC8 #2F7A50 #EDC06A #8C6BB1 #6B7686 #D98B7A #C7DCF2` | smallest dE 15 under simulated protanopia, deuteranopia, tritanopia |
| Report PDF / Excel header, logo tile | `#2A4494` / `#1E2F73` | white bold on the header 8.9:1 |

### 9.2 Type scale

| Level | Size / weight | Class |
|---|---|---|
| Page title (one per page) | 24 px bold | `.page-title` (h1) |
| Section / card heading | 18 px semibold | `.card-title` = `.section-title` |
| Body | 14 px regular | `text-sm` |
| Caption, labels | 12 px medium | `.caption` / `text-xs` |

Moved onto the scale: `text-[15px]` (27 uses; staff → 14 px, public / portal → 16 px), `text-[13px]` (KPI label, sidebar
public tagline, `btn-sm`), `text-[11px]` (date tile month), 20 px dialog / preview headings → 18 px. Display text that
stays larger: KPI and donut numbers, the sign-in and public hero headlines.

### 9.3 Button levels

| Level | Class | Use |
|---|---|---|
| Primary | `btn-primary` (solid) | ONE per page or card: the main action. Page header top right, bottom of a form, or a tab card's header |
| Secondary | `btn-secondary` (outline) | Other real actions; a list row's main action |
| Tertiary | `btn-ghost` (text) | Minor actions: Open, View, Download, Preview, Previous / Next |
| Destructive | `btn-danger` (**red text**, no fill) | Delete, Return, Remove, Deactivate; solid red only to confirm (inside `.modal` or `btn-danger-solid`) |
| On a gradient block | `btn-on-dark` (white outline) | Dashboard banner and "Next activity" card |

Changed in Part 1: activity page "Edit" → secondary (each tab's main action is the primary); Activities list follow-up
"Mark as Conducted" → secondary; Registrations row "Mark attended" → secondary (bulk "Mark selected as attended" is the
tab's primary); certificate preview "Reissue with current signatures" → secondary (the card's primary is the approval);
Members "Invite" hides while its form is open; Return (document rows) and Deactivate (Signatories, Positions, Certificate
design, Members) → red text; the Return / Delete confirmations → solid red.

### 9.4 Every page and its primary action (checked in Chromium at 1366 × 768: visible solid buttons counted)

| Group | Page | Primary action | Where | Check |
|---|---|---|---|---|
| Staff | Dashboard | New activity | page header, top right | ✓ (+ the Needs attention card's own one: the first "Review" / "Approve") |
| Staff | Activities (list / calendar) | New activity | page header, top right | ✓ (follow-up rows were 6 more solid buttons: fixed) |
| Staff | New activity | Create activity | bottom of the form | ✓ |
| Staff | Edit activity | Save changes | bottom of the form | ✓ |
| Staff | Activity: Overview, History, Forms | — (Edit and Change status are secondary in the header) | — | ✓ no main action on these tabs; Forms: "Create from defaults" / "Open registration" when they apply, one per form card |
| Staff | Activity: Registrations | Mark selected as attended | the list card's header | ✓ (row "Mark attended" was solid: fixed) |
| Staff | Activity: Evaluation | Resend to all who haven't answered | the links card's header | ✓ |
| Staff | Activity: Documents | Upload | bottom of the upload form | ✓ (Edit was a second solid button: fixed) |
| Staff | Activity: Certificates | Approve certificates for this activity | the approval card, right | ✓ (Reissue in the preview was solid: fixed) |
| Staff | Documents (by activity / review queue) | — (row actions: Approve secondary, Return red text) | — | ✓ |
| Staff | Document page | — (Approve secondary, Return red text) | — | ✓ |
| Staff | Certificates | — (view only; Signatories secondary in the header) | — | ✓ |
| Staff | Signatories | Save defaults (defaults card), Add (add-a-person card) | bottom of each card's form | ✓ one per card; Deactivate red text |
| Staff | Certificate text | Save the default text | bottom of each certificate type's card | ✓ one per card |
| Staff | Reports | Generate report (the prompt's send button / "Generate report" in More settings) | the request card | ✓ |
| Staff | Startups, startup page, Notifications | — | — | ✓ |
| Staff | AI Assistant | Send | the message box | ✓ (Rename "Save" is in its own dialog) |
| Staff | Search results | Search | the search form | ✓ |
| Staff | My profile | Save profile | bottom of the profile form | ✓ ("Change password" in its own card is secondary) |
| Admin Console | Members | Invite (then Send invitation) | the invite card | ✓ (both were visible together: fixed); Deactivate red text |
| Admin Console | Roles | — (Turn on / Turn off secondary) | — | ✓ |
| Admin Console | Positions | Add position | the add form | ✓; Deactivate red text |
| Admin Console | Certificate design | Upload design | the upload card | ✓; Deactivate red text |
| Admin Console | Deleted activities, System status | — | — | ✓ |
| Admin Console | Delete activity | Delete activity (solid red, confirm page) | bottom of the form | ✓ the only destructive solid button, a confirmation |
| Portal | Overview, Open activities, My registrations, My certificates, Notifications, Profile | — (Register / Download are secondary row actions) | — | ✓ |
| Public | Sign in (staff, startup portal) | Sign in | bottom of the card | ✓ |
| Public | Forgot password | Email me a link | bottom of the card | ✓ |
| Public + Portal | Registration, evaluation, set password forms | the form's submit (Register, Review my answers, Submit, Set password) | bottom of the form | ✓ |
| Public | Open for registration, Privacy notice | — | — | ✓ |
| Public | Evaluation request (closed) | See open activities | the notice card | ✓ |

### 9.5 Needs attention and pending approvals

A 4 px accent bar along the left edge (an inset shadow, so it follows the rounded corners): red when something is
overdue, amber when something is due soon or waits for the member, blue when only approvals wait.

- **Dashboard:** Needs attention comes right after the filters, beside Recent changes, before the KPI cards (its items
  follow the activity-type filter). It has a count badge and rows tinted by urgency, and the first "Review" / "Approve"
  is the card's one solid button. "Certificate approval" and "Documents to review" KPI cards get the amber bar while
  something waits.
- **Activity page:** the Needs attention notice gets the bar and its count as a badge.

Screenshots (1366 × 768, demo data, Demo Director): `b-dashboard.png`, `b-dashboard-attention.png`,
`b-activity-overview.png`, `b-activity-certificates.png`, `b-activity-documents.png`, `b-activities.png`,
`b-documents.png`, `b-signin.png`, `b-public-phone.png` (390 × 844); also `../dashboard.png` and `../login.png`.
