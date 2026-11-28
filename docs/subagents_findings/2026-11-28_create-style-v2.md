# CREATE-STYLE-V2

Flag default off. Only the digit 1 turns it on. Off, `/v1/create` is not mounted and the ask path is unchanged.

On, `GET /v1/create/layouts` lists block fields and theme keys. `POST /v1/create/render` calls `compliance_gate`, then renders docx, pptx, xlsx, or HTML. A body with `facts` and no blocks makes one OpenVault call, role `create`, schema `CreateDocument`. The provider picks the model. A bad reply is `model_output_invalid`. Nothing local writes a sentence.

The pitch file is `packages/core/dms_core/create_style/themes/pitch.json`: colours, fonts, logo, card alpha 0.55, and an 8px grid. A brand file overrides those keys.

Blocks: `image_side`, `image_below`, `two_column`, `kpi_strip`, `chart_commentary`, `table`, `chart_page` (exactly four). Images are center-cropped square. KPI cards are headline, number, and source. Text is stored whole. A word that cannot fit raises `word_does_not_fit` instead of being cut. Excel writes a formatted Summary sheet and a Data sheet with every row. No openpyxl workbook and no xlsxwriter.

Tests: `tests/test_create_style_v2.py`. Golden: `tests/fixtures/create_style/golden.json`. Not PASS.
