---
name: Expensive Mini Savings
description: The EMS page in Home Assistant, a battery planner that shows every decision with its numbers.
colors:
  ha-primary: "#03a9f4"
  ems-accent: "color-mix(in srgb, var(--primary-color, #03a9f4) 70%, black)"
  ha-card-background: "#ffffff"
  ha-divider: "#e0e0e0"
  ha-warning: "#ffa600"
  ha-error: "#db4437"
  stone-grey: "#8d9499"
  hold-blue: "#488fc2"
  grid-charge-pink: "#e0457b"
  paused-blue: "#6aa6ef"
  forced-discharge-violet: "#8e44ad"
  provider-red: "#b33b2e"
  battery-value-teal: "#00897b"
  soc-teal: "#26a69a"
  solar-orange: "#ff9800"
  load-slate: "#607d8b"
  pv-boost-violet: "#8353d1"
  earned-green: "#0f9d58"
  forecast-grey: "#a8adb3"
  price-cheap: "rgb(46,157,74)"
  price-middle: "rgb(242,183,5)"
  price-expensive: "rgb(217,68,47)"
typography:
  display:
    fontFamily: "inherit (Home Assistant theme font, Roboto, sans-serif)"
    fontSize: "3rem"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.02em"
    fontFeature: "tnum"
  headline:
    fontFamily: "inherit"
    fontSize: "1.9rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  metric:
    fontFamily: "inherit"
    fontSize: "2.6rem"
    fontWeight: 600
    lineHeight: 1.1
    fontFeature: "tnum"
  title:
    fontFamily: "inherit"
    fontSize: "1.3rem"
    fontWeight: 600
    letterSpacing: "-0.005em"
  body:
    fontFamily: "inherit"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "inherit"
    fontSize: "0.9rem"
    fontWeight: 400
    lineHeight: 1.45
rounded:
  bar: "6px"
  notice: "8px"
  option: "10px"
  card: "12px"
  badge: "18px"
  full: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  xxl: "28px"
components:
  card:
    backgroundColor: "{colors.ha-card-background}"
    rounded: "{rounded.card}"
    padding: "20px"
  button-primary:
    backgroundColor: "{colors.ems-accent}"
    textColor: "#ffffff"
    rounded: "{rounded.full}"
    padding: "0 20px"
    height: "44px"
  button-secondary:
    backgroundColor: "transparent"
    rounded: "{rounded.full}"
    padding: "0 20px"
    height: "44px"
  action-pill:
    rounded: "{rounded.full}"
    padding: "2px 10px 2px 8px"
    typography: "{typography.label}"
  mode-option:
    backgroundColor: "transparent"
    rounded: "{rounded.option}"
    padding: "10px 12px"
    height: "44px"
  notice:
    rounded: "{rounded.notice}"
    padding: "10px 12px"
    typography: "{typography.label}"
  stepper-button:
    backgroundColor: "transparent"
    rounded: "{rounded.full}"
    size: "44px"
  tab:
    backgroundColor: "transparent"
    padding: "12px 16px"
---

# Design System: Expensive Mini Savings

## Overview

**Creative North Star: "The Honest Meter"**

The EMS page is an instrument, not a dashboard of decorations. Every view answers one question with one
number at the size that question deserves, and gives the reason and the arithmetic right under it: what the
battery does, why, what it costs, and what it really saved after losses and wear. Nothing is rounded up to
look better. When a value is small, it is shown small and honestly.

The page lives inside Home Assistant and looks native there. It inherits the theme font, the light and dark
theme, the card background and border, and the primary colour; it ships no web font and no fixed text or
surface colours. Its own character comes from two places only: the fixed colours of the battery actions,
which carry the same meaning in every chart, pill, badge and approval, and the large tabular numbers that
lead each card.

Density is medium. Cards breathe (20px padding, 16px gaps), but tables and logs are compact and tabular, because
the people checking a decision want to scan rows of times and amounts.

**Key Characteristics:**
- One main number per view, supporting values smaller and quieter beneath it.
- A reason in plain German next to every decision.
- Action colours are semantic and fixed; everything else follows the HA theme.
- Flat, bordered cards with tinted surfaces for emphasis; no custom shadows.
- Tabular figures wherever numbers are compared.

## Colors

Everything structural comes from the Home Assistant theme; the app adds a fixed, semantic set of action and
data colours that follows the HA energy dashboard.

### Primary
- **HA Primary** (`--primary-color`, fallback in frontmatter): selected tab underline, selected mode, focus ring,
  links, the "now" marker in the plan chart.
- **EMS Accent** (primary mixed 70 % with black): filled primary buttons only, so white text keeps its
  contrast on light primary colours.

### Secondary: battery actions
- **Stone Grey** (`stone-grey`): *Eigenverbrauch*, the battery running normally; also the dashed "without
  EMS" state-of-charge line.
- **Hold Blue** (`hold-blue`): *Entladung gesperrt*, the battery holding its charge.
- **Grid Charge Pink** (`grid-charge-pink`): *Laden aus dem Netz*.
- Measured-only states, used in history and log: **Paused Blue** (*Angehalten*), **Forced Discharge Violet**
  (*Erzwungenes Entladen*), **Provider Red** (*Anbieter steuert*).

### Tertiary: data series
- **Battery Value Teal** and **SoC Teal**: value of one kWh in the battery, state of charge (line with a light
  area).
- **Solar Orange**: PV forecast and production. **Load Slate**: household load. **PV-Boost Violet**: heat pump
  PV-Boost windows.
- **Earned Green** (`earned-green`): measured savings, the savings hero number and the "Gemessen" bars.
  **Forecast Grey**: what the plan promised.
- **Price scale** (cheap green → middle amber → expensive red): price bars, interpolated between the day's
  minimum and maximum.

### Neutral
- **Card Background**, **Divider**, **Primary Text**, **Secondary Text**: all HA theme variables
  (`--ha-card-background`, `--divider-color`, `--primary-text-color`, `--secondary-text-color`).
- **Warning** and **Error** (`--warning-color`, `--error-color`): tinted notice surfaces and the tab badge.

### Named Rules
**The Fixed Meaning Rule.** An action colour means the same action everywhere: chart band, pill, approval
icon, history bar. A colour is never reused for something else (Earned Green is not a battery state, Hold
Blue is not a link).

**The Theme Owns the Surface Rule.** Text, background, card and divider colours always come from HA theme
variables. Only the semantic action and data colours are fixed values.

**The Never Colour Alone Rule.** Every state that has a colour also has a word or an icon: the pill carries the
action name, an external change says "von außen", a correction says "Korrektur".

## Typography

**Display Font:** inherited from the Home Assistant theme (Roboto, then the system sans-serif)
**Body Font:** the same

**Character:** One family, contrast through size and weight only. Numbers carry the hierarchy: large,
semibold, tabular, slightly tightened.

### Hierarchy
- **Display** (600, 3rem, 1.1, −0.02em, tabular): the single main number of a view, e.g. "Gespart".
- **Metric** (600, 2.6rem, 1.1, tabular): the gauges in the "Jetzt" card (price now, battery level).
- **Headline** (600, 1.9rem, 1.2, −0.015em, balanced wrap): the sentence saying what the battery does now.
- **Title** (600, 1.3rem): card headings, with a 24px secondary-coloured icon.
- **Body** (400, 1rem, 1.45): reasons and explanations, at most about 70–72ch per line.
- **Label** (400, 0.9–0.95rem, secondary text colour): gauge labels, notes, pills, notices, table headers (500).

### Named Rules
**The One Big Number Rule.** A view has one Display number. Supporting figures (today, week, month) sit below
it at body or label size, never as a row of equal tiles.

**The Tabular Rule.** Every number that is compared with another (tables, KPIs, costs) uses tabular figures.

## Layout

A centred column of cards, up to 1400px wide, with 16px padding (12px × 8px under 600px). Cards stack in a
16px-gap grid; paired cards use a 2 : 1 row (the "Jetzt" card next to the control card) that collapses to one
column under 900px. Card grids auto-fit with a 420px minimum. Gauges inside a card auto-fit at 240px.

The page reads top-down in order of urgency: a pending approval first, then what the battery does now and
why, then the chart, then the planned actions. Tabs (Plan, Ersparnis, Verlauf, Protokoll, Einstellungen) sit
in a sticky header below the HA app bar. On phones the tab icons are hidden and only the labels remain.

## Elevation & Depth

Flat. Cards use the HA card border (1px divider colour) and whatever shadow the user's theme defines
(`--ha-card-box-shadow`, none by default); the app adds no shadows of its own. Emphasis comes from tinted
surfaces instead: the approval card is tinted 9 % with its action colour and bordered at 45 %, notices are
tinted 14 % with warning or error, the selected mode 10 % with the primary colour.

### Named Rules
**The Tint, Don't Lift Rule.** To make something stand out, tint its surface with its meaning colour; do not
raise it with a shadow.

## Shapes

Soft but not playful. Cards have gently rounded corners (12px, or the theme's card radius). Options and
confirmations use 10px, notices 8px, progress bars 6px. Everything you press or that labels an action is fully
rounded: buttons, pills, stepper buttons, the tab badge. Icon badges are rounded squares (18px on the 60px
"Jetzt" badge, 12px on the 40px approval icons). Dots (8px) mark notices and pills.

## Components

### Buttons
- **Shape:** fully rounded (999px), at least 44px high, 20px side padding, 8px gap to a 20px icon.
- **Primary:** EMS Accent background, white text, weight 500. Used for the one decisive action of a card
  (Freigeben).
- **Secondary:** transparent with a divider-coloured border and primary text colour (Ablehnen, Abbrechen).
- **Focus:** 2px primary outline, 2px offset. **Disabled:** 50 % opacity.

### Chips (action pills)
- **Style:** tinted at 18 % with the action colour, an 8px dot in the full action colour, text in the primary
  text colour so contrast holds in both themes. Fully rounded, 0.9rem.

### Cards / Containers
- **Corner Style:** 12px (theme card radius).
- **Background:** HA card background; tinted with an action colour only for the approval card.
- **Shadow Strategy:** theme shadow only (see Elevation & Depth).
- **Border:** 1px divider colour.
- **Internal Padding:** 20px.

### Inputs / Fields
- **Mode selection:** a vertical radio group of bordered options (10px radius, 44px minimum height). Each
  option has a ring dot, a semibold name and a one-line description in secondary text. Selected: primary border,
  10 % primary tint, the dot filled to a thick primary ring. Switching to *Automatisch* asks for confirmation
  in a warning-tinted box first.
- **Stepper:** two round 44px buttons with a divider border around a semibold tabular value (72px minimum).

### Navigation
- **Tabs:** text buttons with an 18px icon, secondary text colour; the selected tab turns primary text,
  semibold, with a 2px primary underline. Arrow keys move between tabs. A red count badge marks pending
  approvals.

### Notice
- A tinted surface (warning, error or success at 14 %), 8px radius, an 8px dot at the start, 0.9rem text.
  Used for missing inputs, errors, hints and the answer after an approval.

### Approval card (signature)
- First card on Plan while something waits for approval: tinted 9 % with the action colour, a 40px icon
  badge per action, the action as a sentence, "Spart ca." in bold, the reason in plain words, the arithmetic
  behind a "Rechnung" disclosure (44px summary). Above the buttons it states how long an approval holds and
  that without it the battery runs normally.
- After approving it turns into a compact "Freigegeben" card: each approved action as time + pill + savings,
  "Gilt bis …", and a secondary "Freigabe zurücknehmen" button. The answer to Freigeben / Ablehnen appears
  directly above it, never in another card.

### Progress bar
- 12px high, 6px radius, track at 10 % of the text colour, fill in SoC Teal (battery) or Earned Green
  (promise kept). A 3px marker in the text colour shows a reference point.

### Plan chart (signature)
- ECharts, one shared time axis: price bars coloured on the price scale, state-of-charge line with its "without
  EMS" dashed counterpart, PV area, load line, action bands in the action colours and a dashed primary line
  at "now". On narrow screens the chart compacts its axes and legend.

## Do's and Don'ts

### Do:
- **Do** give each view one Display number (3rem, tabular, semibold) and put the rest below it at body or
  label size.
- **Do** pair every action colour with its fixed name from PRODUCT.md (*Eigenverbrauch*, *Entladung gesperrt*,
  *Laden aus dem Netz*) or its icon.
- **Do** use HA theme variables for every text, surface, border and focus colour, with the fallbacks shown in
  the frontmatter.
- **Do** keep pressable targets at least 44px high, including disclosure summaries and filter chips.
- **Do** answer an action where it was taken: feedback appears next to the control that was pressed.
- **Do** emphasise with tinted surfaces (9–18 % of the meaning colour), not shadows.

### Don't:
- **Don't** build a row of six equal KPI tiles; one number leads, the others support.
- **Don't** let colour be the only signal for a state; add a word or an icon.
- **Don't** load a web font or hard-code text or background colours; the page must follow the user's HA
  theme in light and dark.
- **Don't** reuse an action colour for anything that is not that action.
