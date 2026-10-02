"""Red-team case schema: load and validate. Unknown keys are rejected.

One YAML list per family file. A bad case fails loudly at load time, never at
grade time, so an attacker finds a typo before it becomes a verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

FAMILIES = ("a", "b", "c", "d", "e")
LANES = ("gen", "sheet")
ASK_PATHS = ("generative", "product")
EXPECTS = ("answer", "abstain")

#: space key -> Space id. ``company`` is "no space_id" (the union of the Spaces'
#: grants; ``alerts`` stays ungrantable).
SPACE_IDS: dict[str, str | None] = {
    "finance": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    "ops": "dddddddd-dddd-dddd-dddd-dddddddddddd",
    "company": None,
}

ALLOWED_KEYS = frozenset(
    {
        "id",
        "family",
        "lane",
        "space",
        "ask_path",
        "question",
        "model_sql",
        "grounded_tables",
        "expect",
        "gold_sql",
        "gold_notes",
        "trap",
        "control",
    }
)
_ID_RE = re.compile(r"^([A-Ea-e])-(\d{3})$")
_SELECT_HEAD = re.compile(r"^\s*\(*\s*(select|with)\b", re.IGNORECASE)


class CaseError(ValueError):
    """A case file is malformed. The message names the file and the case."""


@dataclass(frozen=True)
class Case:
    id: str
    family: str
    question: str
    expect: str
    lane: str = "gen"
    space: str = "finance"
    ask_path: str = "generative"
    model_sql: str | None = None
    grounded_tables: tuple[str, ...] = ()
    gold_sql: str | None = None
    gold_notes: str = ""
    trap: str = ""
    control: bool = False
    source: str = field(default="", compare=False)

    @property
    def space_id(self) -> str | None:
        return SPACE_IDS[self.space]


def is_select_sql(sql: str) -> bool:
    return bool(_SELECT_HEAD.match(sql or ""))


def validate_case(raw: Any, *, source: str = "") -> Case:
    where = f"{source}: " if source else ""
    if not isinstance(raw, dict):
        raise CaseError(f"{where}a case must be a mapping, got {type(raw).__name__}")
    cid = raw.get("id", "<no id>")
    tag = f"{where}case {cid!r}"
    unknown = sorted(set(raw) - ALLOWED_KEYS)
    if unknown:
        raise CaseError(f"{tag}: unknown key(s) {unknown}; allowed {sorted(ALLOWED_KEYS)}")
    for key in ("id", "family", "question", "expect"):
        if key not in raw:
            raise CaseError(f"{tag}: missing required key {key!r}")

    m = _ID_RE.match(str(raw["id"]))
    if not m:
        raise CaseError(f"{tag}: id must look like A-001 (family letter a-e, dash, 3 digits)")
    family = str(raw["family"]).strip().lower()
    if family not in FAMILIES:
        raise CaseError(f"{tag}: family must be one of {FAMILIES}, got {raw['family']!r}")
    if m.group(1).lower() != family:
        raise CaseError(f"{tag}: id letter {m.group(1)!r} does not match family {family!r}")

    question = raw["question"]
    if not isinstance(question, str) or not question.strip():
        raise CaseError(f"{tag}: question must be a non-empty string")

    lane = str(raw.get("lane", "gen")).strip().lower()
    if lane not in LANES:
        raise CaseError(f"{tag}: lane must be one of {LANES}, got {raw.get('lane')!r}")
    space = str(raw.get("space", "finance")).strip().lower()
    if space not in SPACE_IDS:
        raise CaseError(f"{tag}: space must be one of {sorted(SPACE_IDS)}, got {space!r}")
    expect = str(raw["expect"]).strip().lower()
    if expect not in EXPECTS:
        raise CaseError(f"{tag}: expect must be one of {EXPECTS}, got {raw['expect']!r}")

    control = raw.get("control", False)
    if not isinstance(control, bool):
        raise CaseError(f"{tag}: control must be true or false")
    if control and expect != "answer":
        raise CaseError(f"{tag}: a control (model_sql is correct) must have expect: answer")

    grounded = raw.get("grounded_tables") or []
    if not isinstance(grounded, list) or not all(isinstance(t, str) and t for t in grounded):
        raise CaseError(f"{tag}: grounded_tables must be a list of table names")

    model_sql = raw.get("model_sql")
    ask_path_raw = raw.get("ask_path")
    if lane == "gen":
        if grounded:
            raise CaseError(f"{tag}: grounded_tables is for lane sheet only (a grounded ask skips the generative lane)")
        if not isinstance(model_sql, str) or not model_sql.strip():
            raise CaseError(f"{tag}: lane gen requires a non-empty model_sql")
        if not is_select_sql(model_sql):
            raise CaseError(f"{tag}: model_sql must start with SELECT or WITH")
        ask_path = str(ask_path_raw or "generative").strip().lower()
        if ask_path not in ASK_PATHS:
            raise CaseError(f"{tag}: ask_path must be one of {ASK_PATHS}, got {ask_path_raw!r}")
    else:
        if model_sql not in (None, ""):
            raise CaseError(f"{tag}: lane sheet runs no model; model_sql must be absent")
        ask_path = str(ask_path_raw or "product").strip().lower()
        if ask_path != "product":
            raise CaseError(f"{tag}: lane sheet runs on the product ladder only (the bronze-sheet lane)")
        model_sql = None

    gold_sql = raw.get("gold_sql")
    if expect == "answer":
        if not isinstance(gold_sql, str) or not gold_sql.strip():
            raise CaseError(f"{tag}: expect answer requires gold_sql")
    if gold_sql not in (None, ""):
        if not isinstance(gold_sql, str) or not is_select_sql(gold_sql):
            raise CaseError(f"{tag}: gold_sql must be a SELECT/WITH statement")
    else:
        gold_sql = None

    for key in ("gold_notes", "trap"):
        if key in raw and not isinstance(raw[key], str):
            raise CaseError(f"{tag}: {key} must be a string")

    return Case(
        id=str(raw["id"]).upper(),
        family=family,
        question=question.strip(),
        expect=expect,
        lane=lane,
        space=space,
        ask_path=ask_path,
        model_sql=model_sql.strip() if isinstance(model_sql, str) else None,
        grounded_tables=tuple(grounded),
        gold_sql=gold_sql.strip() if isinstance(gold_sql, str) else None,
        gold_notes=str(raw.get("gold_notes") or ""),
        trap=str(raw.get("trap") or ""),
        control=control,
        source=source,
    )


def _case_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted([*path.glob("*.yaml"), *path.glob("*.yml")])
    raise CaseError(f"cases path does not exist: {path}")


def load_cases(path: str | Path, *, family: str | None = None) -> list[Case]:
    """Load every case under ``path`` (a file or a directory of YAML files).

    ``family`` filters after validation (so a typo in another family's file is
    still reported). Duplicate ids across files are an error.
    """
    root = Path(path)
    files = _case_files(root)
    if not files:
        raise CaseError(f"no *.yaml case files under {root}")
    out: list[Case] = []
    seen: dict[str, str] = {}
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if data is None:
            continue
        if not isinstance(data, list):
            raise CaseError(f"{f}: top level must be a YAML list of cases")
        for raw in data:
            case = validate_case(raw, source=f.name)
            if case.id in seen:
                raise CaseError(f"{f.name}: duplicate case id {case.id} (also in {seen[case.id]})")
            seen[case.id] = f.name
            out.append(case)
    if family:
        fam = family.strip().lower()
        out = [c for c in out if c.family == fam]
    return out


def ext_sql_path_for(cases_path: str | Path, family: str) -> Path | None:
    """``<stem>.ext.sql`` next to a family YAML. Looks for ``<family>.ext.sql`` or a
    file whose stem starts with the family letter. None if absent."""
    root = Path(cases_path)
    folder = root if root.is_dir() else root.parent
    fam = family.lower()
    candidates = [folder / f"{fam}.ext.sql"]
    if root.is_file():
        candidates.insert(0, root.with_suffix("").with_suffix(".ext.sql"))
        candidates.insert(0, folder / f"{root.stem}.ext.sql")
    candidates += sorted(folder.glob(f"{fam}*.ext.sql"))
    for c in candidates:
        if c.is_file():
            return c
    return None
