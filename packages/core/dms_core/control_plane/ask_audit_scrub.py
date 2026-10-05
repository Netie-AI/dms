"""BANK-02 (dms#269): what may be written into an ask-audit row.

Two passes, in this order, over the question and over the executed SQL:

1. ``scrub_counted`` removes secret-shaped values. Pattern based and best effort:
   it cannot recognise an arbitrary string as a secret, only the shapes below.
2. ``mask_pii_counted`` passes the text through the same DMS masker the customer
   envelope uses (``dms_core.pii``), so the audit export holds no personal data
   the envelope masks. Secrets go first: masking turns ``ss@db.internal`` into a
   mask token, which would break a ``scheme://user:pass/ss@host`` match.

Matching runs on a normalised view of the text (NFKC, lookalike letters folded,
zero-width characters and /* */ comments dropped, ``%3D``-style escapes decoded) so
that ``pass/**/word=``, a Cyrillic ``a``, a fullwidth ``=`` or ``password%3D`` do
not hide a pair. Replacement is made on the ORIGINAL text, so what is kept is not
altered.

Two failure modes bound the patterns. A leak is a secret left in the record. An
over-redaction is ordinary text or SQL rewritten for good, which corrupts the
audit record at write time. So a prose ``word: value`` needs a token-looking value
or an explicit assignment, patterns need a left word boundary, and nothing here
rewrites an SQL identifier, number or bare column comparison: in SQL, only the
contents of quoted literals and of comments are read as prose.
"""

from __future__ import annotations

import re
import unicodedata

from dms_core.pii import MASK_TOKEN_RE, fail_closed_mask_payload

REDACTED = "[redacted]"

_INVISIBLE = frozenset("\u200b\u200c\u200d\u200e\u200f\u2060\u00ad\ufeff")
#: Letters from other scripts that read as Latin ones, for the words below.
_CONFUSABLES = {
    **dict.fromkeys("\u0430\u03b1", "a"),
    **dict.fromkeys("\u0435\u0454", "e"),
    **dict.fromkeys("\u043e\u03bf", "o"),
    **dict.fromkeys("\u0440\u03c1", "p"),
    **dict.fromkeys("\u0441\u03f2", "c"),
    "\u0445": "x",
    "\u0443": "y",
    "\u043a": "k",
    "\u043c": "m",
    "\u0442": "t",
    **dict.fromkeys("\u0456\u0131", "i"),
    "\u0455": "s",
    "\u0458": "j",
    "\u0501": "d",
    "\u051d": "w",
    "\u04bb": "h",
    "\u0261": "g",
    "\u043d": "h",
    "\u03bd": "v",
}
#: Percent escapes that hide the punctuation of a pair (key%3Dvalue%26next).
_PERCENT = {
    "20": " ",
    "22": '"',
    "26": "&",
    "27": "'",
    "2c": ",",
    "3a": ":",
    "3b": ";",
    "3d": "=",
    "7b": "{",
    "7d": "}",
}

_NAME_CORE = (
    r"(?:pass(?:word|wd|phrase)|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"private[_-]?key|credentials?)"
)
_FAMILY = re.compile(r"(?i)pass(?:word|wd|phrase)|pwd")
_NAME = rf"(?<![A-Za-z0-9])[A-Za-z0-9_.-]{{0,40}}?{_NAME_CORE}[A-Za-z0-9_]*"

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S
)
_KEY_BODY = re.compile(r"(?<!\S)(?:[A-Za-z0-9+/]{40,}={0,2}[ \t]*\r?\n)+[A-Za-z0-9+/]{4,}={0,2}")
_PROVIDER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?:sk-(?:ant-)?|gsk_|ov_|sk_live_|pk_live_|rk_live_)[A-Za-z0-9_-]{16,}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r")"
)
#: scheme://user:PASSWORD@host - the password runs to the LAST @ of the token, so a
#: password holding "/" or "@" is covered. ``host:8080/x?e=a@b`` is a port, not a pair.
#: The second form is the same URL after the PII masker turned ``ss@host`` into a
#: mask token: the password then runs through the token.
_URL_PASSWORD = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@]+:"
    r"(?:(?P<v>(?!\d{1,5}(?:[/?#]|$))\S+)@(?=[^\s@]+)"
    r"|(?P<m>\S*?DMSMASK_[a-z]+_\d{2,}))"
)
_AUTH_HEADER = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?"
    r"(?:(?:basic|bearer|digest|negotiate|token)\s+(?P<v1>[A-Za-z0-9._~+/=:-]{6,})"
    r"|(?P<v2>(?=[A-Za-z0-9._~+/=:-]*[\d+/=_-])[A-Za-z0-9._~+/=:-]{8,}))"
)
_BEARER = re.compile(
    r"(?i)(?<![A-Za-z0-9])bearer\s+"
    r"(?P<v>[A-Za-z0-9._~+/=-]{16,}|(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{8,})"
)
_COOKIE = re.compile(r"(?i)(?<![A-Za-z0-9])(?:set-)?cookie\s*:\s*(?P<v>[A-Za-z0-9_.-]+=[^\r\n]*)")
_CURL_USER = re.compile(r"(?i)\bcurl\b[^\n]*?\s(?:-u|--user)(?:\s+|=)[\"']?(?P<v>[^\s\"']+)")
#: CREATE USER .. WITH PASSWORD 'x', ALTER USER .. PASSWORD 'x', IDENTIFIED BY 'x',
#: SET PASSWORD = PASSWORD('x'): the literal after the keyword, no operator needed.
_SQL_PASSWORD = re.compile(
    r"(?i)\b(?:(?:with\s+(?:(?:un)?encrypted\s+)?)?(?:password|passwd|pwd)"
    r"|identified\s+(?:with\s+\w+\s+)?by)\s*(?:=\s*)?(?:(?:old_)?password\s*\(\s*)?"
    r"'(?P<v>(?:[^']|'')+)'"
)
_ASSIGN = re.compile(
    rf"(?P<name>{_NAME})"
    r"(?:"
    r"(?P<op>(?:\\?[\"'])?\s*(?::=|=>|==|!=|<>|[:=])\s*)"
    r"|(?P<wop>\s+(?:like|ilike|rlike|regexp)\s+)"
    r"|(?P<nop>\s+(?:is|was)\s+(?!(?:not|null|true|false|pending|empty|required|missing|set)\b))"
    r")"
    r"(?:(?P<eq>\\[\"'])(?P<ev>(?:(?!(?P=eq)).)*)(?P=eq)"
    r"|(?P<q>[\"'])(?P<qv>(?:\\.|(?!(?P=q)).)*)(?P=q)"
    r"|(?P<uv>[^\s,;&)}\]\"'`]+))",
    re.I | re.S,
)
_WORD = re.compile(r"[ \t]+((?:[^\s.?!,;]|[.?!](?!\s|$))+)")
_BLOB = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=_-])")
_NOT_A_VALUE = re.compile(r"(?i)^(?:null|none|nil|true|false|undefined|\*+|\?|%s|\$\{.*\}|<.*>)$")
_SQL_LITERAL = re.compile(r"'(?:[^']|'')*'")
_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_REDACTED_TOKEN = re.compile(r"\[redacted\]")

#: Words that end a passphrase: a value that runs into one of these is prose.
_STOP = frozenset(
    "a an and or the this that these those for of to in on at by with from is are was were be "
    "been it its as per not no yes what who when where why how which show list give get tell "
    "me please my our your their we you they i if then than so but also too because while "
    "about into over under between each every all any some many more most less least".split()
)

_PUNCT_SECRET = frozenset("!@#$%^&*_+=/~")


def _view(text: str, *, drop_block_comments: bool) -> tuple[str, list[int], list[int]]:
    """Normalised text for matching, plus the original [start, end) of each character."""
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    i, n = 0, len(text)
    while i < n:
        if drop_block_comments and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j != -1:
                i = j + 2
                continue
        ch = text[i]
        if ch == "%" and i + 2 < n and text[i + 1 : i + 3].lower() in _PERCENT:
            chars.append(_PERCENT[text[i + 1 : i + 3].lower()])
            starts.append(i)
            ends.append(i + 3)
            i += 3
            continue
        if ch in _INVISIBLE:
            i += 1
            continue
        for c in unicodedata.normalize("NFKC", ch):
            chars.append(_CONFUSABLES.get(c, c))
            starts.append(i)
            ends.append(i + 1)
        i += 1
    return "".join(chars), starts, ends


def _classes(value: str) -> int:
    return sum(
        (
            any(c.islower() for c in value),
            any(c.isupper() for c in value),
            any(c.isdigit() for c in value),
            any(c in _PUNCT_SECRET for c in value),
        )
    )


def _token_looking(value: str) -> bool:
    """Long enough and mixed enough to be a credential, not a word or a number."""
    return len(value) >= 8 and not value.isdigit() and _classes(value) >= 2


def _secret_like(value: str) -> bool:
    """Shorter than a token but plainly not prose: letters mixed with digits."""
    has_digit = any(c.isdigit() for c in value)
    has_alpha = any(c.isalpha() for c in value)
    return (has_digit and has_alpha and len(value) >= 6) or _token_looking(value)


def _blob_like(value: str) -> bool:
    """Base64-shaped and not a word: mixed case, and digits or base64 punctuation."""
    digits = sum(c.isdigit() for c in value)
    return (
        any(c.isupper() for c in value)
        and any(c.islower() for c in value)
        and (digits >= 2 or any(c in "+/=" for c in value))
    )


def _words_after(v: str, end: int, first: str) -> int:
    """End of the value after ``first``: further words that belong to the secret.

    A passphrase is plain words ("correct horse battery staple"); take following
    words until a stop word, a number or an assignment. A credential with digits or
    symbols is one word, so only a following word that is itself credential-like
    joins it (``password = FAKEx9one twoFAKEx9``). Never more than seven.
    """
    plain = first.isalpha()
    words = 0
    while words < 7:
        nxt = _WORD.match(v, end)
        if not nxt:
            break
        w = nxt.group(1)
        if "=" in w or ":" in w:
            break
        if plain:
            if w.lower().strip(".,;:!?") in _STOP or w.isdigit():
                break
        elif not _secret_like(w):
            break
        end, words = nxt.end(), words + 1
    return end


def _literal_spans(v: str, a: int, b: int) -> list[tuple[int, int]]:
    """Prose ranges inside the SQL literal ``v[a:b]``, in ``v`` coordinates.

    ``''`` inside a literal is one quote, so the contents are read with it collapsed
    (``LIKE ''x''`` is ``LIKE 'x'``) and the ranges mapped back.
    """
    if b <= a:
        return []
    inner = v[a:b]
    chars: list[str] = []
    origin: list[int] = []
    i = 0
    while i < len(inner):
        if inner.startswith("''", i):
            chars.append("'")
            origin.append(a + i)
            i += 2
        else:
            chars.append(inner[i])
            origin.append(a + i)
            i += 1
    out: list[tuple[int, int]] = []
    for s, e in _spans("".join(chars), sql=False):
        last = origin[e - 1]
        out.append((origin[s], last + (2 if inner.startswith("''", last - a) else 1)))
    return out


def _spans(v: str, *, sql: bool) -> list[tuple[int, int]]:
    """View ranges to redact.

    ``sql=True`` is for executed SQL: identifiers, numbers and bare column
    comparisons are left alone, and the contents of every quoted literal and of every
    comment are read as prose (the ``sql=False`` rules).
    """
    out: list[tuple[int, int]] = []
    if sql:
        for m in _SQL_LITERAL.finditer(v):
            out.extend(_literal_spans(v, m.start() + 1, m.end() - 1))
        for m in _SQL_COMMENT.finditer(v):
            a, b = m.span()
            out.extend((a + s, a + e) for s, e in _spans(v[a:b], sql=False))

    for rx in (_PRIVATE_KEY_BLOCK, _KEY_BODY, _PROVIDER_TOKEN):
        out.extend(m.span() for m in rx.finditer(v))
    for m in _URL_PASSWORD.finditer(v):
        g = "v" if m.group("v") is not None else "m"
        if m.group(g) != REDACTED:
            out.append(m.span(g))
    for m in _AUTH_HEADER.finditer(v):
        out.append(m.span("v1" if m.group("v1") else "v2"))
    out.extend(m.span("v") for m in _BEARER.finditer(v))
    out.extend(m.span("v") for m in _COOKIE.finditer(v))
    out.extend(m.span("v") for m in _CURL_USER.finditer(v))
    for m in _SQL_PASSWORD.finditer(v):
        if not m.group("v").startswith(REDACTED):
            out.append(m.span("v"))

    for m in _ASSIGN.finditer(v):
        span = _assignment(v, m, sql=sql)
        if span is not None:
            out.append(span)

    if not sql:  # in SQL the literal and comment contents were read above
        for m in _BLOB.finditer(v):
            if _blob_like(m.group()):
                out.append(m.span())
    # A value that is already "[redacted]" is not a second secret: a second pass over
    # clean text changes nothing.
    return [(a, b) for a, b in out if not v.startswith(REDACTED, a)]


def _assignment(v: str, m: re.Match[str], *, sql: bool) -> tuple[int, int] | None:
    name = m.group("name")
    family = bool(_FAMILY.search(name))
    op = m.group("op") or ""
    json_style = op.lstrip()[:1] in ('"', "'", "\\")
    explicit = json_style or op.strip(" \t\"'\\") in ("=", ":=", "=>", "==", "!=", "<>")
    prose_colon = bool(op) and not explicit
    nop = bool(m.group("nop"))

    quoted_group = "ev" if m.group("eq") else "qv" if m.group("q") else None
    if quoted_group:
        inner = m.group(quoted_group)
        if not inner or inner.startswith(REDACTED):
            return None
        if nop and not _token_looking(inner):
            return None
        if prose_colon and not family and not _token_looking(inner):
            return None
        if sql and not family and not _secret_like(inner):
            return None
        return m.span(quoted_group)

    val = m.group("uv")
    start = m.start("uv")
    if not val or v.startswith(REDACTED, start) or _NOT_A_VALUE.match(val):
        return None
    if m.group("wop") or sql:
        return None  # LIKE wants a quoted literal; in SQL a bare word is a column or a number
    end = m.end("uv")
    while end > start and v[end - 1] in ".?!" and (end == len(v) or v[end].isspace()):
        end -= 1
    first = v[start:end]
    if nop:
        return (start, end) if _token_looking(first) else None
    if prose_colon:
        if family:
            if _secret_like(first):
                return start, _words_after(v, end, first)
            tail = _words_after(v, end, first) if first.isalpha() else end
            # a passphrase is at least three plain words; "reset required for 12" is prose
            return (start, tail) if tail > end and len(v[start:tail].split()) >= 3 else None
        return (start, end) if _token_looking(first) else None
    return (start, _words_after(v, end, first)) if family else (start, end)


def scrub_counted(text: str | None, *, sql: bool = False) -> tuple[str, int]:
    """Remove secret-shaped values. Returns the clean text and how many were replaced.

    ``sql=True`` is for executed SQL.
    """
    original = text or ""
    if not original:
        return "", 0
    regions: list[tuple[int, int]] = []
    for drop in (False, True):
        view, starts, ends = _view(original, drop_block_comments=drop)
        for s, e in _spans(view, sql=sql):
            if e > s:
                regions.append((starts[s], ends[e - 1]))
    if not regions:
        return original, 0
    regions.sort()
    merged: list[list[int]] = []
    for s, e in regions:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out, last = [], 0
    for s, e in merged:
        out.append(original[last:s])
        out.append(REDACTED)
        last = e
    out.append(original[last:])
    return "".join(out), len(merged)


def scrub(text: str | None, *, sql: bool = False) -> str:
    return scrub_counted(text, sql=sql)[0]


def mask_pii_counted(text: str) -> tuple[str, int]:
    """Mask personal data exactly as the customer envelope does. Returns text and a count.

    Fails closed like the envelope: a masker error blanks the text.
    """
    if not text:
        return text, 0
    masked = str(fail_closed_mask_payload(text=text)["text"])
    return masked, max(0, len(MASK_TOKEN_RE.findall(masked)) - len(MASK_TOKEN_RE.findall(text)))


def has_sql_statement(sql: str | None) -> bool:
    """True when ``sql`` holds a statement, not nothing or a comment-only placeholder."""
    return bool(_SQL_COMMENT.sub("", sql or "").strip())


def safe_cut(text: str, cap: int) -> str:
    """``text[:cap]``, without splitting a ``[redacted]`` or a mask token in two."""
    if len(text) <= cap:
        return text
    for rx in (_REDACTED_TOKEN, MASK_TOKEN_RE):
        for m in rx.finditer(text):
            if m.start() < cap < m.end():
                return text[: m.end()]
    return text[:cap]
