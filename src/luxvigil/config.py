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
"""

import ipaddress
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

DEFAULT_PATH = "luxvigil.toml"

ALLOWED = {
    "trusted": {"networks", "devices"},
    "ignore": {"accounts", "emails", "keys"},
}


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    trusted_networks: list = field(default_factory=list)
    trusted_devices: set = field(default_factory=set)
    ignore_accounts: list = field(default_factory=list)
    ignore_emails: list = field(default_factory=list)
    ignore_keys: list = field(default_factory=list)
    path: str = ""

    def trusts_ip(self, ip):
        try:
            addr = ipaddress.ip_address(ip.strip())
        except ValueError:
            return False
        return any(addr in net for net in self.trusted_networks)

    def ignores(self, r):
        return (
            _matches(r.account_id, self.ignore_accounts)
            or _matches(r.email, self.ignore_emails)
            or _matches(r.api_key_id, self.ignore_keys)
        )


def _matches(value, patterns):
    value = value.lower()
    return any(fnmatchcase(value, p.lower()) for p in patterns)


def _strings(section, key, value):
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

    for section, values in data.items():
        if section not in ALLOWED:
            raise ConfigError(f"{path}: unknown section [{section}] (use [trusted] or [ignore])")
        unknown = set(values) - ALLOWED[section]
        if unknown:
            raise ConfigError(
                f"{path}: unknown key(s) in [{section}]: {', '.join(sorted(unknown))}. "
                f"Allowed: {', '.join(sorted(ALLOWED[section]))}"
            )

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
