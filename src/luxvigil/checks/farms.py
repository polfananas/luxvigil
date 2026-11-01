"""Account farms: lots of accounts that are secretly the same person (or the same bot).

Each account on its own looks fine. The giveaway is what they share:

  - the same device (device_id), even if they hop between IPs and countries
  - the same network (same /24 for IPv4, same /64 for IPv6)

Two accounts are linked if they share either one. Linked accounts get grouped
(if A shares a device with B and B shares a network with C, that's one group),
and a group is flagged as a farm once it has `min_accounts` or more.

Plain rule, no scoring: every farm lists the devices and networks that tied it together.
"""

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field

from luxvigil.checks.groups import Groups


@dataclass
class Farm:
    accounts: list[str]
    devices: list[str]  # shared devices that link the accounts
    networks: list[str]  # shared networks that link the accounts
    requests: int = 0
    cost_usd: float = 0.0
    first_seen: object = None
    last_seen: object = None
    emails: list[str] = field(default_factory=list)

    @property
    def why(self):
        bits = []
        if self.devices:
            n = len(self.devices)
            bits.append(f"{n} shared device{'s' if n > 1 else ''}")
        if self.networks:
            n = len(self.networks)
            nets = ", ".join(self.networks[:2]) + ("…" if n > 2 else "")
            bits.append(f"{n} shared network{'s' if n > 1 else ''} ({nets})")
        return f"{len(self.accounts)} accounts on " + " + ".join(bits)


def network_of(ip):
    """'185.220.101.7' -> '185.220.101.0/24'. IPv6 goes to /64. Junk -> None."""
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    prefix = 24 if addr.version == 4 else 64
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def find_farms(requests, min_accounts=5):
    """Return the farms found in `requests`, biggest cost first."""
    by_device = defaultdict(set)
    by_network = defaultdict(set)
    for r in requests:
        if r.device_id:
            by_device[r.device_id].add(r.account_id)
        net = network_of(r.ip)
        if net:
            by_network[net].add(r.account_id)

    # only things shared by 2+ accounts can link anything
    shared_devices = {d: accs for d, accs in by_device.items() if len(accs) > 1}
    shared_networks = {n: accs for n, accs in by_network.items() if len(accs) > 1}

    groups = Groups()
    for accs in list(shared_devices.values()) + list(shared_networks.values()):
        first, *rest = accs
        for acc in rest:
            groups.join(first, acc)

    members = defaultdict(set)
    for acc in groups.parent:
        members[groups.find(acc)].add(acc)

    farms = {}
    for root, accs in members.items():
        if len(accs) < min_accounts:
            continue
        farms[root] = Farm(
            accounts=sorted(accs),
            devices=sorted(d for d, a in shared_devices.items() if a <= accs),
            networks=sorted(n for n, a in shared_networks.items() if a <= accs),
        )

    # add up what each farm cost
    owner = {acc: farm for farm in farms.values() for acc in farm.accounts}
    emails = defaultdict(set)
    for r in requests:
        farm = owner.get(r.account_id)
        if farm is None:
            continue
        farm.requests += 1
        farm.cost_usd += r.cost_usd
        emails[id(farm)].add(r.email)
        if farm.first_seen is None or r.timestamp < farm.first_seen:
            farm.first_seen = r.timestamp
        if farm.last_seen is None or r.timestamp > farm.last_seen:
            farm.last_seen = r.timestamp
    for farm in farms.values():
        farm.emails = sorted(emails[id(farm)])

    return sorted(farms.values(), key=lambda f: f.cost_usd, reverse=True)
