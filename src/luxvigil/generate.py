"""Fake request logs with abuse planted on purpose, for demos and tests.

Normal users plus four kinds of abuse:
  - a big account farm: disposable emails, same IP range, a few shared devices, template prompts
  - a small farm: different IPs (VPN-style) but all on the same device
  - a shared/leaked key: one paying account's key used from lots of countries
  - a spend spike: a normal user who suddenly burns way more than usual
"""

import csv
import hashlib
import random
from datetime import datetime, timedelta, timezone

from luxvigil.schema import COLUMNS

PRICE_IN = 3 / 1_000_000  # $ per input token
PRICE_OUT = 15 / 1_000_000  # $ per output token

REAL_DOMAINS = ["gmail.com", "outlook.com", "yahoo.com", "icloud.com", "proton.me", "acme.io", "startup.dev"]
DISPOSABLE_DOMAINS = ["mailinator.com", "guerrillamail.com", "10minutemail.com", "sharklasers.com", "yopmail.com"]
COUNTRIES = ["US"] * 8 + ["GB", "DE", "ES", "FR", "IN", "BR", "CA", "NL", "JP", "MX"]
FAR_COUNTRIES = ["US", "BR", "RU", "VN", "NG", "ID", "TR", "PK", "UA", "CN", "PH", "EG"]


def _h(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16]


def _ip(rng, prefix=None):
    if prefix:
        return f"{prefix}.{rng.randint(2, 254)}"
    return f"{rng.randint(11, 223)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(2, 254)}"


def _row(ts, account, email, key, ip, country, device, prompt, tin, tout):
    return {
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "account_id": account,
        "email": email,
        "api_key_id": key,
        "ip": ip,
        "country": country,
        "device_id": device,
        "prompt_hash": prompt,
        "input_tokens": tin,
        "output_tokens": tout,
        "cost_usd": round(tin * PRICE_IN + tout * PRICE_OUT, 6),
    }


def generate(n_users=200, days=30, seed=42, start=None):
    """Return (rows, planted). planted says which accounts/keys are abusive."""
    if n_users < 10:
        raise ValueError("need at least 10 normal users")
    rng = random.Random(seed)
    start = start or datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = []
    planted = {"farm_big": [], "farm_device": [], "shared_key": [], "spike": []}

    def when(day_lo=0, day_hi=days):
        return start + timedelta(days=rng.uniform(day_lo, day_hi))

    # normal users
    for i in range(n_users):
        acc = f"acc_{i:04d}"
        email = f"user{i}@{rng.choice(REAL_DOMAINS)}"
        key = f"key_{_h(acc)}"
        country = rng.choice(COUNTRIES)
        ip = _ip(rng)
        device = f"dev_{_h(acc, 'd')}"
        for _ in range(rng.randint(5, 60)):
            tin, tout = rng.randint(200, 3000), rng.randint(100, 1500)
            rows.append(_row(when(), acc, email, key, ip, country, device, _h(acc, rng.random()), tin, tout))

    # big farm: disposable emails, same /24, 3 devices, 5 prompt templates
    templates = [_h("template", t) for t in range(5)]
    farm_devices = [f"dev_{_h('farm', d)}" for d in range(3)]
    for j in range(60):
        acc = f"acc_f{j:03d}"
        planted["farm_big"].append(acc)
        email = f"{_h(acc)[:8]}@{rng.choice(DISPOSABLE_DOMAINS)}"
        key = f"key_{_h(acc)}"
        signup_day = rng.uniform(3, days - 5)
        for _ in range(rng.randint(40, 70)):  # burn the free credits fast, then disappear
            tin, tout = rng.randint(2000, 4000), rng.randint(1500, 3000)
            rows.append(_row(when(signup_day, signup_day + 1.5), acc, email, key, _ip(rng, "185.220.101"),
                             "NL", rng.choice(farm_devices), rng.choice(templates), tin, tout))

    # small farm: VPN-style random IPs and countries, but one single device
    device = f"dev_{_h('vpnfarm')}"
    for j in range(25):
        acc = f"acc_v{j:03d}"
        planted["farm_device"].append(acc)
        email = f"{_h(acc)[:8]}@{rng.choice(REAL_DOMAINS[:3])}"
        key = f"key_{_h(acc)}"
        signup_day = rng.uniform(5, days - 3)
        country = rng.choice(FAR_COUNTRIES)
        ip = _ip(rng)
        for _ in range(rng.randint(30, 50)):
            tin, tout = rng.randint(1500, 3500), rng.randint(1000, 2500)
            rows.append(_row(when(signup_day, signup_day + 1), acc, email, key, ip, country, device,
                             _h(acc, rng.random()), tin, tout))

    # shared / leaked key: a normal paying account whose key shows up all over the world
    victim = f"acc_{7 % n_users:04d}"
    leaked_key = f"key_{_h(victim)}"
    planted["shared_key"].append(leaked_key)
    victim_email = next(r["email"] for r in rows if r["account_id"] == victim)
    leak_day = days - 8
    for _ in range(400):
        tin, tout = rng.randint(1000, 4000), rng.randint(1000, 4000)
        rows.append(_row(when(leak_day, leak_day + 3), victim, victim_email, leaked_key, _ip(rng),
                         rng.choice(FAR_COUNTRIES), f"dev_{_h(rng.random())}", _h("leak", rng.random()), tin, tout))

    # spend spike: a normal user who goes 10x+ in the last few days
    spiker = f"acc_{(n_users // 2) % n_users:04d}"
    planted["spike"].append(spiker)
    base = next(r for r in rows if r["account_id"] == spiker)
    for _ in range(300):
        tin, tout = rng.randint(3000, 8000), rng.randint(2000, 6000)
        rows.append(_row(when(days - 3, days), spiker, base["email"], base["api_key_id"], base["ip"],
                         base["country"], base["device_id"], _h(spiker, rng.random()), tin, tout))

    rows.sort(key=lambda r: r["timestamp"])
    return rows, planted


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
