"""BANK-02 (dms#269): what may be written into an ask-audit row.

Two passes, in this order, over the question and over the executed SQL:

1. ``scrub_counted`` removes secret-shaped values. Pattern based and best effort:
   it cannot recognise an arbitrary string as a secret, only the shapes below.
2. ``mask_pii_counted`` passes the text through the same DMS masker the customer
   envelope uses (``dms_core.pii``), so the audit export holds no personal data
   the envelope masks. Secrets go first: masking turns ``ss@db.internal`` into a
   mask token, which would otherwise break a ``scheme://user:pass/ss@host`` match.

Matching runs on a normalised view of the text (NFKC, lookalike letters folded,
zero-width characters and block comments dropped, percent escapes such as ``%3D``
and JSON unicode escapes decoded) so that ``pass/**/word=``, a Cyrillic ``a``, a
fullwidth ``=``, ``password%3D`` or a JSON key spelled with a unicode escape do not
hide a pair. Replacement is made on the ORIGINAL text, so what is kept is not
altered.

Two failure modes bound the patterns. A leak is a secret left in the record. An
over-redaction is ordinary text or SQL rewritten for good, which corrupts the
audit record at write time. So a prose ``word: value`` needs a token-looking value
or an explicit assignment, patterns need a left word boundary, and nothing here
rewrites an SQL identifier, number or bare column comparison: in SQL, only the
contents of quoted literals and of comments are read as prose.

How the scrub is bounded (ReDoS). This runs on the request path of every ask and
Python's ``re`` holds the GIL, so a pattern that backtracks stalls the whole API
process. Every pattern is therefore linear: its alternatives are disjoint, its
repeats are possessive (``*+``, ``++``) or bounded, and a scan that needs "the
next block-comment close" or "the last at-sign" is a ``str.find`` over a bounded
token, not a lazy regex. The input is bounded twice: the caller cuts to a window
(the cap plus a margin) before any pattern runs, and ``scrub_counted`` itself
refuses to read past ``MAX_SCRUB_CHARS``. ``tests/test_bank_02_scrub_redos.py``
fuzzes every pattern in this module with adversarial input at the production
window sizes and at 1 MB, and checks that time grows linearly.
"""

from __future__ import annotations

import re
import unicodedata

from dms_core.pii import MASK_TOKEN_RE, fail_closed_mask_payload

REDACTED = "[redacted]"

#: ``scrub_counted`` reads no further than this, whatever it is given. Callers cut
#: to their own, smaller window first; this is the backstop.
MAX_SCRUB_CHARS = 64_000
#: ``has_sql_statement`` reads no further than this.
_STATEMENT_SCAN_CAP = 100_000

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
_HEX = frozenset("0123456789abcdefABCDEF")

_NAME_CORE = (
    r"(?:pass(?:word|wd|phrase)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"private[_-]?key|account[_-]?key|shared[_-]?access[_-]?key|credentials?)"
)
_FAMILY = re.compile(r"(?i)pass(?:word|wd|phrase)?|pwd")
#: The bare word ``pass`` as a whole name (DB_PASS, --pass): read only as an explicit assignment.
_BARE_PASS = re.compile(r"(?i)(?:^|[^A-Za-z0-9])pass$")
#: A credential name is a core word with up to 40 name characters on either side, starting
#: at a word start. It is found from the core word outward, in a few C-level steps per
#: occurrence, because a single regex that lets the prefix and suffix float around the core
#: word re-tries the core at every position and is slow on ``password_password_...``.
_CORE_RX = re.compile(r"(?i)" + _NAME_CORE)
_NAME_RUN = re.compile(r"[A-Za-z0-9_.-]*+")
_NAME_SEP = re.compile(r"[_.-]")
_NAME_TRAIL = re.compile(r"[A-Za-z0-9_]{0,40}+")
_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----.*?(?:-----END [A-Z ]{0,40}PRIVATE KEY-----|\Z)",
    re.S,
)
_KEY_BODY = re.compile(
    r"(?<!\S)(?:[A-Za-z0-9+/]{40,}+={0,2}+[ \t]{0,8}+\r?\n)+[A-Za-z0-9+/]{4,}+={0,2}+"
)
_PROVIDER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?:sk-(?:ant-)?|gsk_|ov_)[A-Za-z0-9_-]{16,}+"
    r"|(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,}+"
    r"|whsec_[A-Za-z0-9]{16,}+"
    r"|glpat-[A-Za-z0-9_-]{20,}+"
    r"|shpat_[A-Fa-f0-9]{32}"
    r"|SG\.[A-Za-z0-9_-]{16,}+\.[A-Za-z0-9_-]{16,}+"
    r"|(?:hf|npm)_[A-Za-z0-9]{30,}+"
    r"|AIza[0-9A-Za-z_-]{20,}+"
    r"|eyJ[A-Za-z0-9_-]{8,}+\.[A-Za-z0-9_-]{8,}+\.[A-Za-z0-9_-]{4,}+"
    r"|gh[pousr]_[A-Za-z0-9]{20,}+|github_pat_[A-Za-z0-9_]{20,}+"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}+"
    r"|AKIA[0-9A-Z]{16}"
    r")"
)
#: scheme://user: - the start of a URL password. The password itself is found in
#: Python (see ``_url_password_spans``): it runs to the LAST at-sign of the token,
#: and a lazy regex for that is quadratic on repeated prefixes.
_URL_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,30}://[^\s:/@]{1,256}+:")
_TOKEN_RUN = re.compile(r"\S{1,2000}+")
_PORT_START = re.compile(r"\d{1,5}(?:[/?#]|$)")
_AUTH_HEADER = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:proxy-)?authorization[\"']?\s{0,16}[:=]\s{0,16}[\"']?"
    r"(?:(?:basic|bearer|digest|negotiate|token)\s{1,16}(?P<v1>[A-Za-z0-9._~+/=:-]{6,512}+)"
    r"|(?P<v2>(?=[A-Za-z0-9._~+/=:-]{0,512}[\d+/=_-])[A-Za-z0-9._~+/=:-]{8,512}+))"
)
_BEARER = re.compile(
    r"(?i)(?<![A-Za-z0-9])bearer\s{1,16}"
    r"(?P<v>[A-Za-z0-9._~+/=-]{16,512}+|(?=[A-Za-z0-9._~+/=-]{0,512}\d)[A-Za-z0-9._~+/=-]{8,512}+)"
)
_COOKIE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:set-)?cookie\s{0,16}:\s{0,16}"
    r"(?P<v>[A-Za-z0-9_.-]{1,128}+=[^\r\n]{0,2048}+)"
)
_CURL_USER = re.compile(
    r"(?i)\bcurl\b[^\n]{0,256}?\s(?:-u|--user)(?:\s{1,16}|=)[\"']?(?P<v>[^\s\"']{1,256}+)"
)
#: CREATE USER .. WITH PASSWORD 'x', ALTER USER .. PASSWORD 'x', IDENTIFIED BY 'x',
#: SET PASSWORD [FOR user] = PASSWORD('x'): the literal after the keyword.
_SQL_PASSWORD = re.compile(
    r"(?i)\b(?:(?:with\s{1,16}(?:(?:un)?encrypted\s{1,16})?)?(?:password|passwd|pwd)"
    r"(?:\s{1,16}for\s{1,16}[^\s=]{1,100})?"
    r"|identified\s{1,16}(?:with\s{1,16}\w{1,32}\s{1,16})?by)"
    r"\s{0,16}(?:=\s{0,16})?(?:(?:old_)?password\s{0,16}\(\s{0,16})?"
    r"'(?P<v>(?:[^']|'')++)'"
)
#: .netrc: machine h login u password P (any whitespace between words).
_NETRC = re.compile(r"(?i)\blogin\s{1,16}\S{1,128}+\s{1,16}password\s{1,16}(?P<v>\S{1,256}+)")
#: Azure SAS signature in a query string.
_SAS_SIG = re.compile(r"(?i)(?<![A-Za-z0-9_])sig=(?P<v>[^\s&;\"']{8,256}+)")
#: .pgpass: host:port:database:user:PASSWORD, as a whole word.
_PGPASS = re.compile(
    r"(?<!\S)(?=[^\s:]{0,255}[A-Za-z*])[^\s:]{1,255}+:(?:\d{1,5}|\*):[^\s:]{1,255}+:"
    r"[^\s:]{1,255}+:(?P<v>\S{1,256}+)(?!\S)"
)
#: mysql -pSECRET / -p'SECRET' (the password is attached to the flag).
_MYSQL_P = re.compile(
    r"(?i)\b(?:mysql|mysqldump|mysqladmin|mysqlpump|mysqlimport|mariadb|mariadb-dump)\b"
    r"[^\n]{0,256}?\s-p(?P<v>'[^']{0,256}+'|\"[^\"]{0,256}+\"|[^\s'\"-]\S{0,255}+)"
)
_SSHPASS = re.compile(r"(?i)\bsshpass\b[^\n]{0,64}?\s-p\s{0,16}(?P<v>\S{1,256}+)")
_REDIS_A = re.compile(r"(?i)\bredis-cli\b[^\n]{0,256}?\s-a\s{1,16}(?P<v>\S{1,256}+)")
#: What follows a credential name: an operator (or LIKE, or "is"), then a value.
_ASSIGN_TAIL = re.compile(
    r"(?:"
    r"(?P<op>(?:\\?[\"'])?\s{0,16}(?::=|=>|==|!=|<>|[:=])\s{0,16})"
    r"|(?P<wop>\s{1,16}(?:like|ilike|rlike|regexp)\s{1,16})"
    r"|(?P<nop>\s{1,16}(?:is|was)\s{1,16}"
    r"(?!(?:not|null|true|false|pending|empty|required|missing|set)\b))"
    r")"
    r"(?:(?P<eq>\\[\"'])(?P<ev>(?:(?!(?P=eq)).)*+)(?P=eq)"
    r"|\"(?P<qd>(?:\\.|[^\\\"])*+)\""
    r"|'(?P<qs>(?:\\.|[^\\'])*+)'"
    r"|(?P<uv>[^\s,;&)}\]\"'`]{1,512}+))",
    re.I | re.S,
)
#: The rest of an unquoted value that ``_ASSIGN_TAIL`` stopped reading at 512 characters.
#: The tail regex is bounded because it is tried at every credential word, and an
#: unbounded run read at each of thousands of them is quadratic; the rest of the run is
#: read here, once, for a value that is accepted.
_VALUE_REST = re.compile(r"[^\s,;&)}\]\"'`]*+")
_VALUE_CAP = 512
_WORD = re.compile(r"[ \t]{1,16}+((?:[^\s.?!,;]|[.?!](?!\s|$)){1,256}+)")
_BLOB = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/]{24,}+={0,2}+(?![A-Za-z0-9+/=_-])")
_NOT_A_VALUE = re.compile(r"(?i)^(?:null|none|nil|true|false|undefined|\*+|\?|%s|\$\{.*\}|<.*>)$")
_SQL_LITERAL = re.compile(r"'(?:[^']++|'')*+'")
_REDACTED_TOKEN = re.compile(r"\[redacted\]")

#: Words that end a passphrase: a value that runs into one of these is prose.
_STOP = frozenset(
    "a an and or the this that these those for of to in on at by with from is are was were be "
    "been it its as per not no yes what who when where why how which show list give get tell "
    "me please my our your their we you they i if then than so but also too because while "
    "about into over under between each every all any some many more most less least".split()
)

_PUNCT_SECRET = frozenset("!@#$%^&*_+=/~")
_BASE64_PUNCT = frozenset("+/=")


def _view(text: str, *, drop_block_comments: bool) -> tuple[str, list[int], list[int]]:
    """Normalised text for matching, plus the original [start, end) of each character."""
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []

    def put(c: str, a: int, b: int) -> None:
        for norm in unicodedata.normalize("NFKC", c):
            chars.append(_CONFUSABLES.get(norm, norm))
            starts.append(a)
            ends.append(b)

    i, n = 0, len(text)
    no_close = False  # once a block-comment open has no close after it, no later one has either
    while i < n:
        ch = text[i]
        if drop_block_comments and ch == "/" and not no_close and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j != -1:
                i = j + 2
                continue
            no_close = True
        if ch == "%" and i + 2 < n:
            rep = _PERCENT.get(text[i + 1 : i + 3].lower())
            if rep is not None:
                put(rep, i, i + 3)
                i += 3
                continue
        if (
            ch == "\\"
            and i + 5 < n
            and text[i + 1] == "u"
            and all(c in _HEX for c in text[i + 2 : i + 6])
        ):
            put(chr(int(text[i + 2 : i + 6], 16)), i, i + 6)
            i += 6
            continue
        if ch in _INVISIBLE:
            i += 1
            continue
        put(ch, i, i + 1)
        i += 1
    return "".join(chars), starts, ends


def _comment_spans(v: str) -> list[tuple[int, int]]:
    """Line comments and block comments, left to right, in one linear pass.

    An unterminated block-comment open is not a comment. Once one has no close after
    it, none after it has either, so the search is not repeated (a lazy regex repeats
    it for every open and is quadratic).
    """
    out: list[tuple[int, int]] = []
    n = len(v)
    nd = v.find("--")
    nb = v.find("/*")
    no_close = False
    while nd != -1 or nb != -1:
        if nb == -1 or (nd != -1 and nd < nb):
            end = v.find("\n", nd)
            end = n if end == -1 else end
            out.append((nd, end))
            i = end
        else:
            end = -1 if no_close else v.find("*/", nb + 2)
            if end == -1:
                no_close = True
                i = nb + 2
            else:
                out.append((nb, end + 2))
                i = end + 2
        if nd != -1 and nd < i:
            nd = v.find("--", i)
        if nb != -1 and nb < i:
            nb = v.find("/*", i)
    return out


def _url_password_spans(v: str) -> list[tuple[int, int]]:
    """Ranges of ``scheme://user:PASSWORD@host`` passwords.

    The password runs to the LAST at-sign of the whitespace-delimited token, so one
    holding a slash or an at-sign is covered. ``host:8080/x?e=a@b`` is a port, not a
    pair. If the PII masker already turned the ``ss@host`` tail into a mask token
    there is no at-sign left, and the password then runs through that token.
    """
    out: list[tuple[int, int]] = []
    for m in _URL_PREFIX.finditer(v):
        k = m.end()
        tok = _TOKEN_RUN.match(v, k)
        if not tok:
            continue
        t = tok.group()
        if _PORT_START.match(t):
            continue
        at = t.rfind("@")
        if 0 < at < len(t) - 1:
            if t[:at] != REDACTED:
                out.append((k, k + at))
            continue
        mask = MASK_TOKEN_RE.search(t)
        if mask and t[: mask.end()] != REDACTED:
            out.append((k, k + mask.end()))
    return out


def _classes(value: str) -> int:
    # map(str.method, ...) iterates in C; a generator expression here is the slowest
    # line in the scrub on a long value.
    return sum(
        (
            any(map(str.islower, value)),
            any(map(str.isupper, value)),
            any(map(str.isdigit, value)),
            not _PUNCT_SECRET.isdisjoint(value),
        )
    )


def _token_looking(value: str) -> bool:
    """Long enough and mixed enough to be a credential, not a word or a number."""
    return len(value) >= 8 and not value.isdigit() and _classes(value) >= 2


def _secret_like(value: str) -> bool:
    """Shorter than a token but plainly not prose: letters mixed with digits."""
    has_digit = any(map(str.isdigit, value))
    has_alpha = any(map(str.isalpha, value))
    return (has_digit and has_alpha and len(value) >= 6) or _token_looking(value)


def _blob_like(value: str) -> bool:
    """Base64-shaped and not a word: mixed case, and digits or base64 punctuation."""
    digits = sum(map(str.isdigit, value))
    return (
        any(map(str.isupper, value))
        and any(map(str.islower, value))
        and (digits >= 2 or not _BASE64_PUNCT.isdisjoint(value))
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

    A doubled quote inside a literal is one quote, so the contents are read with it
    collapsed and the ranges mapped back.
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
        for a, b in _comment_spans(v):
            out.extend((a + s, a + e) for s, e in _spans(v[a:b], sql=False))

    for rx in (_PRIVATE_KEY_BLOCK, _KEY_BODY, _PROVIDER_TOKEN):
        out.extend(m.span() for m in rx.finditer(v))
    out.extend(_url_password_spans(v))
    for m in _SQL_PASSWORD.finditer(v):
        if not m.group("v").startswith(REDACTED):
            out.append(m.span("v"))

    if not sql:  # in SQL, these are read inside literals and comments only (above)
        for m in _AUTH_HEADER.finditer(v):
            out.append(m.span("v1" if m.group("v1") else "v2"))
        for rx in (_BEARER, _COOKIE, _CURL_USER, _NETRC, _SAS_SIG, _PGPASS):
            out.extend(m.span("v") for m in rx.finditer(v))
        for rx in (_MYSQL_P, _SSHPASS, _REDIS_A):
            out.extend(m.span("v") for m in rx.finditer(v))

    seen: set[tuple[int, int]] = set()
    accepted_to = 0  # a credential word inside a value already taken is that secret's own text
    for core in _CORE_RX.finditer(v):
        if core.start() < accepted_to:
            continue
        name_span = _name_around(v, core)
        if name_span is None or name_span in seen:
            continue
        seen.add(name_span)
        tail = _ASSIGN_TAIL.match(v, name_span[1])
        if tail is not None:
            span = _assignment(v, v[name_span[0] : name_span[1]], tail, sql=sql)
            if span is not None:
                out.append(span)
                accepted_to = max(accepted_to, span[1])

    if not sql:
        for m in _BLOB.finditer(v):
            if _blob_like(m.group()):
                out.append(m.span())
    # A value that is already "[redacted]" is not a second secret: a second pass over
    # clean text changes nothing.
    return [(a, b) for a, b in out if not v.startswith(REDACTED, a)]


def _match_end(rx: re.Pattern[str], text: str, pos: int) -> int:
    """Where ``rx`` (a pattern that can match the empty string) ends when tried at ``pos``."""
    m = rx.match(text, pos)
    return m.end() if m else pos


def _name_around(v: str, core: re.Match[str]) -> tuple[int, int] | None:
    """The credential name that contains the core word ``core``, or None.

    Up to 40 name characters to the left, starting at a word start (not preceded by an
    alphanumeric), and up to 40 to the right.
    """
    c0, c1 = core.span()
    lo = max(0, c0 - 40)
    run = _match_end(_NAME_RUN, v[lo:c0][::-1], 0)  # name characters just left of the core
    start = c0 - run
    if start > 0 and v[start - 1] in _ALNUM:  # the 40-character window cut an alphanumeric run
        sep = _NAME_SEP.search(v, start, c0)
        if sep is None:
            return None
        start = sep.end()
    return start, _match_end(_NAME_TRAIL, v, c1)


def _assignment(v: str, name: str, m: re.Match[str], *, sql: bool) -> tuple[int, int] | None:
    family = bool(_FAMILY.search(name))
    op = m.group("op") or ""
    json_style = op.lstrip()[:1] in ('"', "'", "\\")
    explicit = json_style or op.strip(" \t\"'\\") in ("=", ":=", "=>", "==", "!=", "<>")
    prose_colon = bool(op) and not explicit
    nop = bool(m.group("nop"))
    if _BARE_PASS.search(name) and not explicit:
        return None  # "pass: fail ratio" is prose; DB_PASS=x is not

    quoted_group = (
        "ev"
        if m.group("eq")
        else "qd"
        if m.group("qd") is not None
        else "qs"
        if m.group("qs") is not None
        else None
    )
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
    span = _unquoted_span(v, start, m.end("uv"), family, prose_colon, nop)
    if span is not None and len(val) >= _VALUE_CAP:
        # the tail regex stopped at its bound: an accepted value runs to the end of the run
        span = (span[0], max(span[1], _match_end(_VALUE_REST, v, m.end("uv"))))
    return span


def _unquoted_span(
    v: str, start: int, end: int, family: bool, prose_colon: bool, nop: bool
) -> tuple[int, int] | None:
    """The span of an unquoted value read from ``start`` to ``end``, or None if it is prose."""
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

    ``sql=True`` is for executed SQL. Input past ``MAX_SCRUB_CHARS`` is not read: it
    is dropped and the text says so, rather than being returned unscrubbed.
    """
    original = text or ""
    if not original:
        return "", 0
    dropped = 0
    if len(original) > MAX_SCRUB_CHARS:
        dropped = len(original) - MAX_SCRUB_CHARS
        original = original[:MAX_SCRUB_CHARS]
    regions: list[tuple[int, int]] = []
    for drop in (False, True):
        view, starts, ends = _view(original, drop_block_comments=drop)
        for s, e in _spans(view, sql=sql):
            if e > s:
                regions.append((starts[s], ends[e - 1]))
    merged: list[list[int]] = []
    for s, e in sorted(regions):
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
    clean = "".join(out)
    if dropped:
        return f"{clean}...[scrub window: {dropped} chars dropped]", len(merged) + 1
    return clean, len(merged)


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
    """True when ``sql`` holds a statement, not nothing or a comment-only placeholder.

    One linear scan that stops at the first character outside whitespace and
    comments, over at most ``_STATEMENT_SCAN_CAP`` characters. An unterminated block
    comment is text, so it counts as a statement. If the scanned prefix is all comment
    and more text follows, that text is assumed to be a statement.
    """
    s = (sql or "")[:_STATEMENT_SCAN_CAP]
    i, n = 0, len(s)
    while i < n:
        if s[i].isspace():
            i += 1
        elif s.startswith("--", i):
            j = s.find("\n", i)
            if j == -1:
                i = n
                break
            i = j + 1
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            if j == -1:
                return True
            i = j + 2
        else:
            return True
    return len(sql or "") > _STATEMENT_SCAN_CAP


def safe_cut(text: str, cap: int) -> str:
    """``text[:cap]``, without splitting a ``[redacted]`` or a mask token in two."""
    if len(text) <= cap:
        return text
    for rx in (_REDACTED_TOKEN, MASK_TOKEN_RE):
        for m in rx.finditer(text):
            if m.start() < cap < m.end():
                return text[: m.end()]
    return text[:cap]
