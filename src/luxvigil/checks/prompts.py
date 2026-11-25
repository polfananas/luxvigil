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

The 20% rule still lets through the follow-ups people who use them type all
the time ("continue", "go on", "继续"...). Those have one thing in common: they're
short, and a bot template almost never is. So if your logs have the optional
prompt_chars column, prompts under 20 characters are never treated as a
template. On WildChat-4.8M this kept 12 of the 13 real automation groups and
dropped the fake ones built on short phrases. Without the column, nothing changes.

Accounts that share any flagged prompt end up in the same group, so a bot
rotating through 5 templates shows up as one group, not five.

Cost: two numbers, because they mean different things. The requests that sent
the repeated prompts are the signal itself. The rest of what those accounts did
might be perfectly normal, so it's only "worth a look" in the report, never
added to likely abuse.

One big spender in a group of small ones isn't counted as abuse either. The
accounts of a bot are interchangeable, so they spend roughly alike. If one of
them spent more than 10x what the typical member did (the median) on the
repeated prompts, its spend goes to "worth a look" and the report says so.
Without this, 4 throwaway accounts spending $0.12 between them could copy a
prompt a real customer repeats and push that customer's $300 into likely abuse.
The group itself still shows up, with the cheap accounts as the signal.

Limits worth knowing:
  - a legit integration sending a fixed template on behalf of its users can
    look the same (that's in the "innocent explanation" of --json)
  - a bot that adds a random word to every prompt changes the hash each time
    and slips through, since we only compare hashes, never the text
  - without prompt_chars, a short phrase that a small group repeats a lot
    ("otro", "続き") can still get through the 20% rule
  - a bot whose template is a short command ("/start") is skipped by the
    length rule, that's the price of not flagging everyone who types "more"
  - a bot that really does have one account doing most of the work gets that
    account's spend in "worth a look" instead of likely abuse
"""

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median

from luxvigil.checks.groups import Groups


@dataclass
class PromptBot:
    accounts: list[str]
    prompts: list[str]  # the repeated prompt hashes that tie the group together
    repeats: int  # times those accounts sent those prompts, in total
    requests: int = 0  # all requests from these accounts, not just the repeated ones
    cost_usd: float = 0.0  # what all of those cost
    signal_requests: int = 0  # only the requests that sent one of the repeated prompts
    signal_cost_usd: float = 0.0
    # (account, prompt) pairs where that account itself sent that prompt min_repeats+ times
    qualifying: set = field(default_factory=set, repr=False, compare=False)
    # accounts that spent way more than the rest on the repeated prompts: worth a look, not the signal
    big_spenders: list[str] = field(default_factory=list)
    big_spenders_requests: int = 0
    big_spenders_cost_usd: float = 0.0

    def is_signal(self, r):
        """Is this request one of the repeated prompts, sent by an account that repeated
        that same prompt itself? Being in the group isn't enough: an account that sent
        another member's template once (maybe a big, expensive request) is not the signal."""
        return (r.account_id, r.prompt_hash) in self.qualifying

    @property
    def why(self):
        """The one-liner that shows up in the report."""
        n = len(self.prompts)
        what = "the same prompt" if n == 1 else f"the same {n} prompts"
        return f"{len(self.accounts):,} accounts sending {what} over and over ({self.repeats:,} times)"


def find_prompt_bots(requests, min_accounts=5, min_repeats=3, min_share=0.2, min_chars=20, max_spread=10):
    """Return the groups of accounts running the same prompts, biggest cost first.

    A prompt is flagged when `min_accounts`+ accounts each sent it `min_repeats`+ times,
    and those are at least `min_share` of everyone who sent it.
    Only those accounts are part of the bot, not everyone who ever sent the prompt.
    Prompts known to be shorter than `min_chars` characters are skipped.
    An account that spent more than `max_spread` times the group's median on the
    repeated prompts is listed in big_spenders and left out of the signal.
    """
    # 1. how many times each account sent each prompt: prompt -> account -> count
    sends = defaultdict(lambda: defaultdict(int))
    for r in requests:
        if not r.prompt_hash:
            continue
        # "more", "go on", "hi": too short to be a template. Unknown length (None) still counts
        if r.prompt_chars is not None and r.prompt_chars < min_chars:
            continue
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
                qualifying={(acc, p) for p in prompts for acc in repeated[p]},
            )
        )

    # 4. add up what those accounts spent: on the repeated prompts, and in total
    owner = {acc: bot for bot in bots for acc in bot.accounts}
    spent = defaultdict(float)  # account -> its spend on the repeated prompts
    sent = defaultdict(int)
    for r in requests:
        bot = owner.get(r.account_id)
        if bot:
            bot.requests += 1
            bot.cost_usd += r.cost_usd
            if bot.is_signal(r):
                sent[r.account_id] += 1
                spent[r.account_id] += r.cost_usd

    # 5. a bot's accounts spend alike. One that spent 10x+ the typical member is
    #    more likely a real customer whose prompt got copied than part of the bot
    for bot in bots:
        typical = median(spent[acc] for acc in bot.accounts)
        big = [acc for acc in bot.accounts if spent[acc] > 0 and spent[acc] > max_spread * typical]
        if big:
            bot.big_spenders = big
            bot.big_spenders_requests = sum(sent[acc] for acc in big)
            bot.big_spenders_cost_usd = sum(spent[acc] for acc in big)
            bot.qualifying = {(acc, p) for acc, p in bot.qualifying if acc not in big}
        bot.signal_requests = sum(sent[acc] for acc in bot.accounts if acc not in big)
        bot.signal_cost_usd = sum(spent[acc] for acc in bot.accounts if acc not in big)

    return sorted(bots, key=lambda b: (b.signal_cost_usd, b.signal_requests), reverse=True)