"""Optional luxvigil.toml, for telling luxvigil what's fine in your setup.

    [trusted]
    networks = ["203.0.113.0/24"]   # office, VPN exit, a customer's office...
    devices = ["kiosk-01"]

    [ignore]
    accounts = ["qa_*"]
    emails = ["*@yourcompany.com"]
    keys = ["key_ci_*"]

    [farms]
    window_days = 30   # how close together accounts have to show up on a device (0: no limit)

    [prices]           # $ per 1M tokens, [input, output], by the model name in your logs
    "gpt-4o" = [2.5, 10]
    "claude-sonnet-*" = [3, 15]

Trusted networks and devices still get analyzed, they just never link accounts
into a farm. Ignored accounts, emails and keys are left out of the checks
completely (they still count towards your total spend).

Prices are only used for requests with no cost in the logs and a model column
(see schema.py). An exact model name wins over a pattern, then the first pattern
that matches. Models with no price fall back to --price-in/--price-out if you
passed them, otherwise their cost is unknown.

Patterns use shell-style wildcards (* and ?) and don't care about case.
Anything we don't recognise is an error rather than silently skipped: a typo
like [ignored] would otherwise mean your allowlist quietly does nothing.
"""

import ipaddress
import math
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from luxvigil.checks.farms import DEFAULT_WINDOW_DAYS

DEFAULT_PATH = "luxvigil.toml"

# section -> the keys it can have
ALLOWED = {
    "trusted": {"networks", "devices"},
    "ignore": {"accounts", "emails", "keys"},
    "farms": {"window_days"},
    "prices": None,  # any model name
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
    farm_window_days: int = DEFAULT_WINDOW_DAYS  # see checks/farms.py
    prices: dict = field(default_factory=dict)  # model name or pattern (lowercase) -> ($ in, $ out) per 1M tokens
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


def _prices(path, section):
    """[prices]: "model" = [input, output], $ per 1M tokens, both 0 or more."""
    out = {}
    for model, value in section.items():
        ok = (isinstance(value, list) and len(value) == 2
              and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
              and all(math.isfinite(v) and v >= 0 for v in value))
        if not ok:
            raise ConfigError(f'{path}: [prices] "{model}" should be [input, output], $ per 1M tokens, '
                              f"like [2.5, 10], got {value!r}")
        out[model.strip().lower()] = (float(value[0]), float(value[1]))
    return out


def _window(path, value):
    """[farms] window_days: a whole number of days, 0 for no time limit."""
    # bool is an int in Python, so `window_days = true` would quietly mean 1
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3650:
        raise ConfigError(f"{path}: [farms] window_days should be a whole number of days from 0 to 3650 "
                          f"(0 means no time limit), got {value!r}")
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
            raise ConfigError(f"{path}: unknown section [{section}] (use [trusted], [ignore], [farms] or [prices])")
        if not isinstance(values, dict):
            # e.g. `trusted = "1.2.3.4"` at the top instead of a [trusted] section
            raise ConfigError(f"{path}: {section} should be a [{section}] section")
        if ALLOWED[section] is None:
            continue
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
        farm_window_days=_window(path, data.get("farms", {}).get("window_days", DEFAULT_WINDOW_DAYS)),
        prices=_prices(path, data.get("prices", {})),
        path=str(path),
    )