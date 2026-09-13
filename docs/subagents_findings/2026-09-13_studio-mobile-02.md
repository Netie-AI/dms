---
date: 2026-09-13
topic: studio-mobile-02
keywords: [STUDIO-MOBILE-02, LeftNav, TopBar, drawer, lg, dms-182]
---

# STUDIO-MOBILE-02 layout path

PREFLIGHT: PARTIAL (#171 Sources drawer exists; Operate nav was still a docked w-52).

## Main idea

Operate on phone-width opened the full LeftNav (`w-52`) because `setProductMode("graphite")` expanded the sidebar. Below `lg` the nav is now a closed drawer overlay (`left-nav-slot` `max-lg:w-0`), same pattern as #171 Sources. TopBar drops Spaces/Library/API/role below `lg` and stops using `overflow-x-hidden`. Live phone walk is still Platform/Frontend leftover. Not #171 DONE. Not EPIC-008 COMPLETE.

## Files

- `apps/ui/src/lib/viewport.ts` (`navStartsCollapsed`)
- `apps/ui/src/context/AppContext.tsx`
- `apps/ui/src/components/LeftNav.tsx`
- `apps/ui/src/components/TopBar.tsx`
- `apps/ui/src/components/AppShell.tsx`
- `tests/test_studio_mobile_layout.py`

## Reuse

Do not treat the collapsed desktop cream rail as the phone drawer. Phone collapsed is `max-lg:hidden` (zero width), not `w-12`.
