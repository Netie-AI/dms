"""BANK-04 (#271): the appliance serves TLS only and ships no database password.

Docker-free. This reads ``deploy/compose/Caddyfile`` and the compose files as
text and fails when one of the properties the bank asked for stops being true:

* every Caddy site is ``https://``; no plain-HTTP escape hatch;
* the site names a certificate source chosen at install (Caddy internal CA as
  the sandbox default, operator-supplied files otherwise);
* the Postgres password is a *required* install-time variable, never a literal,
  and the API's DATABASE_URL carries that same variable;
* Caddy's is the only port published on every interface;
* TLS key material cannot be committed.

It is the guard for machines without Docker. The behavioural check against live
containers is ``scripts/compose_tls_smoke.sh`` (CI job ``compose-tls-smoke``):
this file proves the config says the right thing, the smoke proves Caddy and
Postgres do it. Neither stands in for the other.

Each test fails on the parent commit (the plain ``:8080`` Caddyfile and the
compose file with ``POSTGRES_PASSWORD: dms``).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_DIR = ROOT / "deploy" / "compose"
CADDYFILE = COMPOSE_DIR / "Caddyfile"
BASE_COMPOSE = COMPOSE_DIR / "docker-compose.yml"
HOSTDB_COMPOSE = COMPOSE_DIR / "docker-compose.hostdb.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SMOKE_SCRIPT = ROOT / "scripts" / "compose_tls_smoke.sh"

LOOPBACK = {"127.0.0.1", "::1", "[::1]", "localhost"}
# ``${NAME:?message}`` is compose's required-variable form: unset or empty aborts
# before any container is created. ``${NAME:-x}`` and ``${NAME-x}`` supply a default.
REQUIRED_VAR = re.compile(r"\$\{(?P<name>[A-Z][A-Z0-9_]*):\?[^}]+\}")


# --- Caddyfile ---------------------------------------------------------------


@dataclass
class Block:
    header: str  # text before the opening brace; "" for the global options block
    # every non-blank, non-comment line inside the block, at any depth
    lines: list[str] = field(default_factory=list)
    # the subset at depth 1, i.e. written directly in the block
    top: list[str] = field(default_factory=list)

    @property
    def is_snippet(self) -> bool:
        return self.header.startswith("(") and self.header.endswith(")")

    @property
    def is_global(self) -> bool:
        return self.header == ""

    @property
    def is_site(self) -> bool:
        return not self.is_snippet and not self.is_global


def parse_caddyfile(text: str) -> list[Block]:
    """Parse the one-directive-per-line layout that ``caddy fmt`` enforces.

    A block opens on a line ending ``{`` preceded by whitespace (or alone) and
    closes on a line that is exactly ``}``. ``{path}`` and ``{$ENV:default}``
    placeholders never end a line with a bare ``{`` or equal a bare ``}``, so
    they do not confuse the depth count.
    """
    blocks: list[Block] = []
    depth = 0
    current: Block | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if depth == 0:
            assert line.endswith("{"), f"Caddyfile line outside any block: {line!r}"
            current = Block(header=line[:-1].strip())
            blocks.append(current)
            depth = 1
            continue
        assert current is not None
        if line == "}":
            depth -= 1
            continue
        current.lines.append(line)
        if depth == 1:
            current.top.append(line)
        if line.endswith(" {"):
            depth += 1
    assert depth == 0, "unbalanced braces in Caddyfile"
    return blocks


def caddy_blocks() -> list[Block]:
    return parse_caddyfile(CADDYFILE.read_text(encoding="utf-8"))


def site_port(address: str) -> str:
    m = re.search(r":(\d+)$", address)
    assert m, f"site address {address!r} names no explicit port"
    return m.group(1)


def test_caddyfile_every_site_is_https_with_no_plain_http_escape():
    blocks = caddy_blocks()
    sites = [b for b in blocks if b.is_site]
    assert sites, "Caddyfile has no site block; nothing would be served"

    for site in sites:
        for address in (a.strip() for a in site.header.split(",")):
            # A bare ``:8080`` is a plain-HTTP site, and so is ``http://...``.
            # Require the scheme so the intent cannot be misread or regress.
            assert address.startswith("https://"), (
                f"site address {address!r} is not https://: Studio and /v1/* "
                "would be served over plain HTTP (BANK-04)"
            )

    # One site, one port: a second port is a second listener nobody published.
    ports = {site_port(a.strip()) for s in sites for a in s.header.split(",")}
    assert len(ports) == 1, f"sites listen on more than one port: {sorted(ports)}"

    glob = [b for b in blocks if b.is_global]
    for block in glob:
        for line in block.lines:
            words = line.split()
            if words[0] == "auto_https":
                assert words[1:] in (["disable_redirects"], ["prefer_wildcard"]), (
                    f"global option {line!r} switches off or weakens automatic HTTPS"
                )
            assert words[0] not in {"http_port", "default_bind"}, (
                f"global option {line!r} moves or widens a listener"
            )
            assert words[0] != "servers", "per-server listener options are not allowed here"


def test_caddyfile_site_terminates_tls_from_an_install_time_source():
    blocks = caddy_blocks()
    snippets = {b.header[1:-1]: b for b in blocks if b.is_snippet}

    assert "tls_internal" in snippets, "no snippet for Caddy's internal CA (sandbox fallback)"
    assert snippets["tls_internal"].lines == ["tls internal"]

    assert "tls_files" in snippets, "no snippet for an operator-supplied certificate"
    (files_line,) = snippets["tls_files"].lines
    words = files_line.split()
    assert words[0] == "tls" and len(words) == 3 and words[1] != "internal", (
        f"tls_files must load a certificate and key file, got {files_line!r}"
    )

    # Paths come from the compose mount, so a supplied certificate directory and
    # the Caddyfile cannot drift apart.
    ui = yaml.safe_load(BASE_COMPOSE.read_text(encoding="utf-8"))["services"]["ui"]
    # ``${DMS_TLS_DIR:-./tls}:/etc/caddy/tls:ro`` has colons inside the variable.
    mount_targets = [
        re.sub(r"\$\{[^}]*\}", "VAR", str(v)).split(":")[1] for v in ui["volumes"] if ":" in str(v)
    ]
    tls_dir = next((t for t in mount_targets if t.endswith("/tls")), None)
    assert tls_dir, "ui service mounts no .../tls directory for operator certificates"
    assert words[1].startswith(tls_dir + "/") and words[2].startswith(tls_dir + "/"), (
        f"tls_files paths {words[1:]} are not under the mounted {tls_dir}"
    )

    (site,) = [b for b in blocks if b.is_site]
    imports = [ln for ln in site.top if ln.startswith("import ")]
    assert imports == ["import tls_{$DMS_TLS_MODE:internal}"], (
        "the site must pick its certificate source from DMS_TLS_MODE and default to "
        f"the internal CA, got {imports}"
    )
    assert not any(ln.split()[0] == "tls" for ln in site.top), (
        "a literal tls directive in the site bypasses the install-time choice"
    )
    # compose hands the mode to Caddy and defaults it the same way.
    assert ui["environment"]["DMS_TLS_MODE"] == "${DMS_TLS_MODE:-internal}"


def test_caddyfile_studio_fallback_does_not_swallow_the_api_routes():
    """Found in BANK-04: ``try_files`` at site level rewrote /health, /v1/* and
    /api/* to /index.html before any ``handle`` ran, so the API was unreachable
    through Caddy. The SPA fallback has to sit inside its own ``handle``.
    """
    (site,) = [b for b in caddy_blocks() if b.is_site]
    for directive in ("try_files", "rewrite", "file_server", "root"):
        stray = [ln for ln in site.top if ln.split()[0] == directive]
        assert not stray, (
            f"{directive!r} at site level runs before every handle and shadows the API "
            f"routes: {stray}"
        )
    handles = [ln for ln in site.top if ln.startswith("handle")]
    assert handles[-1] == "handle {", "the catch-all Studio handle must come last"
    for needed in ("handle /health {", "handle /v1/* {"):
        assert needed in handles, f"missing API route {needed!r}"
        assert handles.index(needed) < handles.index("handle {")


# --- compose -----------------------------------------------------------------


def load_compose(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def env_of(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        return dict(item.split("=", 1) for item in env)
    return {k: "" if v is None else str(v) for k, v in env.items()}


def test_compose_database_password_is_required_and_never_defaulted():
    services = load_compose(BASE_COMPOSE)["services"]
    value = env_of(services["postgres"]).get("POSTGRES_PASSWORD", "")
    assert REQUIRED_VAR.fullmatch(value), (
        f"POSTGRES_PASSWORD is {value!r}; it must be ${{NAME:?message}} so compose "
        "refuses to start without an install-time password (BANK-04)"
    )

    # No file in the compose directory may carry a literal or defaulted password.
    for path in sorted(COMPOSE_DIR.glob("*.yml")):
        doc = load_compose(path)
        for name, service in (doc.get("services") or {}).items():
            for key, val in env_of(service).items():
                if "PASSWORD" in key.upper():
                    assert REQUIRED_VAR.fullmatch(val), (
                        f"{path.name}: service {name} sets {key}={val!r}; passwords come "
                        "from install-time configuration, never a committed value"
                    )


def test_compose_database_urls_carry_the_same_required_password():
    services = load_compose(BASE_COMPOSE)["services"]
    pg_var = REQUIRED_VAR.fullmatch(env_of(services["postgres"])["POSTGRES_PASSWORD"])
    assert pg_var
    for name in ("api", "api_dev"):
        url = env_of(services[name])["DATABASE_URL"]
        m = re.fullmatch(r"postgresql://dms:(?P<pw>.+)@postgres:5432/dms", url)
        assert m, f"{name} DATABASE_URL has an unexpected shape: {url!r}"
        pw = REQUIRED_VAR.fullmatch(m.group("pw"))
        assert pw, (
            f"{name} DATABASE_URL embeds a literal or defaulted password {m.group('pw')!r}"
        )
        assert pw.group("name") == pg_var.group("name"), (
            f"{name} reads {pw.group('name')} but Postgres is initialised from "
            f"{pg_var.group('name')}: the API could never log in"
        )


def published_ports(doc: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(service, host_ip, container_port) for every ``ports`` entry."""
    out: list[tuple[str, str, str]] = []
    for name, service in (doc.get("services") or {}).items():
        for entry in service.get("ports") or []:
            if isinstance(entry, dict):
                ip = str(entry.get("host_ip", ""))
                container = str(entry["target"])
            else:
                parts = str(entry).split("/")[0].split(":")
                container = parts[-1]
                ip = parts[0] if len(parts) == 3 else ""
            out.append((name, ip, container))
    return out


def test_only_caddy_publishes_a_port_on_every_interface():
    base = load_compose(BASE_COMPOSE)
    open_ports = [p for p in published_ports(base) if p[1] not in LOOPBACK]
    assert [(s, c) for s, _, c in open_ports] == [("ui", "8080")], (
        f"ports published on every interface: {open_ports}; T6 allows only Caddy's "
        "(profile-gated services included: a dev profile that binds 0.0.0.0 hands "
        "the unauthenticated API to the LAN)"
    )
    assert base["services"]["ui"]["image"].startswith("caddy:")
    (site,) = [b for b in caddy_blocks() if b.is_site]
    assert site_port(site.header) == open_ports[0][2], (
        "the published container port is not the port Caddy's TLS site listens on"
    )

    # The host-bind overlay exists for the developer's own machine: loopback only.
    overlay = load_compose(HOSTDB_COMPOSE)
    wide = [p for p in published_ports(overlay) if p[1] not in LOOPBACK]
    assert not wide, f"docker-compose.hostdb.yml binds beyond loopback: {wide}"

    for path in (BASE_COMPOSE, HOSTDB_COMPOSE):
        for name, service in (load_compose(path).get("services") or {}).items():
            assert service.get("network_mode") != "host", (
                f"{path.name}: {name} uses host networking and bypasses the port rule"
            )


# --- key material ------------------------------------------------------------


def test_tls_key_material_cannot_be_committed():
    ignore = {
        ln.strip()
        for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    }
    # Anything dropped into the default DMS_TLS_DIR, or anywhere under deploy/, is
    # ignored; so is the file that carries the install-time password.
    for needed in ("deploy/**/*.crt", "deploy/**/*.key", "deploy/**/*.pem", "deploy/compose/.env"):
        assert needed in ignore, f".gitignore does not carry {needed!r}"
    # The default mount directory exists in a clean checkout, so Docker does not
    # create it root-owned on first `up`.
    assert (COMPOSE_DIR / "tls" / ".gitkeep").is_file()

    # Where a git repository is present, ask git rather than trusting the patterns.
    if (ROOT / ".git").exists():
        for name in ("tls.crt", "tls.key", "tls.pem", "chain.pem"):
            r = subprocess.run(
                ["git", "check-ignore", "-q", f"deploy/compose/tls/{name}"],
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            assert r.returncode == 0, f"git does not ignore deploy/compose/tls/{name}"

    # Nothing of the kind sits in the tree outside the (ignored) mount directory
    # and the (ignored) wheelhouse.
    skip = (COMPOSE_DIR / "tls", ROOT / "deploy" / "wheelhouse")
    found = [
        p
        for p in (ROOT / "deploy").rglob("*")
        if p.is_file() and not any(s in p.parents for s in skip)
    ]
    assert found, "no files under deploy/; the scan would pass vacuously"
    for path in found:
        rel = path.relative_to(ROOT).as_posix()
        assert path.suffix.lower() not in {".crt", ".key", ".pem", ".p12", ".pfx", ".cer"}, (
            f"{rel} is certificate or key material"
        )
        assert b"PRIVATE KEY-----" not in path.read_bytes()[:200_000], (
            f"{rel} contains private key material"
        )


# --- the smoke is wired -------------------------------------------------------


def test_ci_runs_the_compose_tls_smoke_and_the_existing_gates_remain():
    jobs = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    for gate in ("protected-paths", "contract-pin", "lint-type-test"):
        assert gate in jobs, f"existing gate {gate!r} is gone"

    assert "compose-tls-smoke" in jobs, "no CI job runs the compose TLS smoke"
    runs = [s.get("run", "") for s in jobs["compose-tls-smoke"]["steps"]]
    assert any("scripts/compose_tls_smoke.sh" in r for r in runs)

    script = SMOKE_SCRIPT.read_text(encoding="utf-8")
    # The three assertions the ticket names, by what each one checks.
    assert "A compose refuses to start with DMS_DB_PASSWORD" in script
    assert "B1 HTTPS /health answers 200" in script
    assert "C plain HTTP" in script
    assert 'fail "C plain HTTP' in script
