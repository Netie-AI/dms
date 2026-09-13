---
date: 2026-09-13
topic: studio-mobile-01
keywords: [STUDIO-MOBILE-01, SourcePanel, AppShell, StudioPage, drawer, lg, dms-171]
---

# STUDIO-MOBILE-01 layout path

PREFLIGHT: MISS (no prior mobile-sources finding).

## Main idea

The live walk (390x844) measured production: open SOURCES is a 352px flex sibling, `main` width 0. Collapse works; arriving answer reopens the dock. Header scrollWidth 534.

PR follow-up: AppShell `source-panel-slot` is `max-lg:w-0` so even an open overlay cannot steal flex width. `shouldExpandSourcesDock` is false below lg for ask and value-trace. TopBar short labels. Desktop `lg` dock unchanged. Not #8 COMPLETE.


## Files

- `apps/ui/src/components/SourcePanel.tsx`
- `apps/ui/src/context/AppContext.tsx`
- `apps/ui/src/pages/StudioPage.tsx`
- `apps/ui/src/lib/viewport.ts`

## Reuse

Do not treat a stacked Studio files column as the Chat overlay. `SourcePanel` is only mounted when `pathname === "/"`.
