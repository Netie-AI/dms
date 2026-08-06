# DMS ask playground

Founder break-test surface. **Edit prompts in `my_questions.yaml`**, keep sample
data in `data/`, run against a live Space.

Seed bank (don't fight over it): `questions.yaml`  
Your working copy: `my_questions.yaml`

## Product ladder (real badges)

| Layer | Meaning | Numbers? |
|-------|---------|----------|
| L0 | Certified / verified SQL asset | yes, executed |
| L1 | Governed metric | yes, executed |
| L2 | FreeRoute SQL | yes, executed |
| L3 | Doc-RAG | **prose/citations only** — no aggregates |

## L4 / L5 automation (labels only — P-DMS-33)

Not new badges until you confirm definitions.

| Label | Probe meaning | Builds via |
|-------|---------------|------------|
| L4 | Multi-step / synonym / encoding that stays precise | EPIC-019 + value-norm + linear verify |
| L5 | Steward registers trusted metrics; system answers from them | VQ-02 + EPIC-021 |

## Setup (once)

```powershell
python D:\DMS\scripts\gen_playground_data.py
D:\DMS\scripts\windows\Start-DMSStack.ps1 -StartSiblings -EnableL2 -StartUi -OpenBrowser
```

1. Create / open a Space in the UI.
2. Studio-upload everything under `playground/data/` into that Space.
3. Copy the Space id.

## Run

```powershell
python D:\DMS\scripts\playground_ask.py --dry

$env:DMS_URL = "http://127.0.0.1:8090"
python D:\DMS\scripts\playground_ask.py --space <space_id>

# after you change one prompt:
python D:\DMS\scripts\playground_ask.py --space <space_id> --only L2_malay_top3
```

## How to tweak

1. Open `playground/my_questions.yaml`.
2. Change only the `prompt:` line (keep `id:` stable).
3. Re-run `--only <id>`.
4. Read `badge`, `abstained`, `route`, and whether `rows` back the number.
5. If L3 shows a category total under a green badge — that is a P0 (E9).

## Oracle cheat sheet (`pg_sales.xlsx` :: Sales)

| Category | sales_value_myr |
|----------|----------------:|
| Electronics | 800,750.50 |
| Home | 500,100.00 |
| Apparel | 450,400.00 |
| Sports | 175,800.25 |
| Misc | 95,000.00 |

Top 3 on **Sales**: Electronics, Home, Apparel.  
**Wide_Fill** top 3 is Misc / Apparel / Sports — different on purpose.

| Filter truth | Value |
|--------------|------:|
| sku `SKU-BETA` | 380,250.50 |
| city `Kuala Lumpur` (not `KL`) | 1,111,150.50 |
| Late penalty (policy docs) | 5,000.00 MYR (L3 quote only) |
