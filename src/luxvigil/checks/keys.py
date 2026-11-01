"""Shared or leaked keys: one API key showing up from all over the world at once.

A real customer uses their key from a couple of places, maybe three if they travel.
A key that hits 5+ countries in a single day has almost always been shared,
leaked in a public repo, or sold.

For each key we slide a time window (24h by default) over its requests and keep
the most countries seen inside any one window. Above the threshold, it gets flagged.

Cost: we count the requests that came from outside the key's home country
(the country of its very first request) as the suspicious part. It's not exact,
since the owner may travel, but it's a fair estimate and easy to check.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta


@dataclass
class SharedKey:
    key: str
    accounts: list[str]  # accounts the key belongs to (usually one)
    home_country: str
    peak_countries: int  # most countries seen within one window
    peak_start: object  # when that window started
    countries: Counter = field(default_factory=Counter)  # country -> requests, whole period
    ips: int = 0
    requests: int = 0  # requests from outside the home country
    cost_usd: float = 0.0

    @property
    def why(self):
        total = len(self.countries)
        more = f" ({total} over the whole period)" if total > self.peak_countries else ""
        return (
            f"key {self.key}: {self.peak_countries} countries in a single day{more}, "
            f"{self.ips:,} IPs, home is {self.home_country or '?'}"
        )


def _peak_countries(reqs, window):
    """Most distinct countries inside any time window. reqs must be sorted by time."""
    seen = Counter()
    best, best_start, lo = 0, None, 0
    for hi, r in enumerate(reqs):
        seen[r.country] += 1
        while r.timestamp - reqs[lo].timestamp > window:
            old = reqs[lo].country
            seen[old] -= 1
            if not seen[old]:
                del seen[old]
            lo += 1
        if len(seen) > best:
            best, best_start = len(seen), reqs[lo].timestamp
    return best, best_start


def find_shared_keys(requests, min_countries=5, window_hours=24):
    """Return the keys used from `min_countries`+ countries inside one window, worst first."""
    by_key = defaultdict(list)
    for r in requests:
        if r.api_key_id and r.country:
            by_key[r.api_key_id].append(r)

    window = timedelta(hours=window_hours)
    found = []
    for key, reqs in by_key.items():
        reqs.sort(key=lambda r: r.timestamp)
        peak, start = _peak_countries(reqs, window)
        if peak < min_countries:
            continue
        home = reqs[0].country
        away = [r for r in reqs if r.country != home]
        found.append(
            SharedKey(
                key=key,
                accounts=sorted({r.account_id for r in reqs}),
                home_country=home,
                peak_countries=peak,
                peak_start=start,
                countries=Counter(r.country for r in reqs),
                ips=len({r.ip for r in reqs}),
                requests=len(away),
                cost_usd=sum(r.cost_usd for r in away),
            )
        )
    return sorted(found, key=lambda k: k.cost_usd, reverse=True)
