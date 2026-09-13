---
date: 2026-09-13
topic: studio-mobile-01
keywords: [STUDIO-MOBILE-01, SourcePanel, AppShell, StudioPage, drawer, lg, dms-171]
---

# STUDIO-MOBILE-01 layout path

PREFLIGHT: MISS (no prior mobile-sources finding).

## Main idea

The founder "SOURCES covers chat" report is the Chat `SourcePanel` flex sibling (`w-[22rem]`, default open) in `AppShell` on `/`. Studio `/studio` also stacked files + SQLSRC above preview below `lg`. Fix: drawer + closed-by-default below `lg`; keep desktop dock/grid. Not EPIC-008 COMPLETE. Not #8 close.

## Files

- `apps/ui/src/components/SourcePanel.tsx`
- `apps/ui/src/context/AppContext.tsx`
- `apps/ui/src/pages/StudioPage.tsx`
- `apps/ui/src/lib/viewport.ts`

## Reuse

Do not treat a stacked Studio files column as the Chat overlay. `SourcePanel` is only mounted when `pathname === "/"`.
