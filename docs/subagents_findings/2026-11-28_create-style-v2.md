# CREATE-STYLE-V2

Flag default off. Only the digit 1 turns it on. Off, Create's `render_artifact` is unchanged and `/v1/create` is not a route.

On, `render_artifact` writes docx, pptx, xlsx, and HTML from `packages/core/dms_core/create_style/themes/pitch.json` and the layout blocks. A brand colour string overrides the accent. Parts the model already returned are copied onto those blocks. The existing Create generator still calls OpenVault. This code does not write a sentence.

`dms_core.create_style.spec.layout_blocks(kind)` is the list `dms_executor.create_context.layout_blocks` should return once DMS-CREATE-CONTEXT-01 lands. Off, it returns nothing, so the style dict stays as it was. `app`, `doc`, and `slides` share one schema.

Blocks: `image_side`, `image_below`, `two_column`, `kpi_strip`, `chart_commentary`, `table`, `chart_page` (exactly four). Excel writes a formatted Summary sheet and a Data sheet with every row.

Tests: `tests/test_create_style_v2.py`. Golden: `tests/fixtures/create_style/golden.json`. Not PASS.
