# LitEval Glass Workspace — Design System Master

**Selected scheme:** C · Dark Glass Workspace
**Date:** 2026-08-16
**Stack:** React 18 + Vite + plain CSS tokens + ECharts

## Direction

Premium dark research workspace built from frosted glass shells, atmospheric background light,
centered workspace framing, and restrained accent signals. Light glass is a supported secondary theme.

## Core Tokens

### Dark (default)

| Role | Value |
| --- | --- |
| Background | `#05070d` |
| Glass surface | `rgba(15, 19, 32, 0.58)` |
| Strong glass | `rgba(19, 24, 40, 0.82)` |
| Glass border | `rgba(255, 255, 255, 0.10)` |
| Glass highlight | `rgba(255, 255, 255, 0.14)` |
| Text | `#e8edf7` |
| Text soft | `#c6d0e0` |
| Muted | `#98a4b8` |
| Primary | `#3b82f6` |
| Green / Yellow / Red | `#22c55e` / `#eab308` / `#ef4444` |
| Shadow | `0 18px 44px rgba(0,0,0,0.42)` |

### Light (toggle)

| Role | Value |
| --- | --- |
| Background | `#e9eef8` |
| Glass surface | `rgba(255,255,255,0.66)` |
| Strong glass | `rgba(255,255,255,0.88)` |
| Text | `#1b2230` |
| Primary | `#2563eb` |

## Rules

1. Shadows express elevation; borders express structure.
2. Glass shells always: `backdrop-filter: blur(16-20px) saturate(1.2)` + 1px border + inset top highlight.
3. Radius is concentric: shell 18-22px, inner controls 9-12px.
4. Buttons press at `scale(0.96)`; no `transition: all`.
5. Numeric values use `tabular-nums`.
6. Headings `text-wrap: balance`; body `text-wrap: pretty`.
7. Dark is default; light theme must preserve the same glass hierarchy.
8. Motion is slow and atmospheric; `prefers-reduced-motion` disables drift and transforms.

## Page Overrides

- `pages/projects.md` — project library cards
- `pages/results.md` — recommendation tiles + dense table + project GNN
- `pages/detail.md` — decision banner as tinted glass stage + chart panels + GNN

## Refinement (2026-08-16)

- Icon family: `lucide-react` only; one stroke weight per context (1.7 UI, 1.8 actions, 2 status marks).
- Detail page central stage: decision banner focus card → radar 1.55fr / gauge 0.85fr → 4-column metric strip.
- Page enter: direct children stagger 0-250ms `rise-in`.
- Theme icons: crossfade via opacity/scale/blur, `cubic-bezier(0.2, 0, 0, 1)`.
