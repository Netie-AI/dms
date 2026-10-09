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
import string
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
    r"(?:'(?P<v>(?:[^']|'')++)'|'(?P<v2>[^\s;]{1,256}+))"
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
#: A bare or quoted argument (quotes kept, so they are redacted with it). Bounded.
_ARG = r"(?:'[^']{1,256}+'|\"[^\"]{1,256}+\"|[^\s'\"-][^\s'\"]{0,255}+)"
#: ``--password X`` / ``--password=X`` and the other flags that take a secret as the next
#: word (vercel ``--token``, netlify ``--auth``, gpg ``--passphrase``). Read in Python:
#: the weak names (token, auth, ...) need a credential-looking value.
_CLI_FLAG = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])--(?P<n>password|passwd|passphrase|token|auth|auth-token"
    r"|access-token|api-key|apikey|secret|client-secret)(?:[ \t]{1,16}+|=)(?P<v>" + _ARG + r")"
)
_CLI_STRONG = frozenset({"password", "passwd", "passphrase"})
#: ssh-keygen -N PASSPHRASE / -P OLD_PASSPHRASE. An empty ``-N ""`` is no passphrase.
_SSH_KEYGEN = re.compile(
    r"(?i)\bssh-keygen\b[^\n]{0,256}?\s-[NP](?:[ \t]{1,16}+|=)?(?P<v>" + _ARG + r")"
)
#: openssl -pass pass:X, -passin pass:X, -passout pass:X (env: and file: are references).
_OPENSSL_PASS = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])-(?:pass|passin|passout|passwd|password)[ \t]{1,16}+pass:"
    r"(?P<v>'[^']{1,256}+'|\"[^\"]{1,256}+\"|[^\s'\"]{1,256}+)"
)
#: docker login -p X (``--password`` is a ``_CLI_FLAG``).
_DOCKER_LOGIN = re.compile(
    r"(?i)\b(?:docker|podman|buildah|skopeo|crane|oras|helm)\s{1,16}(?:registry\s{1,16})?login\b"
    r"[^\n]{0,256}?\s-p(?:[ \t]{1,16}+|=)(?P<v>" + _ARG + r")"
)
#: An HTTP Digest ``response="..."``: a hash of the password and a server nonce.
_DIGEST_RESPONSE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])response\s{0,4}=\s{0,4}[\"']?(?P<v>[A-Za-z0-9+/=_.~-]{8,256}+)"
)
_HEX32 = re.compile(r"(?i)[0-9a-f]{32,}+\Z")
#: SMTP ``AUTH PLAIN <base64>`` / ``AUTH LOGIN <base64>`` (the credentials, encoded).
_SMTP_AUTH = re.compile(
    r"(?i)(?<![A-Za-z0-9_])auth[ \t]{1,8}(?:plain|login|cram-md5|xoauth2)"
    r"(?:[ \t]{1,8}+|[ \t]{0,8}+\r?\n[ \t]{0,8}+)(?P<v>[A-Za-z0-9+/]{8,512}+={0,2}+)"
)
#: The next whitespace-separated chunk of a PEM-style body.
_NEXT_CHUNK = re.compile(r"[ \t]{1,2}+(?P<w>[A-Za-z0-9+/]{4,256}+={0,2}+)(?![A-Za-z0-9+/=_-])")
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
    if (
        text.isascii()
        and "%" not in text
        and "\\" not in text
        and not (drop_block_comments and "/*" in text)
    ):
        # Nothing to normalise, decode or drop: the view is the text (the common case, and
        # the one that costs microseconds instead of a Python loop over every character).
        n = len(text)
        return text, list(range(n)), list(range(1, n + 1))
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
    if "''" not in inner:
        return [(a + s, a + e) for s, e in _spans(inner, sql=False)]
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


#: Where a SQL quoted literal, a quoted identifier or a comment can begin.
_SQL_OPEN = re.compile(r"'|\"|`|--|/\*")


def _close_quote(v: str, k: int, q: str) -> int:
    """Index of the quote that closes a ``q``-quoted run whose content starts at ``k``.

    A doubled quote is one quote inside the run. ``len(v)`` when the run is still open
    at the end of the text.
    """
    while True:
        j = v.find(q, k)
        if j == -1:
            return len(v)
        if v.startswith(q + q, j):
            k = j + 2
            continue
        return j


def _sql_regions(v: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """``(literals, comments)`` of SQL text, left to right, in one linear pass.

    A literal is the span between its quotes. A comment opener inside a literal or a
    quoted identifier is text, not a comment: ``SELECT '--' AS sep, token = t2.token``
    has one literal and no comment, so the column comparison is not read as prose. A
    literal still open at the end of the text (the window cut it) is read to the end:
    what is in it is data, so it is scanned as prose and not taken for code. An
    unterminated block-comment open is not a comment, and once one has no close after
    it none after it has either, so the search is not repeated (a lazy regex repeats
    it for every open and is quadratic).
    """
    lits: list[tuple[int, int]] = []
    comments: list[tuple[int, int]] = []
    n = len(v)
    i = 0
    no_close = False
    while True:
        m = _SQL_OPEN.search(v, i)
        if m is None:
            break
        tok, s = m.group(), m.start()
        if tok == "'":
            e = _close_quote(v, s + 1, "'")
            lits.append((s + 1, e))
            i = e + 1
        elif tok in ('"', "`"):
            i = _close_quote(v, s + 1, tok) + 1
        elif tok == "--":
            e = v.find("\n", s)
            e = n if e == -1 else e
            comments.append((s, e))
            i = e
        else:  # a block-comment open
            e = -1 if no_close else v.find("*/", s + 2)
            if e == -1:
                no_close = True
                i = s + 2
            else:
                comments.append((s, e + 2))
                i = e + 2
    return lits, comments


def _placeholder(value: str) -> bool:
    """A reference to a secret held elsewhere (``$TOKEN``, ``<token>``), not the secret."""
    return value.startswith("$") or bool(_NOT_A_VALUE.match(value))


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0] else value


def _cli_spans(v: str) -> list[tuple[int, int]]:
    """Secrets given on a command line: ``--token X``, ``ssh-keygen -N X``, ``openssl -pass
    pass:X``, ``docker login -p X``, a Digest ``response=``, SMTP ``AUTH PLAIN <b64>``."""
    out: list[tuple[int, int]] = []
    for m in _CLI_FLAG.finditer(v):
        inner = _unquote(m.group("v"))
        if not inner or inner.startswith(REDACTED) or _placeholder(inner):
            continue
        if m.group("n").lower() in _CLI_STRONG or _secret_like(inner):
            out.append(m.span("v"))
    for rx in (_SSH_KEYGEN, _OPENSSL_PASS, _DOCKER_LOGIN):
        for m in rx.finditer(v):
            inner = _unquote(m.group("v"))
            if inner and not inner.startswith(REDACTED) and not _placeholder(inner):
                out.append(m.span("v"))
    for m in _DIGEST_RESPONSE.finditer(v):
        val = m.group("v")
        context = v[max(0, m.start() - 600) : m.start()].lower()
        if _HEX32.match(val) or "digest" in context:
            out.append(m.span("v"))
    for m in _SMTP_AUTH.finditer(v):
        val = m.group("v")
        if _token_looking(val) and (
            len(val) >= 16 or not _BASE64_PUNCT.isdisjoint(val) or any(map(str.isdigit, val))
        ):
            out.append(m.span("v"))
    return out


def _blob_spans(v: str) -> list[tuple[int, int]]:
    """Base64-shaped blobs, and a key body that was split on spaces.

    A PEM body pasted with spaces for its line breaks is a run of chunks, and the last
    one is short or has no digit, so on its own it is not blob-shaped. A chunk next to a
    blob-shaped one is part of the same body; after a full-width (40+) chunk the short
    ones that follow are too.
    """
    out: list[tuple[int, int]] = []
    group: list[re.Match[str]] = []

    def flush() -> None:
        if not group or not any(_blob_like(m.group()) for m in group):
            group.clear()
            return
        out.extend(m.span() for m in group)
        end = group[-1].end()
        if group[-1].end() - group[-1].start() >= 40:
            while True:
                nxt = _NEXT_CHUNK.match(v, end)
                if nxt is None or not _chunk_like(nxt.group("w")):
                    break
                out.append(nxt.span("w"))
                end = nxt.end("w")
                if len(nxt.group("w")) < 40:
                    break
        group.clear()

    prev_end = -1
    for m in _BLOB.finditer(v):
        if group:
            gap = v[prev_end : m.start()]
            if not (0 < len(gap) <= 2 and not gap.strip(" \t")):
                flush()
        group.append(m)
        prev_end = m.end()
    flush()
    return out


def _chunk_like(word: str) -> bool:
    """A later chunk of a key body: digits or base64 punctuation, or mixed case."""
    return (
        any(map(str.isdigit, word))
        or not _BASE64_PUNCT.isdisjoint(word)
        or (len(word) >= 8 and any(map(str.isupper, word)) and any(map(str.islower, word)))
    )


def _spans(v: str, *, sql: bool) -> list[tuple[int, int]]:
    """View ranges to redact.

    ``sql=True`` is for executed SQL: identifiers, numbers and bare column
    comparisons are left alone, and the contents of every quoted literal and of every
    comment are read as prose (the ``sql=False`` rules).
    """
    out: list[tuple[int, int]] = []
    if sql:
        literals, comments = _sql_regions(v)
        for a, b in literals:
            out.extend(_literal_spans(v, a, b))
        for a, b in comments:
            out.extend((a + s, a + e) for s, e in _spans(v[a:b], sql=False))

    for rx in (_PRIVATE_KEY_BLOCK, _KEY_BODY, _PROVIDER_TOKEN):
        out.extend(m.span() for m in rx.finditer(v))
    out.extend(_url_password_spans(v))
    for m in _SQL_PASSWORD.finditer(v):
        group = "v" if m.group("v") is not None else "v2"
        if not m.group(group).startswith(REDACTED):
            out.append(m.span(group))

    if not sql:  # in SQL, these are read inside literals and comments only (above)
        for m in _AUTH_HEADER.finditer(v):
            out.append(m.span("v1" if m.group("v1") else "v2"))
        for rx in (_BEARER, _COOKIE, _CURL_USER, _NETRC, _SAS_SIG, _PGPASS):
            out.extend(m.span("v") for m in rx.finditer(v))
        for rx in (_MYSQL_P, _SSHPASS, _REDIS_A):
            out.extend(m.span("v") for m in rx.finditer(v))
        out.extend(_cli_spans(v))

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
        out.extend(_blob_spans(v))
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
    # the second view (block comments dropped) differs only when there is one
    for drop in (False, True) if "/*" in original else (False,):
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


#: A maximal run of characters an email address is made of, with the ``@``. One run is one
#: candidate address; the ``@`` is in the class so a local part and its domain are one run.
#: ``(?i)`` is deliberate and must stay: ``dms_core.pii._EMAIL_FIND`` is case-insensitive, and
#: under ``re.I`` its ``[A-Z]`` also matches U+0130, U+0131, U+017F and U+212A, so a class of
#: ``A-Za-z`` is NOT the masker's class. A run that stops at one of those letters passes the
#: withhold while the masker still reads it as one long run (quadratic). The class here is
#: the masker's, and ``test_the_withhold_class_covers_everything_the_masker_can_match``
#: derives the masker's set from its own pattern over every code point.
_EMAIL_RUN = re.compile(r"(?i)[A-Z0-9._%+\-@]++")
_EMAIL_CHAR = re.compile(r"(?i)[A-Z0-9._%+\-@]")
#: The longest a valid address can be (RFC 5321). ``dms_core.pii._EMAIL_FIND`` has no bound
#: on its match, so on a long run of address characters it tries every start position and
#: scans to the end of the run each time: quadratic. A run longer than this cannot be an
#: address, so it is withheld before the masker sees it, which bounds that cost to the
#: square of 254 per run, linear overall.
MAX_EMAIL_RUN = 254
_EMAIL_TAIL = re.compile(r"(?i)\.[A-Z]{2}")
#: A mask token for an address, and the markers this module writes in place of text.
_EMAIL_TOKEN = re.compile(r"DMSMASK_email_\d{2,}")
_WITHHELD = re.compile(r"\[(?:long run|email-like run) withheld: \d+ chars\]")
#: RFC 5322 ``atext`` punctuation the masker's local-part class does NOT contain. The masker
#: matches only the part of an address after the last of these, so what is left of it, glued
#: to the mask token, is the start of the local part (``o'brien@x.com`` -> ``o'<token>``).
_ATEXT_ONLY = frozenset("!#$&'*/=?^`{|}~")
_ATEXT_ASCII = frozenset(string.ascii_letters + string.digits + "._%+-") | _ATEXT_ONLY


def _is_atext(c: str) -> bool:
    """``c`` can be part of a local part: RFC 5322 atext or the masker's own class."""
    return c in _ATEXT_ASCII or (c > "\x7f" and _EMAIL_CHAR.match(c) is not None)


def cuts_a_run(text: str, at: int) -> bool:
    """A cut of ``text`` at index ``at`` falls inside a run of address characters."""
    return (
        0 < at < len(text)
        and _EMAIL_CHAR.match(text[at - 1]) is not None
        and _EMAIL_CHAR.match(text[at]) is not None
    )


def _withhold_long_runs(text: str, *, open_end: bool) -> tuple[str, int]:
    """Replace every address-character run longer than ``MAX_EMAIL_RUN`` with a marker.

    ``open_end`` says the text was cut in the middle of a run (the window ended there),
    so the run that touches the end may be half an address and is withheld whatever its
    length. A real address glued to a long run is withheld with it, whole: failing
    closed is the point, a partial address must never be stored.
    """
    out: list[str] = []
    last = 0
    held = 0
    for m in _EMAIL_RUN.finditer(text):
        s, e = m.span()
        if e - s > MAX_EMAIL_RUN or (open_end and e == len(text)):
            out.append(text[last:s])
            out.append(f"[long run withheld: {e - s} chars]")
            last = e
            held += 1
    if not held:
        return text, 0
    out.append(text[last:])
    return "".join(out), held


def _email_shaped(run: str) -> bool:
    """``local@domain.tld`` somewhere in ``run``, the shape the masker is there to remove."""
    at = run.find("@")
    while at != -1:
        nxt = run.find("@", at + 1)
        if at > 0 and _EMAIL_TAIL.search(run, at + 1, len(run) if nxt == -1 else nxt):
            return True
        at = nxt
    return False


def _left_of_tokens(text: str, *, sql: bool) -> list[tuple[int, int]]:
    """Spans ``[start, token_end)`` of a mask token and the local-part text glued to its LEFT.

    The masker's local-part class lacks ``! # $ & ' * / = ? ^ ` { | } ~``, all legal in an
    address, so ``mary.o'brien@x.com`` is masked from ``brien`` on and ``mary.o'`` stays.
    Every run of atext glued to the left of an address token that holds one of those is the
    start of that local part. A scan stops at whitespace or any other character, and never
    crosses the previous span, so the whole pass is linear. In SQL a literal's own quote is
    not part of the address: the scan stops at the start of the literal that holds the token
    (``email='a@b.com'`` keeps ``email=``).
    """
    tokens = list(_EMAIL_TOKEN.finditer(text))
    if not tokens:
        return []
    literals = _sql_regions(text)[0] if sql else []
    spans: list[tuple[int, int]] = []
    floor = 0
    k = 0
    for m in tokens:
        t0 = m.start()
        while k < len(literals) and literals[k][1] <= t0:
            k += 1
        lo = floor
        if k < len(literals) and literals[k][0] <= t0 < literals[k][1]:
            lo = max(lo, literals[k][0])
        i = t0
        while i > lo and _is_atext(text[i - 1]):
            i -= 1
        if i < t0 and any(c in _ATEXT_ONLY for c in text[i:t0]):
            spans.append((i, m.end()))
            floor = m.end()
    return spans


def _withhold_email_like(text: str, *, sql: bool = False) -> str:
    """Withhold what the masker left of an address.

    - A run that is still address-shaped (the masker's email pattern needs a word boundary
      after the top-level domain, so ``a@b.com123`` is not matched).
    - The local-part text glued to the left of an address token (``_left_of_tokens``).
    Each is replaced, with its token, by ``[email-like run withheld: N chars]``.
    """
    spans = [
        m.span()
        for m in _EMAIL_RUN.finditer(text)
        if "@" in m.group() and (len(m.group()) > MAX_EMAIL_RUN or _email_shaped(m.group()))
    ]
    spans += _left_of_tokens(text, sql=sql)
    if not spans:
        return text
    out: list[str] = []
    last = 0
    for s, e in sorted(spans):
        if e <= last:
            continue
        s = max(s, last)
        out.append(text[last:s])
        out.append(f"[email-like run withheld: {e - s} chars]")
        last = e
    out.append(text[last:])
    return "".join(out)


def _marks(text: str) -> int:
    """Mask tokens and withheld markers in ``text``."""
    return len(MASK_TOKEN_RE.findall(text)) + len(_WITHHELD.findall(text))


def mask_pii_counted(text: str, *, open_end: bool = False, sql: bool = False) -> tuple[str, int]:
    """Mask personal data exactly as the customer envelope does. Returns text and a count.

    Fails closed like the envelope: a masker error blanks the text. Three things are
    added around the shared masker, none of which edit it:

    - a run of address characters (the masker's own class, case folding included) longer
      than ``MAX_EMAIL_RUN`` is withheld first (the masker's email pattern is quadratic on
      one; see ``MAX_EMAIL_RUN``). Fidelity limit: such a run is not stored, whatever else
      it was;
    - a run the window cut (``open_end``) is withheld, so half an address is not kept;
    - after masking, a run still shaped like an address, and the local-part text glued to
      the left of an address token, are withheld whole.

    The count is the mask tokens and withheld markers this added to the text, so it equals
    what is stored. The masker runs once, over the whole text, never over pieces: it
    recognises PII that spans words (a spaced phone or card number, a birth cue and a date).
    """
    if not text:
        return text, 0
    held, _ = _withhold_long_runs(text, open_end=open_end)
    masked = str(fail_closed_mask_payload(text=held)["text"])
    final = _withhold_email_like(masked, sql=sql)
    return final, max(0, _marks(final) - _marks(text))


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
