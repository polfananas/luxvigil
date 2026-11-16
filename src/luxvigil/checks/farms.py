"""Account farms: lots of accounts that are secretly the same person (or the same bot).

Each account on its own looks fine. The giveaway is what they share:

  - the same device (device_id), even if they hop between IPs and countries
  - the same network (same /24 for IPv4, same /64 for IPv6)

A shared device is enough to link two accounts. A shared network isn't:
mobile carriers, universities and big offices put thousands of real people
behind a handful of IPs. So a network only links accounts that already look
off on their own (a throwaway email, or sending the same prompts as others).

Three separate questions, each answered on its own:

  1. how are they related?
     - direct: one device or network shared by `min_accounts` or more accounts
     - chained: only a chain of smaller shares (A and B on one device, B and C
       on another...) gets to `min_accounts`. A support agent logging in on
       customers' laptops, or a family computer, chain together the same way
  2. when did they show up on it? A device or network is "concentrated" when
     `min_accounts` accounts were first seen on it within `window_days` (30 by
     default, set in luxvigil.toml). Farms tend to come in bursts. Five people
     using a library computer months apart don't
  3. anything else off about them? At least half of the accounts have a
     throwaway email or send a bot's repeated prompts

Only all three together (direct, concentrated, and something else off) make a
farm that counts as likely abuse. A concentrated group with nothing else off
(10 students on the same lab image the same morning look exactly like a farm
with clean emails), a direct share spread over months, or a chain: all worth
a look, and the report says which one it is.

"First seen" is the first request we see from that account on that device in
these logs. It's not when the account was created, which we don't know, and if
the logs start in the middle of things, accounts that used the device for years
look like they all just arrived. The report says so when that may be the case.

How a burst is found, per device or network: sort the accounts by when they
were first seen on it, and from the earliest one, look `window_days` ahead
(the boundary counts: exactly 30 days later is in). Enough accounts there:
that's a burst, and the search goes on after it. Not enough: try again from
the next account. So a burst is never longer than `window_days`, two bursts
months apart stay two, and an account is in at most one burst per device.
Bursts on different devices or networks are one farm when they share accounts
and are close in time (within `window_days` of each other).

Cost, for a likely farm: the requests its accounts made on the shared devices
and networks, that's what the evidence is about. Whatever else they did is
worth a look. For everything else, all of what the accounts spent is worth a
look. Accounts that hang off a farm only through smaller shares, or showed up
on its device outside the burst, are listed with it (linked) but not counted.

Limits worth knowing:
  - chains still chain: one account on two unrelated family computers joins
    both, that's why they're only worth a look
  - a farm that never puts more than a few accounts on one device, or spreads
    its signups over more than `window_days`, only shows up as worth a look
  - a busy kiosk or a class gets 5 new users in a day: concentrated, and only
    "something else off" keeps it out of likely abuse
  - a network link leans on the other checks (throwaway email, template prompts),
    so a farm and a bot finding on the same accounts aren't two separate pieces
    of evidence
  - what a device_id means depends on where yours comes from: a browser
    fingerprint and a hardware id are not equally strong
  - 30 days is a reasonable guess, not a measured number. Nobody has labeled
    AI API logs to calibrate it on

What it won't catch: a careful farm where every account has its own device,
IPs from residential proxies all over the place and normal-looking emails.
Nothing is shared, so there's nothing to link.
"""

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from functools import lru_cache

from luxvigil.checks.emails import disposable_by_account, load_disposable_domains
from luxvigil.checks.groups import Groups
from luxvigil.checks.prompts import find_prompt_bots

DEFAULT_WINDOW_DAYS = 30  # see "concentrated" at the top. Change it in luxvigil.toml: [farms] window_days


@dataclass
class Farm:
    accounts: list[str]
    devices: list[str]  # shared devices that link the accounts
    networks: list[str]  # shared networks that link the accounts
    chained: bool = False  # no single device or network has min_accounts on it, see the top
    concentrated: bool = False  # min_accounts first seen on one of them within window_days
    corroborated: int = 0  # accounts with something else off (throwaway email, bot prompts)
    largest_share: int = 0  # most accounts on any one device or network in the farm
    arrived_from: object = None  # first and last "first seen on the shared device or network",
    arrived_to: object = None  # i.e. the burst. For a group that isn't concentrated, the whole spread
    earlier_use_unknown: bool = False  # the logs start right before: they may have been there before
    linked: list[str] = field(default_factory=list)  # related, but not part of it (see the top)
    requests: int = 0  # all requests from the accounts
    cost_usd: float = 0.0
    signal_requests: int = 0  # only the ones made on the shared devices and networks
    signal_cost_usd: float = 0.0
    first_seen: object = None  # first and last request from any account in the farm,
    last_seen: object = None  # i.e. when the accounts were active

    @property
    def likely(self):
        """Direct, concentrated, and at least half the accounts have something else off."""
        return not self.chained and self.concentrated and 2 * self.corroborated >= len(self.accounts)

    def is_signal(self, r):
        """A request by one of the farm's accounts on one of its shared devices or networks."""
        if r.account_id not in self._accounts:
            return False
        return r.device_id in self._devices or (bool(self._networks) and network_of(r.ip) in self._networks)

    def __post_init__(self):
        self._accounts, self._devices, self._networks = set(self.accounts), set(self.devices), set(self.networks)

    @property
    def why(self):
        """The one-liner that shows up in the report."""
        bits = []
        if self.devices:
            n = len(self.devices)
            bits.append(f"{n} shared device{'s' if n > 1 else ''}")
        if self.networks:
            n = len(self.networks)
            nets = ", ".join(self.networks[:2]) + ("…" if n > 2 else "")  # don't print 40 networks
            bits.append(f"{n} shared network{'s' if n > 1 else ''} ({nets})")
        on = " + ".join(bits)
        n = len(self.accounts)
        span = _days(self.arrived_from, self.arrived_to)
        if self.chained:
            why = f"{n:,} accounts linked in a chain over {on} (at most {self.largest_share} on any one)"
        elif self.concentrated:
            why = f"{n:,} accounts first seen on {on} within {span}"
        else:
            why = f"{n:,} accounts on {on}, first seen there over {span} (spread out: shared or second-hand?)"
        if self.corroborated:
            who = "all" if self.corroborated == n else f"{self.corroborated}"
            why += f", {who} with throwaway emails or bot prompts"
        elif self.concentrated and not self.chained:
            why += ", nothing else off about them (a class or a kiosk looks the same)"
        if self.linked:
            why += f" (+{len(self.linked)} more related, not counted)"
        return why


def _days(start, end):
    """How long between two times, said simply: "the same day", "3 days", "4 months"."""
    if start is None or end is None:
        return "an unknown time"
    days = (end - start).total_seconds() / 86400
    if days < 1 and start.date() == end.date():
        return "the same day"
    if round(days) <= 1:
        return "a day"
    if days < 60:
        return f"{round(days)} days"
    return f"{round(days / 30)} months"


# The same IPs show up over and over in a log, so each one is only worked out once.
@lru_cache(maxsize=1 << 16)
def network_of(ip):
    """'185.220.101.7' -> '185.220.101.0/24'. IPv6 goes to /64. Junk -> None.

    A /24 is the 256 addresses that share the first three numbers. Farms usually
    rent a block like that, or sit behind one provider that hands out IPs from it.
    For IPv6, a /64 is roughly what one home or one server gets.
    """
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    prefix = 24 if addr.version == 4 else 64
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def suspect_accounts(requests, bots=None):
    """Accounts that look off on their own: throwaway email, or part of a copy-paste bot.

    Only these can be linked through a shared network (see the top of the file).
    `bots` lets the caller pass find_prompt_bots() if it already ran it.
    """
    # any of the account's emails, so the order of the rows doesn't matter
    out = set(disposable_by_account(requests, load_disposable_domains()))

    for bot in find_prompt_bots(requests) if bots is None else bots:
        out.update(bot.accounts)
    return out


def find_farms(requests, min_accounts=5, config=None, suspects=None, window_days=None):
    """Return the farms found in `requests`: likely ones first, then biggest cost first.

    Devices and networks trusted in the config never link accounts.
    `suspects` lets the caller pass suspect_accounts() if it already has it.
    `window_days`: how close together accounts have to show up to count as a burst.
    None takes it from the config (30 by default), 0 means no time limit at all.
    """
    suspects = suspect_accounts(requests) if suspects is None else suspects
    if window_days is None:
        window_days = config.farm_window_days if config else DEFAULT_WINDOW_DAYS
    window = timedelta(days=window_days) if window_days else None

    # 1. who uses what, and since when: device -> {account: first seen on it}, same for networks
    by_device = defaultdict(dict)
    by_network = defaultdict(dict)
    logs_start = None
    for r in requests:
        logs_start = r.timestamp if logs_start is None or r.timestamp < logs_start else logs_start
        if r.device_id and not (config and r.device_id in config.trusted_devices):
            _seen(by_device[r.device_id], r)
        # networks only count for accounts that already look off, and never for trusted ones
        if r.account_id not in suspects or (config and config.trusts_ip(r.ip)):
            continue
        net = network_of(r.ip)
        if net:
            _seen(by_network[net], r)

    # 2. a device or network used by a single account doesn't link anything
    shared = {("device", d): seen for d, seen in by_device.items() if len(seen) > 1}
    shared.update({("network", n): seen for n, seen in by_network.items() if len(seen) > 1})

    # 3. the bursts: min_accounts first seen on the same device or network within the window
    bursts = [(key, *b) for key, seen in shared.items() for b in _bursts(seen, min_accounts, window)]

    # 4. bursts that share accounts and are close in time are the same farm (one rotating
    #    through devices). Far apart in time, they stay separate, so nothing grows forever
    same = Groups()
    for i, (_, accs_i, start_i, end_i) in enumerate(bursts):
        same.join(i, i)
        for j in range(i):
            _, accs_j, start_j, end_j = bursts[j]
            close = window is None or (start_j <= end_i + window and start_i <= end_j + window)
            if close and accs_i & accs_j:
                same.join(i, j)

    def farm(accs, keys, arrived, **kw):
        return Farm(
            accounts=sorted(accs),
            devices=sorted(name for kind, name in keys if kind == "device"),
            networks=sorted(name for kind, name in keys if kind == "network"),
            corroborated=len(accs & suspects),
            largest_share=max((len(shared[k].keys() & accs) for k in keys), default=0),
            arrived_from=min(arrived), arrived_to=max(arrived),
            **kw,
        )

    farms, in_a_burst = [], set()
    for members in same.members().values():
        accs = set().union(*(bursts[i][1] for i in members))
        keys = {bursts[i][0] for i in members}
        start = min(bursts[i][2] for i in members)
        arrived = [start, max(bursts[i][3] for i in members)]
        # a burst right at the start of the logs may just be where the logs start
        early = window is not None and start - logs_start < window
        farms.append(farm(accs, keys, arrived, concentrated=True, earlier_use_unknown=early))
        in_a_burst |= accs

    # 5. everything else that's linked (spread out in time, or a chain): worth a look.
    #    Accounts related to a burst but not in it are listed with it, not counted
    everything = Groups()
    for seen in shared.values():
        everything.join_all(list(seen))
    for group in everything.members().values():
        rest = group - in_a_burst
        if rest and len(group) > len(rest):  # hangs off a farm: listed with the biggest one in it
            host = max((f for f in farms if set(f.accounts) <= group), key=lambda f: len(f.accounts))
            host.linked = sorted(set(host.linked) | rest)
        elif len(rest) >= min_accounts:
            keys = {k for k, seen in shared.items() if seen.keys() & rest}
            chained = max(len(shared[k].keys() & rest) for k in keys) < min_accounts
            arrived = [t for k in keys for a, t in shared[k].items() if a in rest]
            farms.append(farm(rest, keys, arrived, chained=chained))

    # 6. add up what each farm cost and when its accounts were active
    owner = {acc: farm for farm in farms for acc in farm.accounts}
    for r in requests:
        farm = owner.get(r.account_id)
        if farm is None:
            continue
        farm.requests += 1
        farm.cost_usd += r.cost_usd
        if farm.is_signal(r):
            farm.signal_requests += 1
            farm.signal_cost_usd += r.cost_usd
        if farm.first_seen is None or r.timestamp < farm.first_seen:
            farm.first_seen = r.timestamp
        if farm.last_seen is None or r.timestamp > farm.last_seen:
            farm.last_seen = r.timestamp

    return sorted(farms, key=lambda f: (f.likely, f.cost_usd, f.requests), reverse=True)


def _seen(first, r):
    """Keep the earliest time this account was seen here, whatever order the rows come in."""
    t = first.get(r.account_id)
    if t is None or r.timestamp < t:
        first[r.account_id] = r.timestamp


def _bursts(first_seen, min_accounts, window):
    """{account: first seen} -> [(accounts, start, end)], see the top of the file.

    window None means no time limit: everyone on it is one burst, if there are enough.
    """
    arrivals = sorted((t, a) for a, t in first_seen.items())  # ties broken by account, so it's stable
    out, i = [], 0
    while i < len(arrivals):
        start = arrivals[i][0]
        j = i
        while j < len(arrivals) and (window is None or arrivals[j][0] - start <= window):
            j += 1
        if j - i >= min_accounts:
            out.append(({a for _, a in arrivals[i:j]}, start, arrivals[j - 1][0]))
            i = j
        else:
            i += 1
    return out
