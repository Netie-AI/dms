# CCA hostile international paraphrase harvest

**Date:** 2026-09-05
**Keywords:** CCA, filter-engagement, hostile, international, paraphrase, false-positive

**Count:** 250 unique asks
**By class:** 1=38, 2=32, 3=32, 4=30, 5=30, 6=37, 7=28, 8=23
**Public URLs:** 3. Remainder `synthetic:<class>`.

Harvester `ordinary_or_filter` is **not gold**. City KL is not a country filter. Independent labeler must ignore those tags.

## Purpose

Stress-test filter-engagement detector with ordinary English cue words, negation traps,
encoding mismatches, attribute vs filter, grouping vs filter, international locale phrasing,
multi-constraint filters, and mixed-language queries.

## JSON

```json
[
  {
    "id": "hst-001",
    "question": "Show all purchases from SUP-02 last quarter",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "all = completeness, not asset-class filter"
  },
  {
    "id": "hst-002",
    "question": "List any overdue invoices above RM 50,000",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "any = existential quantifier"
  },
  {
    "id": "hst-003",
    "question": "Are there any shipments still in transit to Penang?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "in = preposition for location status"
  },
  {
    "id": "hst-004",
    "question": "Which products are in LA warehouse right now?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "in = physical location"
  },
  {
    "id": "hst-005",
    "question": "What is the market share of SKU-BETA in Q3?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "market = business term not market filter"
  },
  {
    "id": "hst-006",
    "question": "Segment the customers by spend decile for FY24",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "segment = verb for analytics"
  },
  {
    "id": "hst-007",
    "question": "How many orders came in after 5pm Friday?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "in = time preposition"
  },
  {
    "id": "hst-008",
    "question": "Show all line items with no discount applied",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "no = absence of discount not negation filter"
  },
  {
    "id": "hst-009",
    "question": "Which SKUs are only sold online?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "only = channel restriction"
  },
  {
    "id": "hst-010",
    "question": "What class of steel did we procure for Plant 3?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "class = material grade not asset class"
  },
  {
    "id": "hst-011",
    "question": "Rank all regions by gross margin",
    "source_url": "https://www.mckinsey.com/capabilities/growth-marketing-and-sales/our-insights",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "regions = grouping dimension"
  },
  {
    "id": "hst-012",
    "question": "List every country we shipped to in January",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "country as report column not filter yet"
  },
  {
    "id": "hst-013",
    "question": "Which sector grew fastest in our portfolio?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "sector = industry bucket in prose"
  },
  {
    "id": "hst-014",
    "question": "Show all returns with no RMA number",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "no = missing field"
  },
  {
    "id": "hst-015",
    "question": "Are any contracts expiring in Q4?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "any = existence check"
  },
  {
    "id": "hst-016",
    "question": "What market price did we pay for copper cathode?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "market = commodity price"
  },
  {
    "id": "hst-017",
    "question": "Break down revenue in MYR and USD",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "in = currency expression"
  },
  {
    "id": "hst-018",
    "question": "Which customers are in arrears over 90 days?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "in = status phrase"
  },
  {
    "id": "hst-019",
    "question": "Show all SKUs with no stock movement in 60 days",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "no = zero activity"
  },
  {
    "id": "hst-020",
    "question": "Segment pipeline by deal stage",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "segment = analytics verb"
  },
  {
    "id": "hst-021",
    "question": "List any POs still open for SUP-07",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "any = open POs"
  },
  {
    "id": "hst-022",
    "question": "What is our share in the widget category?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "in = category membership prose"
  },
  {
    "id": "hst-023",
    "question": "Show all inbound ASNs received in March",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "in = calendar month"
  },
  {
    "id": "hst-024",
    "question": "Which lots are in quarantine hold?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "in = status"
  },
  {
    "id": "hst-025",
    "question": "No shipments left warehouse B today?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "no = zero count question"
  },
  {
    "id": "hst-026",
    "question": "Classify vendors by payment terms",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "class = classify verb"
  },
  {
    "id": "hst-027",
    "question": "Market outlook slide: what changed vs plan?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "market = presentation context"
  },
  {
    "id": "hst-028",
    "question": "Country of origin for lot L-8842?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "country = attribute lookup"
  },
  {
    "id": "hst-029",
    "question": "Region manager for North zone?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "region = org role"
  },
  {
    "id": "hst-030",
    "question": "Sector P/E for our peer set?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "sector = finance jargon"
  },
  {
    "id": "hst-031",
    "question": "Show all GRNs posted in SAP yesterday",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "all = full list"
  },
  {
    "id": "hst-032",
    "question": "Any quality holds on batch B-119?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "any = existence"
  },
  {
    "id": "hst-033",
    "question": "Only show rows where margin is negative",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "only = UI instruction"
  },
  {
    "id": "hst-034",
    "question": "What segment does customer C-441 belong to?",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "segment = attribute question"
  },
  {
    "id": "hst-035",
    "question": "List purchases in chronological order",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "ordinary",
    "notes": "in = sort order"
  },
  {
    "id": "hst-036",
    "question": "Commercial class revenue only for Malaysia",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "filter",
    "notes": "explicit commercial + country filters"
  },
  {
    "id": "hst-037",
    "question": "Residential segment sales in Singapore only",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "filter",
    "notes": "segment + country + only"
  },
  {
    "id": "hst-038",
    "question": "Show only residential properties in KL",
    "source_url": "synthetic:1-cue-words",
    "attack_class": "1",
    "ordinary_or_filter": "filter",
    "notes": "residential + city filter"
  },
  {
    "id": "hst-039",
    "question": "Total revenue excluding tax for Q2",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "excluding tax not excluding asset class"
  },
  {
    "id": "hst-040",
    "question": "Commercial revenue net of VAT",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "commercial as revenue type label"
  },
  {
    "id": "hst-041",
    "question": "Residential is excluded from this dashboard scope",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "meta scope not query filter"
  },
  {
    "id": "hst-042",
    "question": "All of SEA other than Singapore — what is YoY growth?",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "geo negation with filter intent"
  },
  {
    "id": "hst-043",
    "question": "Not just commercial — include mixed-use too",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "widening scope not filter"
  },
  {
    "id": "hst-044",
    "question": "No matter if commercial or residential, show total rent",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "no matter = ignore distinction"
  },
  {
    "id": "hst-045",
    "question": "Revenue excluding intercompany eliminations",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "accounting exclusion"
  },
  {
    "id": "hst-046",
    "question": "Sales ex-factory excluding freight",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "price basis exclusion"
  },
  {
    "id": "hst-047",
    "question": "Commercial units but not including staff housing",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "commercial filter with carve-out"
  },
  {
    "id": "hst-048",
    "question": "Malaysia and Thailand, not Indonesia",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "explicit geo negation"
  },
  {
    "id": "hst-049",
    "question": "All countries except CN for export volume",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "country negation filter"
  },
  {
    "id": "hst-050",
    "question": "Residential leases excluding short-stay",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "residential + subtype exclusion"
  },
  {
    "id": "hst-051",
    "question": "Commercial portfolio excluding vacant units",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "commercial with occupancy carve-out"
  },
  {
    "id": "hst-052",
    "question": "SEA revenue without Singapore contribution",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "regional minus one country"
  },
  {
    "id": "hst-053",
    "question": "Not limited to commercial — show entire book",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "widening not filtering"
  },
  {
    "id": "hst-054",
    "question": "Exclude returns from gross sales",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "returns exclusion accounting"
  },
  {
    "id": "hst-055",
    "question": "Industrial segment but not mining sub-sector",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "sector with negated sub-sector"
  },
  {
    "id": "hst-056",
    "question": "All property types except residential",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "explicit residential exclusion"
  },
  {
    "id": "hst-057",
    "question": "Commercial revenue, residential stripped out",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "polarity strip residential"
  },
  {
    "id": "hst-058",
    "question": "VN and PH only, no MY",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "country include/exclude combo"
  },
  {
    "id": "hst-059",
    "question": "Ex tax and ex duty — what is net?",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "tax/duty exclusions"
  },
  {
    "id": "hst-060",
    "question": "Non-commercial income lines — list them",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "non-commercial as account category"
  },
  {
    "id": "hst-061",
    "question": "Everything but residential for cap rate table",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "residential exclusion filter"
  },
  {
    "id": "hst-062",
    "question": "Commercial class, not including land bank",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "commercial with land exclusion"
  },
  {
    "id": "hst-063",
    "question": "No residential in this chart — correct?",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "confirmation about viz scope"
  },
  {
    "id": "hst-064",
    "question": "Revenue net of rebates, gross of returns",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "accounting polarity"
  },
  {
    "id": "hst-065",
    "question": "Excluding FX revaluation impact",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "FX exclusion"
  },
  {
    "id": "hst-066",
    "question": "All ASEAN except Myanmar",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "regional negation"
  },
  {
    "id": "hst-067",
    "question": "Commercial tenants only, sublease excluded",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "commercial + sublease negation"
  },
  {
    "id": "hst-068",
    "question": "Not residential — is this row commercial?",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "attribute check not filter"
  },
  {
    "id": "hst-069",
    "question": "Sales excluding discontinued operations",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "ordinary",
    "notes": "discontinued ops exclusion"
  },
  {
    "id": "hst-070",
    "question": "Industrial only, no retail footprint",
    "source_url": "synthetic:2-negation",
    "attack_class": "2",
    "ordinary_or_filter": "filter",
    "notes": "sector include + retail negation"
  },
  {
    "id": "hst-071",
    "question": "Revenue in Malaysia last fiscal year",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "full country name"
  },
  {
    "id": "hst-072",
    "question": "Revenue in MY last fiscal year",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "ISO country code"
  },
  {
    "id": "hst-073",
    "question": "Sales for Kuala Lumpur outlets",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "full city name"
  },
  {
    "id": "hst-074",
    "question": "Sales for KL outlets",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "city abbreviation"
  },
  {
    "id": "hst-075",
    "question": "Units sold for BETA variant",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "ordinary",
    "notes": "BETA as product name fragment"
  },
  {
    "id": "hst-076",
    "question": "Units sold for SKU-BETA",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "ordinary",
    "notes": "full SKU code"
  },
  {
    "id": "hst-077",
    "question": "Vietnam distributor revenue",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "full country name"
  },
  {
    "id": "hst-078",
    "question": "VN distributor revenue",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "country code"
  },
  {
    "id": "hst-079",
    "question": "Johor Bahru warehouse throughput",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "ordinary",
    "notes": "city name not filter code"
  },
  {
    "id": "hst-080",
    "question": "JB warehouse throughput",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "ordinary",
    "notes": "city abbreviation"
  },
  {
    "id": "hst-081",
    "question": "Thailand vs TH — same total?",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "name vs code equivalence"
  },
  {
    "id": "hst-082",
    "question": "Indonesia sales IDR and ID country",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "ID country code vs currency"
  },
  {
    "id": "hst-083",
    "question": "United Arab Emirates trade volume",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "full country name"
  },
  {
    "id": "hst-084",
    "question": "UAE trade volume",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "country abbreviation"
  },
  {
    "id": "hst-085",
    "question": "South Africa ZA shipments",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "country name + code"
  },
  {
    "id": "hst-086",
    "question": "Australia AU export value",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "name + ISO"
  },
  {
    "id": "hst-087",
    "question": "United Kingdom UK wholesale",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "name + code"
  },
  {
    "id": "hst-088",
    "question": "Germany DE plant output",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "name + code"
  },
  {
    "id": "hst-089",
    "question": "Brazil BR subsidiary revenue",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "name + code"
  },
  {
    "id": "hst-090",
    "question": "Mexico MX maquiladora spend",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "name + code"
  },
  {
    "id": "hst-091",
    "question": "India IN manufacturing cost",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "name + code"
  },
  {
    "id": "hst-092",
    "question": "Japan JP OEM orders",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "name + code"
  },
  {
    "id": "hst-093",
    "question": "Penang vs PNG — clarify which PNG",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "ordinary",
    "notes": "ambiguous abbreviation trap"
  },
  {
    "id": "hst-094",
    "question": "ALPHA vs SKU-ALPHA revenue",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "ordinary",
    "notes": "partial vs full SKU"
  },
  {
    "id": "hst-095",
    "question": "BETA margin vs SKU-BETA margin",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "ordinary",
    "notes": "encoding pair comparison"
  },
  {
    "id": "hst-096",
    "question": "Kuala Lumpur vs KL — same store list?",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "ordinary",
    "notes": "city encoding equivalence"
  },
  {
    "id": "hst-097",
    "question": "Malaysia MYR sales vs Malaysia country filter",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "currency vs country"
  },
  {
    "id": "hst-098",
    "question": "VN Ho Chi Minh vs Vietnam national",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "city within country"
  },
  {
    "id": "hst-099",
    "question": "SG commercial rent",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "country code + asset class"
  },
  {
    "id": "hst-100",
    "question": "Singapore residential occupancy",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "filter",
    "notes": "full name + asset class"
  },
  {
    "id": "hst-101",
    "question": "SEA region total vs individual countries",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "ordinary",
    "notes": "region vs country scope"
  },
  {
    "id": "hst-102",
    "question": "COM class assets — is that commercial?",
    "source_url": "synthetic:3-encoding",
    "attack_class": "3",
    "ordinary_or_filter": "ordinary",
    "notes": "class code vs word"
  },
  {
    "id": "hst-103",
    "question": "What commercial class is asset A-102?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "attribute lookup"
  },
  {
    "id": "hst-104",
    "question": "What asset class does warehouse W-7 have?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "attribute question"
  },
  {
    "id": "hst-105",
    "question": "Is this lease residential or commercial?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "binary attribute"
  },
  {
    "id": "hst-106",
    "question": "Which sector is customer C-88 tagged under?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "customer attribute"
  },
  {
    "id": "hst-107",
    "question": "What country is supplier S-12 registered in?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "registration attribute"
  },
  {
    "id": "hst-108",
    "question": "Commercial properties only — filter report",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "filter",
    "notes": "explicit filter instruction"
  },
  {
    "id": "hst-109",
    "question": "Residential units only for occupancy rate",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "filter",
    "notes": "explicit residential filter"
  },
  {
    "id": "hst-110",
    "question": "Show me the class field for building B-3",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "field lookup"
  },
  {
    "id": "hst-111",
    "question": "Does invoice I-551 say commercial or industrial?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "document attribute"
  },
  {
    "id": "hst-112",
    "question": "What market segment label is on this deal?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "CRM attribute"
  },
  {
    "id": "hst-113",
    "question": "Commercial class breakdown as columns not filter",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "dimension not filter"
  },
  {
    "id": "hst-114",
    "question": "List asset class values in the dataset",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "distinct values query"
  },
  {
    "id": "hst-115",
    "question": "Filter to commercial only",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "filter",
    "notes": "bare filter command"
  },
  {
    "id": "hst-116",
    "question": "Industrial class only for plant P-2",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "filter",
    "notes": "class + location filter"
  },
  {
    "id": "hst-117",
    "question": "What is the property type of unit U-9?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "property type attribute"
  },
  {
    "id": "hst-118",
    "question": "Commercial or residential — which tag applies?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "tag attribute"
  },
  {
    "id": "hst-119",
    "question": "Country code on this shipment record?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "field on record"
  },
  {
    "id": "hst-120",
    "question": "Sector column value for row 442?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "cell attribute"
  },
  {
    "id": "hst-121",
    "question": "Only commercial tenants in the rent roll",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "filter",
    "notes": "filter on tenant class"
  },
  {
    "id": "hst-122",
    "question": "Residential category in P&L — which line?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "account mapping question"
  },
  {
    "id": "hst-123",
    "question": "Commercial revenue stream — which GL?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "GL mapping not filter"
  },
  {
    "id": "hst-124",
    "question": "What region is cost center 440 assigned?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "org attribute"
  },
  {
    "id": "hst-125",
    "question": "Segment name for cohort 2024-Q1?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "cohort attribute"
  },
  {
    "id": "hst-126",
    "question": "Filter residential leases expiring 2026",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "filter",
    "notes": "residential + date filter"
  },
  {
    "id": "hst-127",
    "question": "Commercial asset register — full list",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "filter",
    "notes": "commercial scope list"
  },
  {
    "id": "hst-128",
    "question": "Is SKU-BETA in the premium class?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "product class attribute"
  },
  {
    "id": "hst-129",
    "question": "Which class does the auditor use for this site?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "audit taxonomy attribute"
  },
  {
    "id": "hst-130",
    "question": "Commercial use permit — expiry date?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "permit attribute not filter"
  },
  {
    "id": "hst-131",
    "question": "Residential zoning for parcel P-44?",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "zoning attribute"
  },
  {
    "id": "hst-132",
    "question": "Show commercial-class filters currently applied",
    "source_url": "synthetic:4-attribute-vs-filter",
    "attack_class": "4",
    "ordinary_or_filter": "ordinary",
    "notes": "meta question about filters"
  },
  {
    "id": "hst-133",
    "question": "Revenue by country for FY24",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "group by country"
  },
  {
    "id": "hst-134",
    "question": "Rank categories in Malay language labels",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "rank + locale not country filter"
  },
  {
    "id": "hst-135",
    "question": "Sales by region, not filtered to one region",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "grouping explicit"
  },
  {
    "id": "hst-136",
    "question": "Margin by asset class column",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "group by class"
  },
  {
    "id": "hst-137",
    "question": "Top 5 countries by volume",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "ranking across countries"
  },
  {
    "id": "hst-138",
    "question": "Segment revenue by customer tier",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "segment as verb + group"
  },
  {
    "id": "hst-139",
    "question": "Break down industrial vs commercial by quarter",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "cross-tab not single filter"
  },
  {
    "id": "hst-140",
    "question": "Country-wise export breakdown",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "pivot phrasing"
  },
  {
    "id": "hst-141",
    "question": "Group leases by property type",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "explicit group by"
  },
  {
    "id": "hst-142",
    "question": "Market share by sector chart",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "sector as dimension"
  },
  {
    "id": "hst-143",
    "question": "Revenue in Malaysia only",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "filter",
    "notes": "single-country filter not group"
  },
  {
    "id": "hst-144",
    "question": "Commercial only — then group by country",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "filter",
    "notes": "filter then group"
  },
  {
    "id": "hst-145",
    "question": "Compare residential and commercial by city",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "compare classes by dimension"
  },
  {
    "id": "hst-146",
    "question": "YoY growth by ASEAN country",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "multi-country group"
  },
  {
    "id": "hst-147",
    "question": "Distribution of SKUs across warehouses",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "distribution = group"
  },
  {
    "id": "hst-148",
    "question": "Split revenue by commercial/residential",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "split = group by class"
  },
  {
    "id": "hst-149",
    "question": "Malaysia-only slice grouped by month",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "filter",
    "notes": "filter + group"
  },
  {
    "id": "hst-150",
    "question": "Sector ranking for APAC",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "rank within region"
  },
  {
    "id": "hst-151",
    "question": "Count units by country of manufacture",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "manufacture country dimension"
  },
  {
    "id": "hst-152",
    "question": "Aggregate rent by asset class",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "aggregate by class"
  },
  {
    "id": "hst-153",
    "question": "Pivot table: region x product class",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "pivot dimensions"
  },
  {
    "id": "hst-154",
    "question": "Histogram of deal size by segment",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "segment dimension"
  },
  {
    "id": "hst-155",
    "question": "List each country with its total",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "per-country totals"
  },
  {
    "id": "hst-156",
    "question": "VN revenue only, monthly trend",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "filter",
    "notes": "country filter + time series"
  },
  {
    "id": "hst-157",
    "question": "Group by market segment then sort desc",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "market segment as CRM dimension"
  },
  {
    "id": "hst-158",
    "question": "Commercial revenue by country — all countries",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "class mentioned + group all countries"
  },
  {
    "id": "hst-159",
    "question": "Residential share by region",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "share calc by region"
  },
  {
    "id": "hst-160",
    "question": "Filter Malaysia then break down by sector",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "filter",
    "notes": "sequential filter + group"
  },
  {
    "id": "hst-161",
    "question": "Category rank within Singapore only",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "filter",
    "notes": "country filter + rank"
  },
  {
    "id": "hst-162",
    "question": "Cross-tab country vs property class",
    "source_url": "synthetic:5-grouping-vs-filter",
    "attack_class": "5",
    "ordinary_or_filter": "ordinary",
    "notes": "two-dimensional group"
  },
  {
    "id": "hst-163",
    "question": "Wie hoch war der Umsatz in Q3?",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "German Mittelstand CFO phrasing"
  },
  {
    "id": "hst-164",
    "question": "Zeige alle offenen Posten nach Land",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "German: open items by country"
  },
  {
    "id": "hst-165",
    "question": "Qual o faturamento por regiao no Brasil?",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "Brazilian distributor Portuguese"
  },
  {
    "id": "hst-166",
    "question": "Receita comercial no estado de Sao Paulo",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "filter",
    "notes": "BR commercial + state"
  },
  {
    "id": "hst-167",
    "question": "What is our FOB Jebel Ali export total?",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "UAE trader port reference"
  },
  {
    "id": "hst-168",
    "question": "Dubai commercial lease yield last year",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "filter",
    "notes": "UAE city + commercial filter"
  },
  {
    "id": "hst-169",
    "question": "今四半期の売上は？",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "Japanese manufacturer quarterly sales"
  },
  {
    "id": "hst-170",
    "question": "Commercial tenants in Osaka only",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "filter",
    "notes": "JP city + commercial"
  },
  {
    "id": "hst-171",
    "question": "India PLI scheme eligible production volume",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "Indian manufacturer policy term"
  },
  {
    "id": "hst-172",
    "question": "GST exclusive revenue Maharashtra plants",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "India tax + state"
  },
  {
    "id": "hst-173",
    "question": "Maquiladora spend in Tijuana vs Monterrey",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "Mexican maquiladora comparison"
  },
  {
    "id": "hst-174",
    "question": "IMMEX program commercial exports MX",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "filter",
    "notes": "Mexico program + commercial"
  },
  {
    "id": "hst-175",
    "question": "PGM output Rustenburg quarter",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "South African miner site"
  },
  {
    "id": "hst-176",
    "question": "ZAR revenue ex hedging — SA ops",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "SA miner currency phrasing"
  },
  {
    "id": "hst-177",
    "question": "UK wholesaler net sales ex VAT",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "UK VAT convention"
  },
  {
    "id": "hst-178",
    "question": "Commercial property yield in Manchester",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "filter",
    "notes": "UK city + commercial"
  },
  {
    "id": "hst-179",
    "question": "Wheat export volume WA vs QLD",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "Australian agribusiness states"
  },
  {
    "id": "hst-180",
    "question": "AUD farmgate price NSW barley",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "AU agri pricing"
  },
  {
    "id": "hst-181",
    "question": "Mittelstand: offene Debitoren nach Segment",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "DE SMEs debtors by segment"
  },
  {
    "id": "hst-182",
    "question": "Distribuidor: margem por cliente no RJ",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "BR distributor margin by client"
  },
  {
    "id": "hst-183",
    "question": "Trader: LC value for Sharjah shipments",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "UAE LC trade finance"
  },
  {
    "id": "hst-184",
    "question": "Manufacturer: 工場別の商業用物件収益",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "filter",
    "notes": "JP commercial property by plant"
  },
  {
    "id": "hst-185",
    "question": "Manufacturer: export obligation under SEZ",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "India SEZ compliance"
  },
  {
    "id": "hst-186",
    "question": "Maquiladora: valor agregado Mexico 2024",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "MX value-add reporting"
  },
  {
    "id": "hst-187",
    "question": "Miner: export parity price manganese",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "ZA commodity export"
  },
  {
    "id": "hst-188",
    "question": "Wholesaler: pallet lines shipped ex Dundee",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "UK logistics hub"
  },
  {
    "id": "hst-189",
    "question": "Agribusiness: containerised hay to Japan",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "AU export destination"
  },
  {
    "id": "hst-190",
    "question": "GmbH consolidated Umsatz nach Land",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "DE consolidated by country"
  },
  {
    "id": "hst-191",
    "question": "LTDA receita residencial SP capital",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "filter",
    "notes": "BR residential SP filter"
  },
  {
    "id": "hst-192",
    "question": "Free zone commercial rent roll UAE",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "filter",
    "notes": "UAE free zone commercial"
  },
  {
    "id": "hst-193",
    "question": "Kabushiki kaisha segment revenue APAC",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "JP corp APAC segment"
  },
  {
    "id": "hst-194",
    "question": "Pvt Ltd industrial power cost per unit",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "India industrial cost"
  },
  {
    "id": "hst-195",
    "question": "S de RL maquila labor cost per hour",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "ordinary",
    "notes": "MX maquila labor"
  },
  {
    "id": "hst-196",
    "question": "Handelsregister: commercial lease count DE",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "filter",
    "notes": "DE commercial lease count"
  },
  {
    "id": "hst-197",
    "question": "Nota fiscal: receita industrial BR",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "filter",
    "notes": "BR industrial revenue"
  },
  {
    "id": "hst-198",
    "question": "Bill of entry: commercial goods value UAE",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "filter",
    "notes": "UAE customs commercial"
  },
  {
    "id": "hst-199",
    "question": "Customs: 輸出商業物件の合計",
    "source_url": "synthetic:6-intl-locales",
    "attack_class": "6",
    "ordinary_or_filter": "filter",
    "notes": "JP commercial export total"
  },
  {
    "id": "hst-200",
    "question": "Commercial lease revenue in Malaysia excluding residential",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "class + country + negation"
  },
  {
    "id": "hst-201",
    "question": "Residential occupancy Singapore excluding commercial mixed-use",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "residential + SG + exclusion"
  },
  {
    "id": "hst-202",
    "question": "Industrial sector VN excluding retail sub-sector",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "sector + country + negation"
  },
  {
    "id": "hst-203",
    "question": "Commercial MY revenue net of residential share",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "triple constraint"
  },
  {
    "id": "hst-204",
    "question": "MY commercial leases in KL only",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + class + city"
  },
  {
    "id": "hst-205",
    "question": "SEA commercial portfolio excluding SG residential",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "region + class + exclusions"
  },
  {
    "id": "hst-206",
    "question": "VN industrial plants excluding subcontract residential housing",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + sector + carve-out"
  },
  {
    "id": "hst-207",
    "question": "Commercial rent TH excluding Bangkok residential condos",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + class + city carve-out"
  },
  {
    "id": "hst-208",
    "question": "ID commercial units Java only not residential",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + island + class negation"
  },
  {
    "id": "hst-209",
    "question": "PH commercial revenue Visayas excluding residential land",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + region + class"
  },
  {
    "id": "hst-210",
    "question": "AU commercial agri revenue NSW excluding residential farms",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + state + class"
  },
  {
    "id": "hst-211",
    "question": "UK commercial wholesale Midlands excluding residential storage",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + sector + exclusion"
  },
  {
    "id": "hst-212",
    "question": "DE commercial Mittelstand revenue Bavaria only",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + class + state"
  },
  {
    "id": "hst-213",
    "question": "BR commercial distributor margin south region only",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + class + region"
  },
  {
    "id": "hst-214",
    "question": "UAE commercial free-zone rent excluding residential visas",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + class + policy carve-out"
  },
  {
    "id": "hst-215",
    "question": "JP commercial Osaka manufacturing revenue only",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + class + city"
  },
  {
    "id": "hst-216",
    "question": "IN commercial PLI eligible plants excluding residential townships",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + class + exclusion"
  },
  {
    "id": "hst-217",
    "question": "MX commercial maquila value-add border states only",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + class + geo"
  },
  {
    "id": "hst-218",
    "question": "ZA commercial mining services excluding residential camps",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + sector + exclusion"
  },
  {
    "id": "hst-219",
    "question": "Commercial SKU-BETA sales Malaysia excluding Singapore",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "SKU + class + geo negation"
  },
  {
    "id": "hst-220",
    "question": "Residential KL only excluding commercial serviced apartments",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "class + city + exclusion"
  },
  {
    "id": "hst-221",
    "question": "MY + TH commercial revenue excluding residential and industrial",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "multi-country + class + negations"
  },
  {
    "id": "hst-222",
    "question": "Commercial lease escalation Malaysia 2024 excluding residential renewals",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "class + country + time + exclusion"
  },
  {
    "id": "hst-223",
    "question": "VN commercial export FOB excluding residential sample shipments",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + class + trade term + exclusion"
  },
  {
    "id": "hst-224",
    "question": "SG commercial office occupancy excluding residential SOHO",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "country + class + subtype exclusion"
  },
  {
    "id": "hst-225",
    "question": "Commercial revenue ASEAN excluding residential and SG",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "region + class + multi exclusion"
  },
  {
    "id": "hst-226",
    "question": "Industrial MY Penang excluding commercial showroom space",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "sector + country + city + exclusion"
  },
  {
    "id": "hst-227",
    "question": "Commercial tenant AR Malaysia excluding residential utility reimbursements",
    "source_url": "synthetic:7-multi-constraint",
    "attack_class": "7",
    "ordinary_or_filter": "filter",
    "notes": "class + country + AR carve-out"
  },
  {
    "id": "hst-228",
    "question": "What is jualan di Malaysia for Q3?",
    "source_url": "https://www.malaysia.gov.my/portal/content/24090",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "Malay jualan= sales + country"
  },
  {
    "id": "hst-229",
    "question": "Show omzet in Nederland last year",
    "source_url": "https://www.cbs.nl/en-gb",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "Dutch omzet=revenue + Netherlands"
  },
  {
    "id": "hst-230",
    "question": "Faturamento no Brasil by month",
    "source_url": "https://www.gov.br/receitafederal",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "PT faturamento + Brazil"
  },
  {
    "id": "hst-231",
    "question": "Umsatz in Deutschland commercial only",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "DE Umsatz + commercial filter"
  },
  {
    "id": "hst-232",
    "question": "売上 in Japan manufacturing segment",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "ordinary",
    "notes": "JP uriage=sales in mixed query"
  },
  {
    "id": "hst-233",
    "question": "Ingresos en Mexico maquiladora plants",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "ES ingresos + Mexico"
  },
  {
    "id": "hst-234",
    "question": "Doanh thu tai Viet Nam commercial leases",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "VI revenue + VN + commercial"
  },
  {
    "id": "hst-235",
    "question": "Pendapatan di Indonesia residential only",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "ID pendapatan + residential"
  },
  {
    "id": "hst-236",
    "question": "Ventes en France excluding residential",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "FR ventes + exclusion"
  },
  {
    "id": "hst-237",
    "question": "Ricavi in Italia commercial class",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "IT ricavi + commercial"
  },
  {
    "id": "hst-238",
    "question": "Inkomsten in Belgie by sector",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "ordinary",
    "notes": "NL inkomsten + group by sector"
  },
  {
    "id": "hst-239",
    "question": "Obroty w Polsce industrial only",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "PL obroty + industrial"
  },
  {
    "id": "hst-240",
    "question": "Tržby v Cesku commercial portfolio",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "CZ tržby + commercial"
  },
  {
    "id": "hst-241",
    "question": "Revenue in Malaysia — jualan komersial sahaja",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "EN + Malay commercial only"
  },
  {
    "id": "hst-242",
    "question": "Omzet commercial Nederland excluding residential",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "Dutch + EN mixed constraints"
  },
  {
    "id": "hst-243",
    "question": "Faturamento comercial Brasil vs residential",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "ordinary",
    "notes": "PT commercial vs residential compare"
  },
  {
    "id": "hst-244",
    "question": "UAE revenue — إيرادات تجارية only",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "EN + Arabic commercial revenue"
  },
  {
    "id": "hst-245",
    "question": "Sales in India — वाणिज्यिक leases only",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "EN + Hindi commercial"
  },
  {
    "id": "hst-246",
    "question": "Malaysia sales vs jualan kediaman",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "ordinary",
    "notes": "EN + Malay residential term"
  },
  {
    "id": "hst-247",
    "question": "Compare omzet and revenue in Netherlands",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "ordinary",
    "notes": "Dutch/EN synonym compare"
  },
  {
    "id": "hst-248",
    "question": "Segmen komersial di Malaysia by month",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "Malay segment + country + time"
  },
  {
    "id": "hst-249",
    "question": "Receita residencial no Brasil 2024",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "PT residential Brazil 2024"
  },
  {
    "id": "hst-250",
    "question": "Commercial Umsatz Deutschland excluding Wohnimmobilien",
    "source_url": "synthetic:8-mixed-language",
    "attack_class": "8",
    "ordinary_or_filter": "filter",
    "notes": "EN+DE commercial excluding residential RE"
  }
]
```
