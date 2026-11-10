"""Copy-paste bots: different accounts sending the exact same prompts again and again.

People write their own prompts. Bots run templates. When the same prompt
(by hash, we never see the text) is sent by 5+ accounts that each send it
3+ times themselves, that's a script, not a coincidence.

The "each send it 3+ times" part matters: lots of real users type "hi" or
"test" once, so a prompt shared by many accounts isn't enough on its own.
And it's per account, not an average: one power user sending a prompt 100
times shouldn't drag four people who sent it once into a bot.

Repeating it isn't enough either. On real chat logs, short follow-ups like
"more", "shorter" or "continue" get typed over and over by thousands of people,
so plenty of them clear the 3+ bar by chance. The difference is who else sends
it: a bot template is sent almost only by the bot, a common phrase is sent by
lots of people and most of them don't repeat it. So a prompt only counts if at
least 20% of the accounts that ever sent it are repeating it. (Found running
luxvigil on WildChat, see benchmarks/: without this rule "more" and "hi" chained
2,262 strangers into one fake bot.)

Accounts that share any flagged prompt end up in the same group, so a bot
rotating through 5 templates shows up as one group, not five.

Limits worth knowing:
  - a legit integration sending a fixed template on behalf of its users can
    look the same (that's in the "innocent explanation" of --json)
  - a bot that adds a random word to every prompt changes the hash each time
    and slips through, since we only compare hashes, never the text
  - a short phrase that a small group happens to repeat a lot ("otro",
    "続き") can still get through the 20% rule, it just ends up as a small group
"""

from collections import defaultdict
from dataclasses import dataclass

from luxvigil.checks.groups import Groups


@dataclass
class PromptBot:
    accounts: list[str]
    prompts: list[str]  # the repeated prompt hashes that tie the group together
    repeats: int  # times those accounts sent those prompts, in total
    requests: int = 0  # all requests from these accounts, not just the repeated ones
    cost_usd: float = 0.0

    @property
    def why(self):
        """The one-liner that shows up in the report."""
        n = len(self.prompts)
        what = "the same prompt" if n == 1 else f"the same {n} prompts"
        return f"{len(self.accounts):,} accounts sending {what} over and over ({self.repeats:,} times)"


def find_prompt_bots(requests, min_accounts=5, min_repeats=3, min_share=0.2):
    """Return the groups of accounts running the same prompts, biggest cost first.

    A prompt is flagged when `min_accounts`+ accounts each sent it `min_repeats`+ times,
    and those are at least `min_share` of everyone who sent it.
    Only those accounts are part of the bot, not everyone who ever sent the prompt.
    """
    # 1. how many times each account sent each prompt: prompt -> account -> count
    sends = defaultdict(lambda: defaultdict(int))
    for r in requests:
        if r.prompt_hash:
            sends[r.prompt_hash][r.account_id] += 1

    # 2. for each prompt, keep the accounts that sent it over and over themselves.
    #    If enough of them did, and they're a real chunk of everyone sending it
    #    (not 5 people out of the 10,000 who type "more"), it's a bot template
    repeated = {}  # prompt -> {account: times sent}, only the heavy senders
    for prompt, per_account in sends.items():
        heavy = {acc: n for acc, n in per_account.items() if n >= min_repeats}
        if len(heavy) >= min_accounts and len(heavy) >= min_share * len(per_account):
            repeated[prompt] = heavy

    if not repeated:
        return []

    # 3. accounts sharing any template end up together, so a bot that rotates
    #    through several templates is one group
    groups = Groups()
    for heavy in repeated.values():
        groups.join_all(list(heavy))

    bots = []
    for accs in groups.members().values():
        prompts = sorted(p for p, heavy in repeated.items() if set(heavy) <= accs)
        bots.append(
            PromptBot(
                accounts=sorted(accs),
                prompts=prompts,
                repeats=sum(sum(repeated[p].values()) for p in prompts),
            )
        )

    # 4. add up everything those accounts spent
    owner = {acc: bot for bot in bots for acc in bot.accounts}
    for r in requests:
        bot = owner.get(r.account_id)
        if bot:
            bot.requests += 1
            bot.cost_usd += r.cost_usd

    return sorted(bots, key=lambda b: b.cost_usd, reverse=True)