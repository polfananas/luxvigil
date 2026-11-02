"""Spend spikes: an account that suddenly burns way more than it usually does.

Could be a stolen account, a leaked key, a script stuck in a loop, or someone
who found a way to resell your API. Whatever it is, you want to know.

For each account we take its usual daily spend (the median over the days it was
active before) and flag the days where it spent 10x that or more. Accounts need
a few days of history first, so brand new accounts never count as a spike.
There's also a dollar floor, so going from $0.01 to $0.10 doesn't set anything off.

Cost: only the extra spend counts (that day's spend minus the usual), not the whole day.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import median


@dataclass
class Spike:
    account: str
    usual_daily: float  # median daily spend before the spike
    peak_daily: float  # worst day
    first_day: object
    last_day: object
    requests: int  # requests on the spike days
    cost_usd: float  # extra spend over the usual
    days: list = field(default_factory=list)  # every spike day, for the report

    @property
    def times(self):
        return self.peak_daily / self.usual_daily if self.usual_daily else float("inf")

    @property
    def why(self):
        when = str(self.first_day) if self.first_day == self.last_day else f"{self.first_day} to {self.last_day}"
        return (
            f"{self.account} went from ~${self.usual_daily:,.2f}/day to ${self.peak_daily:,.2f}/day "
            f"({self.times:,.0f}x) on {when}"
        )


def find_spikes(requests, multiplier=10, min_history_days=3, min_daily_usd=1.0):
    """Return spend spikes, biggest extra cost first. Back-to-back spike days count as one spike."""
    daily = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))  # account -> day -> [spend, requests]
    for r in requests:
        day = daily[r.account_id][r.timestamp.date()]
        day[0] += r.cost_usd
        day[1] += 1

    spikes = []
    for account, days in daily.items():
        history = []  # spend on the normal days seen so far
        current = None
        for day in sorted(days):
            spend, n = days[day]
            usual = median(history) if len(history) >= min_history_days else None
            is_spike = usual is not None and spend >= max(usual * multiplier, min_daily_usd)

            if is_spike:
                if current and day - current.last_day <= timedelta(days=1):
                    current.last_day = day
                    current.peak_daily = max(current.peak_daily, spend)
                    current.requests += n
                    current.cost_usd += spend - current.usual_daily
                    current.days.append(day)
                else:
                    current = Spike(account, usual, spend, day, day, n, spend - usual, [day])
                    spikes.append(current)
            else:
                history.append(spend)  # spike days don't count towards "usual"

    return sorted(spikes, key=lambda s: s.cost_usd, reverse=True)
