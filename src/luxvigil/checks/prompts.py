"""Copy-paste bots: different accounts sending the exact same prompts again and again.

People write their own prompts. Bots run templates. When the same prompt
(by hash, we never see the text) comes from 5+ accounts and each of them sends
it several times, that's a script, not a coincidence.

The "several times" part matters: lots of real users type "hi" or "test" once,
so a prompt shared by many accounts on its own isn't enough.

Accounts that share any flagged prompt end up in the same group, so a bot
rotating through 5 templates shows up as one group, not five.
"""

from collections import defaultdict
from dataclasses import dataclass

from luxvigil.checks.groups import Groups


@dataclass
class PromptBot:
    accounts: list[str]
    prompts: list[str]  # the repeated prompt hashes that tie the group together
    repeats: int  # times those prompts were sent in total
    requests: int = 0
    cost_usd: float = 0.0

    @property
    def why(self):
        n = len(self.prompts)
        what = "the same prompt" if n == 1 else f"the same {n} prompts"
        return f"{len(self.accounts)} accounts sending {what} over and over ({self.repeats:,} times)"


def find_prompt_bots(requests, min_accounts=5, min_repeats=3):
    """Return the groups of accounts running the same prompts, biggest cost first.

    A prompt counts as repeated when `min_accounts`+ accounts send it and they
    send it `min_repeats`+ times each on average.
    """
    sends = defaultdict(lambda: defaultdict(int))  # prompt -> account -> times sent
    for r in requests:
        if r.prompt_hash:
            sends[r.prompt_hash][r.account_id] += 1

    repeated = {}
    for prompt, per_account in sends.items():
        total = sum(per_account.values())
        if len(per_account) >= min_accounts and total / len(per_account) >= min_repeats:
            repeated[prompt] = per_account

    if not repeated:
        return []

    groups = Groups()
    for per_account in repeated.values():
        groups.join_all(list(per_account))

    bots = []
    for accs in groups.members().values():
        prompts = sorted(p for p, per_account in repeated.items() if set(per_account) <= accs)
        bots.append(
            PromptBot(
                accounts=sorted(accs),
                prompts=prompts,
                repeats=sum(sum(repeated[p].values()) for p in prompts),
            )
        )

    owner = {acc: bot for bot in bots for acc in bot.accounts}
    for r in requests:
        bot = owner.get(r.account_id)
        if bot:
            bot.requests += 1
            bot.cost_usd += r.cost_usd

    return sorted(bots, key=lambda b: b.cost_usd, reverse=True)
