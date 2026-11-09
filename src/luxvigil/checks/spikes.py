"""Spend spikes: an account that suddenly burns way more than it usually does.

Could be a stolen account, a leaked key, a script stuck in a loop, or someone
who found a way to resell your API. Whatever it is, you want to know.

For each account we take its usual daily spend (the median over the days it was
active before) and flag the days where it spent 10x that or more. Accounts need
a few days of history first, so brand new accounts never count as a spike.
There's also a dollar floor, so going from $0.01 to $0.10 doesn't set anything off.

If the higher spend just keeps going for a week, we stop calling it a spike:
that's what a customer who started using you for real looks like. The first
week still shows up, flagged as "and it stayed there", so you can check it.

Cost: only the extra spend counts (that day's spend minus the usual), not the whole day.

Limits worth knowing:
  - days are UTC calendar days, so a burst around midnight gets split in two
  - a slow ramp (2x a week for a month) never jumps 10x in one day and isn't caught
  - a spike is just "more than usual": a launch or a batch job looks the same,
    which is why spikes only count as "worth a look" in the report
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
    sustained: bool = False  # still high after a week: probably the new normal

    @property
    def times(self):
        return self.peak_daily / self.usual_daily if self.usual_daily else None

    @property
    def why(self):
        """The one-liner that shows up in the report."""
        when = str(self.first_day) if self.first_day == self.last_day else f"{self.first_day} to {self.last_day}"
        jump = f" ({self.times:,.0f}x)" if self.times else ""  # nothing to divide by if it was spending $0
        tail = ", and it stayed there" if self.sustained else ""
        return (
            f"{self.account} went from ~${self.usual_daily:,.2f}/day to ${self.peak_daily:,.2f}/day"
            f"{jump} on {when}{tail}"
        )


def find_spikes(requests, multiplier=10, min_history_days=3, min_daily_usd=1.0, new_normal_days=7):
    """Return spend spikes, biggest extra cost first. Back-to-back spike days count as one spike.

    After `new_normal_days` spike days in a row, the higher spend becomes the account's
    new normal: the spike is closed and those days start counting as usual spend.
    """
    # 1. spend and requests per account per day: account -> day -> [spend, requests]
    daily = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    for r in requests:
        day = daily[r.account_id][r.timestamp.date()]
        day[0] += r.cost_usd
        day[1] += 1

    spikes = []
    for account, days in daily.items():
        history = []  # spend on the normal days seen so far
        current = None  # the spike we're in the middle of, if any

        # 2. walk the account's days in order, comparing each one to what was usual before it
        for day in sorted(days):
            spend, n = days[day]
            usual = median(history) if len(history) >= min_history_days else None
            is_spike = usual is not None and spend >= max(usual * multiplier, min_daily_usd)

            # 3. a week of "spikes" in a row is the new normal, not a spike anymore
            if is_spike and current and current.sustained:
                history.append(spend)
                continue

            if is_spike:
                if current and day - current.last_day <= timedelta(days=1):
                    # the spike goes on: extend it
                    current.last_day = day
                    current.peak_daily = max(current.peak_daily, spend)
                    current.requests += n
                    current.cost_usd += spend - current.usual_daily
                    current.days.append(day)
                    if len(current.days) >= new_normal_days:
                        current.sustained = True
                        history.extend([spend] * len(current.days))  # the higher level is usual now
                else:
                    current = Spike(account, usual, spend, day, day, n, spend - usual, [day])
                    spikes.append(current)
            else:
                current = None
                history.append(spend)  # spike days don't count towards "usual"

    return sorted(spikes, key=lambda s: s.cost_usd, reverse=True)