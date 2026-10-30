---
keywords: [studio, sql-extract, verify, links, objects, pie, clean_sale_by_grp]
main_idea: Studio now shows PK/FK counts and verify() link status on the extract receipt. Live Finance Chat clean_sale_by_grp is L0 pie on objects.clean_sale. Goal open until #116 real MySQL/SQL Server.
---

# Studio link-verify + live object pie (2026-09-08)

PREFLIGHT: HIT extract-object-silver-tools + live-ask-after-unseal.

## What landed

- API already returned `links` from `verify_source_links`. Studio ignored it (`links?: unknown`).
- Studio extract receipt now prints declared PK/FK counts and `sqlLinksLabel` (empty FK set is not verified; violations listed).
- Live `:8090` republished `objects.clean_sale` + tool `clean_sale_by_grp`. Chat Finance: L0_CERTIFIED pie, Insights, source `objects.clean_sale`.

## Verify

- `npm test -- src/lib/sqlSourceLinks.test.ts` (3 passed)
- `python D:\DMS\.tmp\live_object_tool.py` -> pie L0
- Chat UI localhost:3000 `clean_sale_by_grp` certified + pie

## Trap

Cursor Write/StrReplace on new `.ts` can be UTF-16-LE (nuls). Vite overlay `Expected ";" but found "\x00"`. Rewrite with `[UTF8Encoding]::new($false)`.

## Not done

#116 live MySQL/SQL Server extract. No host :3306/:1433. Goal OPEN.