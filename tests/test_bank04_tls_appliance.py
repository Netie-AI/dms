"""BANK-04 (#271): the appliance serves TLS only and ships no database password.

Docker-free. This reads ``deploy/compose/Caddyfile`` and the compose files as
text and fails when one of the properties the bank asked for stops being true:

* every Caddy site is ``https://``; no plain-HTTP escape hatch;
* the site names a certificate source chosen at install (Caddy internal CA as
  the sandbox default, operator-supplied files otherwise);
* the Postgres password is a *required* install-time variable, never a literal,
  and the API's DATABASE_URL carries that same variable;
* Caddy's is the only port published on every interface;
* TLS key material cannot be committed;
* the API image installs the private cortex-contract only from the wheelhouse,
  and the docker build context leaves secrets out.

It is the guard for machines without Docker. The behavioural check against live
containers is ``scripts/compose_tls_smoke.sh`` (CI job ``compose-tls-smoke``):
this file proves the config says the right thing, the smoke proves Caddy and
Postgres do it. Neither stands in for the other.

Each test fails on the parent commit (the plain ``:8080`` Caddyfile and the
compose file with ``POSTGRES_PASSWORD: dms``).
"""

from __future__ import annotations

import fnmatch
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
DOCKERFILE = ROOT / "apps" / "api" / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"

LOOPBACK = {"127.0.0.1", "::1", "[::1]", "localhost"}
# ``${NAME:?message}`` is compose's required-variable form: unset or empty aborts
# before any container is created. ``${NAME:-x}`` and ``${NAME-x}`` supply a default.
REQUIRED_VAR = re.compile(r"\$\{(?P<name>[A-Z][A-Z0-9_]*):\?[^}]+\}")


class ComposeLoader(yaml.SafeLoader):
    """SafeLoader that accepts compose's merge tags (``!reset``, ``!override``).

    A bare ``yaml.safe_load`` raises on them, which would crash this file the day
    an override file uses one. They are read as the plain data they tag, so the
    audits below still see the overriding values.
    """


def _construct_tagged(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)  # type: ignore[arg-type]


ComposeLoader.add_multi_constructor("!", _construct_tagged)


def load_compose(path: Path) -> dict[str, Any]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=ComposeLoader) or {}  # noqa: S506


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

    (glob,) = [b for b in blocks if b.is_global]
    for line in glob.lines:
        words = line.split()
        if words[0] == "auto_https":
            assert words[1:] in (["disable_redirects"], ["prefer_wildcard"]), (
                f"global option {line!r} switches off or weakens automatic HTTPS"
            )
        assert words[0] not in {"http_port", "default_bind"}, (
            f"global option {line!r} moves or widens a listener"
        )
        # Any of these points Caddy at a public ACME CA with the customer's hostname.
        assert words[0] not in {"acme_ca", "acme_ca_root", "acme_eab", "acme_dns", "email"}, (
            f"global option {line!r} configures a public ACME CA"
        )

    # D3: never public ACME, even if a site ends up with no tls directive (a glob
    # in DMS_TLS_MODE makes `import tls_*` match no file, which Caddy only warns
    # about). With local_certs such a site falls back to the internal CA.
    assert "local_certs" in glob.top, (
        "global option local_certs is missing: a site without a tls directive would "
        "request a certificate from a public ACME CA"
    )

    # NIT e: no HTTP/3. It opens UDP on the TLS port and advertises itself by Alt-Svc.
    assert "servers {" in glob.top, "no global servers block to turn HTTP/3 off"
    protocols = [ln for ln in glob.lines if ln.startswith("protocols")]
    assert protocols == ["protocols h1 h2"], f"HTTP/3 must stay off, got {protocols}"


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
    ui = load_compose(BASE_COMPOSE)["services"]["ui"]
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
    """(service, host_ip, container_port) for every ``ports`` entry.

    ``${NAME:-default}`` is read as its default, which is what an install that
    sets nothing gets: ``${DMS_BIND_ADDR:-0.0.0.0}:8080:8080`` binds 0.0.0.0.
    """
    out: list[tuple[str, str, str]] = []
    for name, service in (doc.get("services") or {}).items():
        for entry in service.get("ports") or []:
            if isinstance(entry, dict):
                ip = str(entry.get("host_ip", ""))
                container = str(entry["target"])
            else:
                text = re.sub(r"\$\{[^}:]+:-([^}]*)\}", r"\1", str(entry))
                parts = text.split("/")[0].split(":")
                container = parts[-1]
                ip = parts[0] if len(parts) == 3 else ""
            out.append((name, ip, container))
    return out


def test_only_caddy_publishes_a_port_on_every_interface(tmp_path):
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
    # The bind address is an install-time key; unset it is every interface, as before.
    assert base["services"]["ui"]["ports"] == ["${DMS_BIND_ADDR:-0.0.0.0}:8080:8080"]

    # Every other compose file in the directory (the host-bind overlay, a local
    # override) may publish loopback only, and none may use host networking.
    for path in sorted(COMPOSE_DIR.glob("*.yml")):
        doc = load_compose(path)
        if path != BASE_COMPOSE:
            wide = [p for p in published_ports(doc) if p[1] not in LOOPBACK]
            assert not wide, f"{path.name} publishes beyond loopback: {wide}"
        for name, service in (doc.get("services") or {}).items():
            assert service.get("network_mode") != "host", (
                f"{path.name}: {name} uses host networking and bypasses the port rule"
            )

    # A compose override may use ``!override`` / ``!reset``. A bare yaml.safe_load
    # raises on them; the loader above reads them as the data they tag, so the
    # audit still sees the port an override opens.
    override = tmp_path / "docker-compose.override.yml"
    override.write_text(
        "services:\n"
        "  ui:\n"
        "    ports: !override\n"
        '      - "0.0.0.0:9443:8080"\n'
        "  api:\n"
        "    ports: !reset []\n"
        "    environment:\n"
        "      DATABASE_URL: !reset null\n"
        "  api_dev:\n"
        "    ports: !override\n"
        '      - "127.0.0.1:8090:8080"\n',
        encoding="utf-8",
    )
    tagged = load_compose(override)
    assert published_ports(tagged) == [("ui", "0.0.0.0", "8080"), ("api_dev", "127.0.0.1", "8080")]
    assert tagged["services"]["api"]["ports"] == []
    opened = [p for p in published_ports(tagged) if p[1] not in LOOPBACK]
    assert opened == [("ui", "0.0.0.0", "8080")]


def test_ui_entry_guard_admits_only_the_words_internal_and_files():
    """DMS_TLS_MODE is spliced into ``import tls_<mode>``. A glob (``*``) makes that
    a file glob, which Caddy only warns about, leaving the site with no certificate
    source. The ui command refuses anything but the two exact words first.
    """
    ui = load_compose(BASE_COMPOSE)["services"]["ui"]
    command = ui.get("command")
    assert isinstance(command, list) and command[:2] == ["sh", "-c"], (
        "the ui service has no entry guard command; Caddy would start with whatever "
        "DMS_TLS_MODE says"
    )
    script = command[2]
    # compose writes a literal $ as $$
    assert 'case "$${DMS_TLS_MODE}" in' in script
    accepted = re.search(r"^\s*([A-Za-z|]+)\) ;;\s*$", script, flags=re.M)
    assert accepted, "no `word|word) ;;` arm in the entry guard"
    words = accepted.group(1).split("|")
    assert words == ["internal", "files"], f"the guard accepts {words}"
    assert re.search(r"^\s*\*\) .*exit 1 ;;\s*$", script, flags=re.M), (
        "the arm after the two words must refuse (exit 1) everything else"
    )
    # Caddy starts only after the guard, and by exec so it is PID 1.
    assert script.index("esac") < script.index("exec caddy run")
    # `case` patterns are literal words here, so none of these can reach Caddy.
    for value in ("*", "tls_*", "internal|files", "files ", "INTERNAL", "", "../x"):
        assert value not in words
    assert ui["environment"]["DMS_TLS_MODE"] == "${DMS_TLS_MODE:-internal}"


# --- key material ------------------------------------------------------------


def test_tls_key_material_cannot_be_committed():
    ignore = {
        ln.strip()
        for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    }
    # Everything dropped into the default DMS_TLS_DIR is ignored whatever it is
    # called (only .gitkeep is tracked); anywhere else under deploy/ the usual
    # certificate, key and keystore extensions are ignored (PKCS#12 and Java
    # keystores are how banks ship them); so is the file that carries the password.
    for needed in (
        "deploy/compose/tls/*",
        "!deploy/compose/tls/.gitkeep",
        "deploy/**/*.crt",
        "deploy/**/*.cer",
        "deploy/**/*.cert",
        "deploy/**/*.key",
        "deploy/**/*.pem",
        "deploy/**/*.p12",
        "deploy/**/*.pfx",
        "deploy/**/*.jks",
        "deploy/compose/.env",
    ):
        assert needed in ignore, f".gitignore does not carry {needed!r}"
    # The default mount directory exists in a clean checkout, so Docker does not
    # create it root-owned on first `up`.
    assert (COMPOSE_DIR / "tls" / ".gitkeep").is_file()

    # Where a git repository is present, ask git rather than trusting the patterns.
    if (ROOT / ".git").exists():

        def ignored(rel: str) -> bool:
            r = subprocess.run(
                ["git", "check-ignore", "-q", rel], cwd=ROOT, capture_output=True, check=False
            )
            return r.returncode == 0

        names = (
            "tls.crt tls.key tls.pem chain.pem bank.p12 bank.pfx tls.cert tls.cer bank.jks "
            "notes.txt sub/dir/file.bin"
        ).split()
        for name in names:
            assert ignored(f"deploy/compose/tls/{name}"), (
                f"git does not ignore deploy/compose/tls/{name}"
            )
        for name in ("bank.p12", "bank.pfx", "x.jks", "x.cer", "x.cert", "x.key", "x.pem", "x.crt"):
            assert ignored(f"deploy/other/{name}"), f"git does not ignore deploy/other/{name}"
        assert not ignored("deploy/compose/tls/.gitkeep"), ".gitkeep must stay trackable"

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
        suffixes = {".crt", ".key", ".pem", ".p12", ".pfx", ".cer", ".cert", ".jks"}
        assert path.suffix.lower() not in suffixes, f"{rel} is certificate or key material"
        assert b"PRIVATE KEY-----" not in path.read_bytes()[:200_000], (
            f"{rel} contains private key material"
        )


# --- the api image: cortex-contract only from the wheelhouse ------------------


def dockerfile_instructions(text: str) -> list[tuple[str, str]]:
    """(INSTRUCTION, argument text) with line continuations joined and comments dropped."""
    out: list[tuple[str, str]] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("#") or (not buf and not line.strip()):
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        instruction, _, rest = buf.strip().partition(" ")
        out.append((instruction.upper(), " ".join(rest.split())))
        buf = ""
    return out


def test_api_image_installs_cortex_contract_only_from_the_wheelhouse():
    """cortex-contract is on no index, and the name is unclaimed on PyPI. An install
    that merely adds ``--find-links /wheelhouse`` still searches PyPI too, so a
    ``cortex-contract`` 1.2.1 published there would win and land in the appliance.
    The wheel must go in first, by exact local path, with no index, and the main
    install must be unable to replace it.
    """
    instructions = dockerfile_instructions(DOCKERFILE.read_text(encoding="utf-8"))
    runs = [arg for ins, arg in instructions if ins == "RUN"]
    installs = [r for r in runs if "pip install" in r]
    assert installs, "the api image installs nothing with pip"
    joined = " ; ".join(installs)
    steps = [s.strip() for s in joined.split(";")]

    # Nothing may add an index or a link source that could carry another cortex-contract.
    for step in (s for s in steps if "pip install" in s):
        padded = f" {step} "
        for flag in ("--find-links", " -f ", "--extra-index-url", "--index-url", " -i "):
            assert flag not in padded, (
                f"pip install uses {flag.strip()!r}; an index can still win: {step}"
            )

    def step_index(*needles: str) -> int:
        for i, step in enumerate(steps):
            if all(n in step for n in needles):
                return i
        raise AssertionError(f"no install step contains all of {needles}")

    # 1. exactly one wheel, or the build fails
    guard = step_index("-ne 1") if any("-ne 1" in s for s in steps) else -1
    assert guard >= 0 and "cortex_contract-*.whl" in joined, (
        "the build must fail unless the wheelhouse holds exactly one cortex_contract wheel"
    )
    # 2. the wheel itself: local path, no index, no dependency resolution
    wheel_path = "/wheelhouse/cortex_contract-*.whl"
    wheel = step_index("pip install", "--no-index", "--no-deps", wheel_path)
    # 3. a constraint frozen from what is installed, and the main install under it
    pin = step_index("pip freeze", "cortex", ">")
    main = step_index("pip install", "-c ", "./packages/cortex_client")
    pin_file = re.search(r">\s*(\S+)", steps[pin])
    assert pin_file and f"-c {pin_file.group(1)}" in steps[main], (
        "the main install must run under the constraint frozen from the installed wheel"
    )
    assert guard < wheel < pin < main, "wheel first, then the pin, then the main install"


def test_docker_build_context_leaves_secrets_out_and_keeps_what_the_api_image_copies():
    ignore = [
        ln.strip()
        for ln in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    # The private checkout, the git config that holds checkout credentials, and
    # the install-time secrets stay out of the context.
    for needed in ("_cortex/", ".git", ".env", "deploy/compose/.env", "deploy/compose/tls/"):
        assert needed in ignore, f".dockerignore does not carry {needed!r}"

    def is_ignored(path: str) -> bool:
        parts = path.strip("/").split("/")
        prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        for pattern in ignore:
            pat = pattern.rstrip("/")
            for candidate in prefixes:
                if fnmatch.fnmatchcase(candidate, pat) or candidate == pat:
                    return True
                if pat.startswith("**/") and fnmatch.fnmatchcase(candidate, pat[3:]):
                    return True
        return False

    sources: list[str] = []
    for ins, arg in dockerfile_instructions(DOCKERFILE.read_text(encoding="utf-8")):
        if ins == "COPY":
            words = [w for w in arg.split() if not w.startswith("--")]
            sources += [w.removeprefix("./") for w in words[:-1]]
    assert "deploy/wheelhouse" in sources and "packages" in sources, (
        f"COPY sources not read as expected: {sources}"
    )
    needed_but_ignored = [s for s in sources if is_ignored(s)]
    assert not needed_but_ignored, (
        f".dockerignore excludes what the api image COPYs: {needed_but_ignored}"
    )
    # The matcher itself: it must say yes to the secrets and no to the sources.
    for secret in ("_cortex/pyproject.toml", ".git", ".env", "apps/ui/.env", "deploy/compose/.env"):
        assert is_ignored(secret), f"matcher does not ignore {secret}"
    for kept in ("pyproject.toml", "packages/core/pyproject.toml", "deploy/wheelhouse/.gitkeep"):
        assert not is_ignored(kept), f"matcher ignores {kept}"


# --- the smoke is wired -------------------------------------------------------


def test_ci_runs_the_compose_tls_smoke_and_the_existing_gates_remain():
    jobs = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    for gate in ("protected-paths", "contract-pin", "lint-type-test"):
        assert gate in jobs, f"existing gate {gate!r} is gone"

    assert "compose-tls-smoke" in jobs, "no CI job runs the compose TLS smoke"
    job = jobs["compose-tls-smoke"]
    runs = [s.get("run", "") for s in job["steps"]]
    assert any("scripts/compose_tls_smoke.sh" in r for r in runs)
    # The launcher's password reader is pinned by its self-check, run in this job.
    assert any("Test-GetDmsDbPassword.ps1" in r for r in runs)
    assert (ROOT / "scripts" / "windows" / "Test-GetDmsDbPassword.ps1").is_file()

    # Token hygiene: read-only permissions, and no checkout leaves a credential in
    # .git/config for the rest of the job (the private Cortex checkout carries
    # CORTEX_CONTRACT_TOKEN).
    assert job.get("permissions") == {"contents": "read"}
    checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]
    assert len(checkouts) == 2
    for step in checkouts:
        assert step.get("with", {}).get("persist-credentials") is False, (
            f"checkout step {step.get('name')!r} keeps its credentials in .git/config"
        )

    script = SMOKE_SCRIPT.read_text(encoding="utf-8")
    # The three assertions the ticket names, by what each one checks.
    assert "A compose refuses to start with DMS_DB_PASSWORD" in script
    assert "B1 HTTPS /health answers 200" in script
    assert "C plain HTTP" in script
    assert 'fail "C plain HTTP' in script
    # The glob mode that Caddy alone only warns about is asserted live.
    assert "DMS_TLS_MODE=* with a non-local host" in script


def test_smoke_script_is_hermetic():
    """The smoke verifies the shipped compose file, not whatever the workstation has."""
    script = SMOKE_SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))

    # Inert sibling URLs: /health must not probe a Cortex or OpenVault on this host.
    assert 'export CORTEX_URL="http://127.0.0.1:9"' in code
    assert 'export OPENVAULT_URL="http://127.0.0.1:9"' in code
    # The published port is bound to loopback through the install-time key.
    assert 'export DMS_BIND_ADDR="127.0.0.1"' in code
    # Every compose invocation names the file, so an override cannot change the run.
    invocations = re.findall(r"docker compose --env-file[^\n]*", code)
    assert invocations, "no compose invocation found"
    for call in invocations:
        assert "-f docker-compose.yml" in call, f"compose call without an explicit -f: {call}"
    # Teardown removes the image the run built, not only containers and volumes.
    assert "down -v --rmi local" in code
    assert not re.search(r"down -v --remove-orphans", code), "a teardown still leaves the image"
