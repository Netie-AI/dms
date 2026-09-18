---
date: 2026-09-18
topic: studio-mobile-03
keywords: [STUDIO-MOBILE-03, TopBar, New menu, overflow-x-auto, Sources chip, 390px, dms-192]
---

# STUDIO-MOBILE-03 TopBar phone clip

PREFLIGHT: HIT (#182 LeftNav drawer exists; TopBar still used overflow-x-auto).

## Main idea

`overflow-x-auto` on the header makes overflow-y compute to auto, so the New dropdown is clipped and not hit-testable. Space was `max-w-[7rem]` plus a compact `netie` title. Chat Sources chip (`fixed right-3 top-16`) sat on the h1.

Fix: `overflow-visible`; drop the title; Space `min-w-[8rem] flex-1`; New menu `z-50` right-aligned below `lg`; Chat `max-lg:pr-24`. Operate drawer and #171 Sources overlay unchanged. Live phone walk is still Platform/Frontend leftover. Not #182 product DONE. Not EPIC-008 COMPLETE.

## Files

- `apps/ui/src/components/TopBar.tsx`
- `apps/ui/src/pages/ChatPage.tsx`
- `apps/ui/src/components/SourcePanel.tsx`
- `tests/test_studio_mobile_layout.py`
- `apps/ui/e2e/studio-mobile.spec.ts`

## Reuse

Do not put overflow-x on the header to "fit" phone chrome. Fit by dropping secondary labels and giving Space the leftover width.
