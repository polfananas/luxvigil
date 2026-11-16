"""Account farms: lots of accounts that are secretly the same person (or the same bot).

Each account on its own looks fine. The giveaway is what they share:

  - the same device (device_id), even if they hop between IPs and countries
  - the same network (same /24 for IPv4, same /64 for IPv6)

A shared device is enough to link two accounts. A shared network isn't:
mobile carriers, universities and big offices put thousands of real people
behind a handful of IPs. So a network only links accounts that already look
off on their own (a throwaway email, or sending the same prompts as others).

How strong the evidence is depends on how it adds up:

  - direct: one device or network shared by `min_accounts` or more accounts
    on its own. Devices and networks like that which share accounts are one
    farm (a farm rotating through a few devices). That's likely abuse
  - chained: accounts linked in a chain of smaller shares (A and B on one
    device, B and C on another...) until the chain reaches `min_accounts`.
    No single device or network says much here: a support agent who logs in
    on customers' laptops, or a family computer, chain together the same way.
    So a chained group is only worth a look

Accounts that hang off a direct farm through smaller shares only (say, a farm
account that also logged in on a home laptop with someone else) are listed
with the farm as linked, but not counted as part of it.

Plain rule, no scoring: every farm lists the devices and networks that tied it together.

Cost: everything the linked accounts spent. The link is between accounts, not
requests, so there's no telling which of their requests were "the farm" and
which weren't. The report says it's the spend of the linked accounts, and
nothing more.

Limits worth knowing:
  - chained groups still chain: one account on two unrelated family computers
    joins both. That's why they're only worth a look
  - a careful farm that never puts more than a few accounts on one device only
    shows up as chained
  - a device shared months apart still links, the period shown is when any
    account in the farm was active, not when the device was shared
  - a network link leans on the other checks (throwaway email, template prompts),
    so a farm and a bot finding on the same accounts aren't two separate pieces
    of evidence
  - what a device_id means depends on where yours comes from: a browser
    fingerprint and a hardware id are not equally strong

What it won't catch: a careful farm where every account has its own device,
IPs from residential proxies all over the place and normal-looking emails.
Nothing is shared, so there's nothing to link.
"""

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from luxvigil.checks.emails import disposable_by_account, load_disposable_domains
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
    chained: bool = False  # True: no single device or network has min_accounts on it, see above
    largest_share: int = 0  # most accounts on any one device or network in the farm
    linked: list[str] = field(default_factory=list)  # hanging off a direct farm through smaller shares

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
        why = f"{len(self.accounts):,} accounts on " + " + ".join(bits)
        if self.chained:
            why += f", chained (at most {self.largest_share} on any one)"
        if self.linked:
            why += f" (+{len(self.linked)} more linked through smaller shares, not counted)"
        return why


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
    shared = list(shared_devices.values()) + list(shared_networks.values())
    everything = Groups()
    for accs in shared:
        everything.join_all(list(accs))

    # 4. the direct evidence: devices and networks with min_accounts or more on them.
    #    The ones that share accounts are one farm
    direct = Groups()
    for accs in shared:
        if len(accs) >= min_accounts:
            direct.join_all(list(accs))
    cores = list(direct.members().values())
    in_a_core = {a for core in cores for a in core}

    def farm(accs, chained=False, linked=()):
        return Farm(
            accounts=sorted(accs),
            devices=sorted(d for d, a in shared_devices.items() if a <= accs),
            networks=sorted(n for n, a in shared_networks.items() if a <= accs),
            chained=chained,
            largest_share=max((len(a) for a in shared if a <= accs), default=0),
            linked=sorted(linked),
        )

    # 5. direct farms first. Whatever else they chain to is listed, not counted
    farms = []
    for group in everything.members().values():
        if len(group) < min_accounts:
            continue
        mine = sorted((core for core in cores if core <= group), key=len, reverse=True)
        if not mine:
            farms.append(farm(group, chained=True))
            continue
        rest = group - in_a_core
        for i, core in enumerate(mine):
            farms.append(farm(core, linked=rest if i == 0 else ()))  # the extras go with the biggest one

    # 6. add up what each farm cost and when it was active
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

    return sorted(farms, key=lambda f: (f.cost_usd, f.requests), reverse=True)