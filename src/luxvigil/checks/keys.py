"""Shared or leaked keys: one API key showing up from all over the world at once.

A real customer uses their key from a couple of places, maybe three if they travel.
A key that hits 5+ countries in a single day has almost always been shared,
leaked in a public repo, or sold.

For each key we slide a time window (24h by default) over its requests and keep
the most countries seen inside any one window. Above the threshold, it gets flagged.

Cost: the requests from outside the key's home country count as the suspicious
part. Home is the country the key was used from the most *before* things went
weird, so a leak with hundreds of requests can't take it over, and an owner who
happened to be travelling on day one doesn't break it either.

Limits worth knowing:
  - a customer running the same key on servers in several cloud regions can
    look like a leak (that's why it's in the "innocent explanation" of --json)
  - a key leaked and used only from the owner's own country won't show up here
  - countries come from IP geolocation, so VPNs blur the picture
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
    window_hours: int = 24

    @property
    def why(self):
        """The one-liner that shows up in the report."""
        total = len(self.countries)
        more = f" ({total} over the whole period)" if total > self.peak_countries else ""
        window = "a single day" if self.window_hours == 24 else f"{self.window_hours}h"
        return (
            f"key {self.key}: {self.peak_countries} countries in {window}{more}, "
            f"{self.ips:,} IPs, home is {self.home_country or '?'}"
        )


def _peak_countries(reqs, window):
    """Most distinct countries inside any time window. reqs must be sorted by time.

    Sliding window: `lo` is the oldest request still inside the window. Every new
    request goes in, and requests older than `window` drop out from the front.
    `seen` counts requests per country inside the window, so its size is the
    number of countries right now.
    """
    seen = Counter()
    best, best_start, lo = 0, None, 0
    for r in reqs:
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


def _home_country(reqs, peak_start):
    """Where the key normally lives: its most used country before the busiest window.

    If there's nothing before it (the logs start mid-leak), fall back to the
    country of the very first request.
    """
    before = Counter(r.country for r in reqs if r.timestamp < peak_start)
    if before:
        return before.most_common(1)[0][0]
    return reqs[0].country


def find_shared_keys(requests, min_countries=5, window_hours=24):
    """Return the keys used from `min_countries`+ countries inside one window, worst first."""
    # group requests by key, skipping rows with no key or no country to go on
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
        home = _home_country(reqs, start)
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
                window_hours=window_hours,
            )
        )
    return sorted(found, key=lambda k: k.cost_usd, reverse=True)