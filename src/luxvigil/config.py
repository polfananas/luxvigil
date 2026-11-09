"""Optional luxvigil.toml, for telling luxvigil what's fine in your setup.

    [trusted]
    networks = ["203.0.113.0/24"]   # office, VPN exit, a customer's office...
    devices = ["kiosk-01"]

    [ignore]
    accounts = ["qa_*"]
    emails = ["*@yourcompany.com"]
    keys = ["key_ci_*"]

Trusted networks and devices still get analyzed, they just never link accounts
into a farm. Ignored accounts, emails and keys are left out of the checks
completely (they still count towards your total spend).

Patterns use shell-style wildcards (* and ?) and don't care about case.
Anything we don't recognise is an error rather than silently skipped: a typo
like [ignored] would otherwise mean your allowlist quietly does nothing.
"""

import ipaddress
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

DEFAULT_PATH = "luxvigil.toml"

# section -> the keys it can have
ALLOWED = {
    "trusted": {"networks", "devices"},
    "ignore": {"accounts", "emails", "keys"},
}


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    """What the user told us is fine. An empty Config trusts and ignores nothing."""

    trusted_networks: list = field(default_factory=list)
    trusted_devices: set = field(default_factory=set)
    ignore_accounts: list = field(default_factory=list)
    ignore_emails: list = field(default_factory=list)
    ignore_keys: list = field(default_factory=list)
    path: str = ""
    # the same IPs come up on thousands of rows, so check each one once
    _ip_cache: dict = field(default_factory=dict, repr=False, compare=False)

    def trusts_ip(self, ip):
        """Is this IP inside one of the trusted networks? Bad IPs are never trusted."""
        if ip not in self._ip_cache:
            self._ip_cache[ip] = self._trusts_ip(ip)
        return self._ip_cache[ip]

    def _trusts_ip(self, ip):
        try:
            addr = ipaddress.ip_address(ip.strip())
        except ValueError:
            return False
        return any(addr in net for net in self.trusted_networks)

    def ignores(self, r):
        """Should this request be left out of the checks?"""
        return (
            _matches(r.account_id, self.ignore_accounts)
            or _matches(r.email, self.ignore_emails)
            or _matches(r.api_key_id, self.ignore_keys)
        )


def _matches(value, patterns):
    """Does `value` match any of the wildcard patterns, ignoring case?"""
    value = value.lower()
    return any(fnmatchcase(value, p.lower()) for p in patterns)


def _strings(section, key, value):
    """Make sure a setting is a list of strings, so "qa_*" (no brackets) gets caught."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"[{section}] {key} should be a list of strings")
    return value


def load_config(path=None):
    """Read the config. No path and no luxvigil.toml around means an empty config."""
    if path is None:
        if not Path(DEFAULT_PATH).exists():
            return Config()
        path = DEFAULT_PATH

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} isn't valid TOML: {e}") from e

    # 1. only the sections and keys we know about
    for section, values in data.items():
        if section not in ALLOWED:
            raise ConfigError(f"{path}: unknown section [{section}] (use [trusted] or [ignore])")
        if not isinstance(values, dict):
            # e.g. `trusted = "1.2.3.4"` at the top instead of a [trusted] section
            raise ConfigError(f"{path}: {section} should be a [{section}] section")
        unknown = set(values) - ALLOWED[section]
        if unknown:
            raise ConfigError(
                f"{path}: unknown key(s) in [{section}]: {', '.join(sorted(unknown))}. "
                f"Allowed: {', '.join(sorted(ALLOWED[section]))}"
            )

    # 2. turn the networks into something we can check IPs against
    trusted, ignore = data.get("trusted", {}), data.get("ignore", {})
    networks = []
    for n in _strings("trusted", "networks", trusted.get("networks", [])):
        try:
            networks.append(ipaddress.ip_network(n.strip(), strict=False))
        except ValueError as e:
            raise ConfigError(f"{path}: '{n}' isn't a valid IP or network") from e

    return Config(
        trusted_networks=networks,
        trusted_devices=set(_strings("trusted", "devices", trusted.get("devices", []))),
        ignore_accounts=_strings("ignore", "accounts", ignore.get("accounts", [])),
        ignore_emails=_strings("ignore", "emails", ignore.get("emails", [])),
        ignore_keys=_strings("ignore", "keys", ignore.get("keys", [])),
        path=str(path),
    )