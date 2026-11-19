"""luxvigil audit-config: look through a LiteLLM deployment's files for the
settings that get its keys stolen.

It reads files on your machine and nothing else: no requests to your proxy or
to anyone's. Point it at the folder with your config.yaml, .env and
docker-compose.yml (or at the files themselves) and it says what it found.

What it checks, all from LiteLLM's own docs and code:

  fix now
  - the master key is a publicly known one (sk-1234, the example in LiteLLM's
    docs, which LiteLLM itself refuses at startup)
  - dangerously_permit_weak_or_unset_master_key is on, so the proxy starts
    with no master key or a known one
  - keys written straight into config.yaml instead of os.environ/...
  - a LiteLLM image or package older than 1.84.0
  - an Admin UI password from the docs' examples (admin, langchain...)

  worth a look
  - a LiteLLM image that isn't pinned to a version (latest, main-stable...)
  - no UI_PASSWORD, so the master key is also the Admin UI password

YAML is read line by line, without a YAML library (luxvigil has no
dependencies). That covers config.yaml and docker-compose.yml as people write
them, and anything it can't make sense of is skipped rather than guessed at.
Settings that live somewhere else (a secret manager, Kubernetes, your shell)
aren't in these files, so it can't see them.
"""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

# sha256 of the master keys LiteLLM itself refuses to start with
# (PUBLICLY_KNOWN_MASTER_KEY_SHA256_DIGESTS in litellm/proxy/auth/master_key_boot_check.py).
# This one is sk-1234. Kept as a hash, like LiteLLM does.
KNOWN_MASTER_KEYS = frozenset({"88dc28d0f030c55ed4ab77ed8faf098196cb1c05df778539800c9f1243fe6b4b"})

# 1.84.0 fixes the LiteLLM flaws published this year (Wiz's, Microsoft's, the one on CISA's
# known exploited list). Raise it when a newer one comes out.
FIXED_VERSION = (1, 84, 0)

# passwords from LiteLLM's docs and from setups copied from them
WEAK_UI_PASSWORDS = {"admin", "langchain", "password", "litellm", "changeme", "1234", "12345", "123456"}

# keys in config.yaml that hold a secret
SECRET_KEYS = {"master_key", "api_key", "aws_secret_access_key", "aws_session_token", "client_secret",
               "azure_ad_token"}

OVERRIDE_SETTING = "dangerously_permit_weak_or_unset_master_key"
OVERRIDE_ENV = "LITELLM_DANGEROUSLY_PERMIT_WEAK_OR_UNSET_MASTER_KEY"

UNPINNED_TAGS = {"", "latest", "main", "main-latest", "main-stable", "stable"}

DOCS = {
    "master_key": "https://docs.litellm.ai/docs/proxy/master_key_rotations",
    "security": "https://docs.litellm.ai/docs/proxy/security_best_practices",
    "ui": "https://docs.litellm.ai/docs/proxy/ui",
}

FIX_NOW = "fix now"
LOOK = "worth a look"


class AuditError(ValueError):
    pass


@dataclass
class Finding:
    level: str  # FIX_NOW or LOOK
    check: str  # stable id: default-master-key, weak-key-override, ...
    title: str
    where: str  # file:line
    why: str
    fix: str
    docs: str = ""


@dataclass
class Setting:
    """One name = value we found, and where."""

    name: str
    value: str | None  # after resolving ${VAR} and os.environ/VAR, None if we couldn't
    where: str
    literal: bool = True  # written as is, not taken from another variable


@dataclass
class Audit:
    files: list = field(default_factory=list)  # what we read
    findings: list = field(default_factory=list)

    @property
    def fix_now(self):
        return [f for f in self.findings if f.level == FIX_NOW]

    @property
    def look(self):
        return [f for f in self.findings if f.level == LOOK]


# ---------------------------------------------------------------- reading files


def _truthy(value):
    return str(value or "").strip().lower() in {"true", "1", "yes", "on"}


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _strip_comment(line):
    """Drop a # comment, unless the # is inside quotes or glued to a value (sk-a#b)."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def read_env(text):
    """A .env file -> [(line, name, value)]."""
    out = []
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
            continue
        value = value.strip()
        if not (len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]):
            value = _strip_comment(value).strip()
        out.append((no, name, _unquote(value)))
    return out


@dataclass
class Entry:
    line: int
    path: tuple  # the keys above it
    key: str | None  # None for a list item that isn't a key: value
    value: str | None  # None when it opens a section


_KEY = re.compile(r"""^("[^"]*"|'[^']*'|[^\s:#'"][^:]*?)\s*:(?:\s+(.*))?$""")


def yaml_entries(text):
    """The key: value lines of a YAML file, with the keys above each one.

    Enough for config.yaml and docker-compose.yml as people write them. Flow
    style ({a: b}, [a, b]) and anchors come back as plain values, and block
    scalars (| and >) are skipped.
    """
    stack = []  # (indent, key)
    skip_deeper_than = None
    for no, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if skip_deeper_than is not None:
            if indent > skip_deeper_than:
                continue
            skip_deeper_than = None
        line = _strip_comment(raw).rstrip()
        body = line.strip()
        if not body or body in ("---", "..."):
            continue
        is_item = False
        while body == "-" or body.startswith("- "):
            is_item = True
            indent += 2  # what's in a list item sits one level deeper
            body = body[2:].lstrip() if body != "-" else ""
        if not body:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = tuple(key for _, key in stack)
        match = _KEY.match(body)
        # "- NAME=value" in a compose environment list isn't a key, even with a : later on
        if match and not (is_item and "=" in body.split(":", 1)[0]):
            key = _unquote(match.group(1))
            value = (match.group(2) or "").strip()
            if not value:
                stack.append((indent, key))
                yield Entry(no, path, key, None)
            elif value[0] in "|>":
                skip_deeper_than = indent
                yield Entry(no, path, key, None)
            else:
                yield Entry(no, path, key, _unquote(value))
        elif is_item:
            yield Entry(no, path, None, _unquote(body))


_VAR = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?+])([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def interpolate(value, env):
    """Docker Compose's ${VAR}, ${VAR:-default}, ${VAR:?error} and $VAR, from the .env
    values we have. None when a variable isn't in them and has no default."""
    missing = False

    def sub(m):
        nonlocal missing
        if m.group(0) == "$$":
            return "$"
        name = m.group(1) or m.group(4)
        op, arg = m.group(2) or "", m.group(3) or ""
        have = env.get(name)
        empty_counts = op.startswith(":")
        is_set = have is not None and (have != "" or not empty_counts)
        if op.endswith("+"):
            return arg if is_set else ""
        if is_set:
            return have
        if op.endswith("-"):
            return arg
        missing = True
        return ""

    out = _VAR.sub(sub, value)
    return None if missing else out


# ---------------------------------------------------------------- finding the files


def _kind(path, text):
    name = path.name.lower()
    if name.startswith("requirements") and name.endswith(".txt"):
        return "requirements"
    if name == ".env" or name.endswith(".env") or name.startswith(".env."):
        return "env"
    if name.endswith((".yaml", ".yml")):
        if re.search(r"^services\s*:", text, re.M):
            return "compose"
        if re.search(r"^(general_settings|model_list|litellm_settings|environment_variables)\s*:", text, re.M):
            return "config"
    return None


def _is_example(path):
    return any(word in path.name.lower() for word in ("example", "sample", "template"))


def find_files(paths):
    """The files to read: the ones given, and in folders the ones that look like a LiteLLM setup."""
    found = []
    for given in paths:
        p = Path(given)
        if p.is_dir():
            candidates = sorted(c for c in p.iterdir() if c.is_file() and not _is_example(c))
        elif p.is_file():
            candidates = [p]
        else:
            raise AuditError(f"{given}: no such file or folder")
        for c in candidates:
            if c.stat().st_size > 5_000_000:
                continue  # not a config file
            try:
                text = c.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                if p.is_file():
                    raise AuditError(f"{given}: can't read it as a UTF-8 text file") from None
                continue
            kind = _kind(c, text)
            if kind and c not in [f[0] for f in found]:
                found.append((c, kind, text))
            elif p.is_file() and not kind:
                raise AuditError(f"{given}: doesn't look like a LiteLLM config.yaml, .env, "
                                 "docker-compose.yml or requirements.txt")
    return found


# ---------------------------------------------------------------- collecting settings


def _where(path, line):
    return f"{path}:{line}"


def collect(files):
    """Everything relevant we can read out of the files: settings, images, versions."""
    env = {}  # from .env files, for ${VAR} and os.environ/VAR
    settings = []  # Setting
    configs = []  # (path, [Entry])
    images = []  # (where, image)
    packages = []  # (where, version)
    extra_env = []  # env_file: paths from compose files, read too

    for path, kind, text in files:
        if kind == "env":
            for no, name, value in read_env(text):
                env.setdefault(name, value)
                settings.append(Setting(name, value, _where(path, no)))

    for path, kind, text in files:
        if kind == "compose":
            for e in yaml_entries(text):
                if e.key == "env_file" and e.value:
                    extra_env.append((path, e.value))
                elif "env_file" in e.path[-2:] and e.key in (None, "path") and e.value:
                    extra_env.append((path, e.value))
    already = {p.resolve() for p, _, _ in files}
    for compose_path, ref in extra_env:
        ref = interpolate(ref, env)
        if not ref:
            continue
        p = (compose_path.parent / ref).resolve()
        if p in already or not p.is_file():
            continue
        already.add(p)
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        files.append((p, "env", text))
        for no, name, value in read_env(text):
            env.setdefault(name, value)
            settings.append(Setting(name, value, _where(p, no)))

    for path, kind, text in files:
        if kind == "compose":
            for e in yaml_entries(text):
                if e.key == "image" and e.value:
                    images.append((_where(path, e.line), interpolate(e.value, env) or e.value))
                elif e.path and e.path[-1] == "environment":
                    if e.key is not None and e.value is not None:
                        name, raw = e.key, e.value
                    elif e.key is None and e.value and "=" in e.value:
                        name, raw = e.value.split("=", 1)
                    else:
                        continue
                    value = interpolate(raw, env)
                    settings.append(Setting(name.strip(), value, _where(path, e.line), literal="$" not in raw))
        elif kind == "config":
            entries = list(yaml_entries(text))
            configs.append((path, entries))
            for e in entries:
                if e.path[:1] == ("environment_variables",) and e.key and e.value is not None:
                    value, literal = _config_value(e.value, env)
                    settings.append(Setting(e.key, value, _where(path, e.line), literal))
        elif kind == "requirements":
            for no, raw in enumerate(text.splitlines(), 1):
                m = re.match(r"^\s*litellm(?:\[[^\]]*\])?\s*==\s*([0-9][^\s;#,]*)", raw, re.I)
                if m:
                    packages.append((_where(path, no), m.group(1)))

    return env, settings, configs, images, packages


def _config_value(raw, env):
    """A config.yaml value: (what it is, whether it's written there as is)."""
    if raw.startswith("os.environ/"):
        return env.get(raw[len("os.environ/"):]), False
    return raw, True


# ---------------------------------------------------------------- the checks


def _mask(value):
    """Enough to recognise your own key, not enough to use it."""
    if len(value) <= 8:
        return value[:2] + "…"
    return f"{value[:4]}… ({len(value)} characters)"


def _is_known(key):
    return hashlib.sha256(key.encode()).hexdigest() in KNOWN_MASTER_KEYS


def _version(text):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else None


def _image_tag(image):
    """'ghcr.io/berriai/litellm:main-v1.83.3' -> ('litellm', 'main-v1.83.3'), None if pinned by digest."""
    if "@" in image:
        return None
    name, tag = image, ""
    head, sep, tail = image.rpartition(":")
    if sep and "/" not in tail:
        name, tag = head, tail
    return name.rsplit("/", 1)[-1], tag


def run_checks(files):
    env, settings, configs, images, packages = collect(files)
    findings = []

    # where each master key comes from: the env var, or general_settings.master_key
    master_keys = [s for s in settings if s.name == "LITELLM_MASTER_KEY"]
    for path, entries in configs:
        for e in entries:
            if e.key == "master_key" and e.value is not None:
                value, literal = _config_value(e.value, env)
                master_keys.append(Setting("master_key", value, _where(path, e.line), literal))

    # the same key is often in .env, the compose file and config.yaml: one finding, every place
    places = list(dict.fromkeys(s.where for s in master_keys if s.value and _is_known(s.value)))
    if places:
        findings.append(Finding(
                FIX_NOW, "default-master-key",
                "the master key is sk-1234, the example key from LiteLLM's docs",
                ", ".join(places),
                "anyone who tries it is a proxy admin, with your models, virtual keys and spend in reach. "
                "In February, 294 of 3,074 LiteLLM gateways open to the internet accepted it.",
                'set LITELLM_MASTER_KEY to "sk-" plus a long random value (openssl rand -hex 32) and restart. '
                "With a database, follow LiteLLM's rotation guide so your stored keys stay readable.",
                DOCS["master_key"]))

    # the override that lets the proxy start without a master key, or with a known one
    overrides = [s for s in settings if s.name == OVERRIDE_ENV and _truthy(s.value)]
    for path, entries in configs:
        for e in entries:
            if e.key == OVERRIDE_SETTING and _truthy(_config_value(e.value or "", env)[0]):
                overrides.append(Setting(OVERRIDE_SETTING, e.value, _where(path, e.line)))
    for s in overrides:
        findings.append(Finding(
            FIX_NOW, "weak-key-override",
            f"{s.name} is on",
            s.where,
            "it lets the proxy start with no master key, which accepts every request without "
            "authentication, or with a known one. LiteLLM says to never run it outside local development.",
            "remove it and set a strong LITELLM_MASTER_KEY.",
            DOCS["master_key"]))

    # secrets written into config.yaml itself
    for path, entries in configs:
        for e in entries:
            if e.key in SECRET_KEYS and e.value and not e.value.startswith("os.environ/") \
                    and "$" not in e.value and e.value.lower() not in ("none", "null", "~"):
                known = e.key == "master_key" and _is_known(e.value)
                if known:
                    continue  # already reported as the default key
                findings.append(Finding(
                    FIX_NOW, "secret-in-config",
                    f"{e.key} is written into the config file ({_mask(e.value)})",
                    _where(path, e.line),
                    "config files get committed, copied into images and shared in issues. Keys left "
                    "in code and public files are one of the ways AI keys are being stolen right now.",
                    f"put it in an environment variable or a secret manager and write "
                    f"{e.key}: os.environ/YOUR_VARIABLE instead.",
                    DOCS["security"]))

    # how old LiteLLM is
    for where, image in images:
        parsed = _image_tag(image)
        if not parsed or "litellm" not in parsed[0].lower():
            continue
        tag = parsed[1]
        version = _version(tag)
        if version and version < FIXED_VERSION:
            findings.append(Finding(
                FIX_NOW, "old-version",
                f"LiteLLM {'.'.join(map(str, version))} ({image})",
                where,
                "versions before 1.84.0 have published flaws, at least one of them exploited in the wild.",
                "upgrade to 1.84.0 or later, pinned to an exact version.",
                DOCS["security"]))
        elif not version and tag.lower() in UNPINNED_TAGS:
            findings.append(Finding(
                LOOK, "unpinned-image",
                f"the LiteLLM image isn't pinned to a version ({image})",
                where,
                "you don't know which version you're running, or when it changes under you.",
                "pin an exact release tag, or an image digest.",
                DOCS["security"]))
    for where, text in packages:
        version = _version(text)
        if version and version < FIXED_VERSION:
            findings.append(Finding(
                FIX_NOW, "old-version",
                f"litellm=={text}",
                where,
                "versions before 1.84.0 have published flaws, at least one of them exploited in the wild.",
                "upgrade to 1.84.0 or later.",
                DOCS["security"]))

    # the Admin UI login
    ui_off = any(s.name == "DISABLE_ADMIN_UI" and _truthy(s.value) for s in settings)
    env_login_off = any(e.key == "disable_env_credential_login" and _truthy(_config_value(e.value or "", env)[0])
                        for _, entries in configs for e in entries)
    if not ui_off and not env_login_off:
        passwords = [s for s in settings if s.name == "UI_PASSWORD"]
        for s in passwords:
            if s.value is not None and s.value.strip().lower() in WEAK_UI_PASSWORDS:
                findings.append(Finding(
                    FIX_NOW, "weak-ui-password",
                    f'the Admin UI password is "{s.value}"',
                    s.where,
                    "whoever signs in with it is a proxy admin. It's the kind of password that gets tried first.",
                    "set UI_PASSWORD to a long random value, or create proxy_admin users and turn on "
                    "disable_env_credential_login.",
                    DOCS["ui"]))
        # with sk-1234 that's already said, louder
        if not passwords and master_keys and not places:
            findings.append(Finding(
                LOOK, "master-key-is-ui-password",
                "no UI_PASSWORD, so the master key is also the Admin UI password",
                master_keys[0].where,
                "one leaked key opens both the API and the Admin UI, as admin. Fine if that key never leaves "
                "your secret manager, worth knowing either way.",
                "once your admins have proxy_admin users (or SSO), set general_settings.disable_env_credential_login: "
                "true. Settings made outside these files (Kubernetes, a secret manager) aren't seen here.",
                DOCS["ui"]))

    order = {FIX_NOW: 0, LOOK: 1}
    findings.sort(key=lambda f: order[f.level])
    return findings


def audit(paths):
    files = find_files(paths)
    if not files:
        raise AuditError("found no LiteLLM files to check here: config.yaml (with general_settings or "
                         "model_list), .env, docker-compose.yml or requirements.txt")
    findings = run_checks(files)
    return Audit(files=[str(p) for p, _, _ in files], findings=findings)


# ---------------------------------------------------------------- output


def render_text(result):
    out = [f"🕯️ luxvigil audit-config · read {len(result.files)} file{'s' if len(result.files) != 1 else ''}: "
           + ", ".join(result.files), ""]
    if not result.findings:
        out.append("  ✅ nothing to fix in these files")
    for level, icon, items in ((FIX_NOW, "🔴", result.fix_now), (LOOK, "🟡", result.look)):
        if not items:
            continue
        out.append(f"  {icon} {level} ({len(items)})")
        for f in items:
            out.append(f"     - {f.title}  ·  {f.where}")
            out.append(f"       {f.why}")
            out.append(f"       → {f.fix}")
            if f.docs:
                out.append(f"       {f.docs}")
        out.append("")
    out.append("  Only these files were read, nothing on your network. Settings made somewhere else "
               "(a secret manager, Kubernetes, your shell) aren't seen.")
    return out


def to_dict(result):
    return {
        "files": result.files,
        "findings": [
            {"level": f.level, "check": f.check, "title": f.title, "where": f.where,
             "why": f.why, "fix": f.fix, "docs": f.docs}
            for f in result.findings
        ],
    }
