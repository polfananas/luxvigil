"""Account farms: lots of accounts that are secretly the same person (or the same bot).

Each account on its own looks fine. The giveaway is what they share:

  - the same device (device_id), even if they hop between IPs and countries
  - the same network (same /24 for IPv4, same /64 for IPv6)

A shared device is enough to link two accounts. A shared network isn't:
mobile carriers, universities and big offices put thousands of real people
behind a handful of IPs. So a network only links accounts that already look
off on their own (a throwaway email, or sending the same prompts as others).

Linked accounts get grouped (if A shares a device with B and B shares a
network with C, that's one group), and a group is flagged as a farm once it
has `min_accounts` or more.

Plain rule, no scoring: every farm lists the devices and networks that tied it together.

What it won't catch: a careful farm where every account has its own device,
IPs from residential proxies all over the place and normal-looking emails.
Nothing is shared, so there's nothing to link.
"""

import ipaddress
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from luxvigil.checks.emails import disposable_domain, load_disposable_domains
from luxvigil.checks.groups import Groups
from luxvigil.checks.prompts import find_prompt_bots


@dataclass
class Farm:
    accounts: list[str]
    devices: list[str]  # shared devices that link the accounts
    networks: list[str]  # shared networks that link the accounts
    requests: int = 0
    cost_usd: float = 0.0
    first_seen: object = None  # first and last request from any account in the farm,
    last_seen: object = None  # i.e. when the farm was active

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
            bits.append(f"{n} shared network{'s' if n > 1 else ''} ({nets}, with throwaway emails or template prompts)")
        return f"{len(self.accounts)} accounts on " + " + ".join(bits)


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
    domains = load_disposable_domains()

    # one email per account is enough, no need to check it on every request
    first_email = {}
    for r in requests:
        first_email.setdefault(r.account_id, r.email)
    out = {acc for acc, email in first_email.items() if disposable_domain(email, domains)}

    for bot in find_prompt_bots(requests) if bots is None else bots:
        out.update(bot.accounts)
    return out


def find_farms(requests, min_accounts=5, config=None, suspects=None):
    """Return the farms found in `requests`, biggest cost first.

    Devices and networks trusted in the config never link accounts.
    `suspects` lets the caller pass suspect_accounts() if it already has it.
    """
    suspects = suspect_accounts(requests) if suspects is None else suspects

    # 1. who uses what: device -> accounts, network -> (suspicious) accounts
    by_device = defaultdict(set)
    by_network = defaultdict(set)
    for r in requests:
        if r.device_id and not (config and r.device_id in config.trusted_devices):
            by_device[r.device_id].add(r.account_id)
        # networks only count for accounts that already look off, and never for trusted ones
        if r.account_id not in suspects or (config and config.trusts_ip(r.ip)):
            continue
        net = network_of(r.ip)
        if net:
            by_network[net].add(r.account_id)

    # 2. a device or network used by a single account doesn't link anything
    shared_devices = {d: accs for d, accs in by_device.items() if len(accs) > 1}
    shared_networks = {n: accs for n, accs in by_network.items() if len(accs) > 1}

    # 3. link every account to the others it shares something with. Links chain:
    #    A-B share a device and B-C share a network, so A, B and C end up together
    groups = Groups()
    for accs in list(shared_devices.values()) + list(shared_networks.values()):
        groups.join_all(list(accs))

    # 4. groups big enough are farms. Each farm keeps the devices and networks
    #    that tied it together, that's the "why" in the report
    farms = []
    for accs in groups.members().values():
        if len(accs) < min_accounts:
            continue
        farms.append(Farm(
            accounts=sorted(accs),
            devices=sorted(d for d, a in shared_devices.items() if a <= accs),
            networks=sorted(n for n, a in shared_networks.items() if a <= accs),
        ))

    # 5. add up what each farm cost and when it was active
    owner = {acc: farm for farm in farms for acc in farm.accounts}
    for r in requests:
        farm = owner.get(r.account_id)
        if farm is None:
            continue
        farm.requests += 1
        farm.cost_usd += r.cost_usd
        if farm.first_seen is None or r.timestamp < farm.first_seen:
            farm.first_seen = r.timestamp
        if farm.last_seen is None or r.timestamp > farm.last_seen:
            farm.last_seen = r.timestamp

    return sorted(farms, key=lambda f: f.cost_usd, reverse=True)