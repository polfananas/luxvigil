"""The log format luxvigil reads: one row per API request, as a CSV.

This is the only place that touches your file. Everything after it works on
clean Request objects, so all the "is this row any good" checks live here:
bad numbers stop the scan (or get skipped with --skip-bad-rows), smaller
oddities like duplicated rows or missing IPs just get a warning.

Exports from Excel and friends are handled too: a BOM at the start of the
file, and ; or tab instead of , as the separator (European Excel uses ;).
"""

import csv
import hashlib
import ipaddress
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

# column -> what it is
COLUMNS = {
    "timestamp": "when the request happened, ISO 8601 (2026-10-01T14:03:22Z) or a unix timestamp. "
                 "Everything is turned into UTC; a time with no timezone is taken as UTC already",
    "account_id": "your internal id for the account",
    "email": "email the account signed up with",
    "api_key_id": "id or hash of the key used (never the key itself)",
    "ip": "client IP address",
    "country": "2-letter country code of the IP",
    "device_id": "device / browser fingerprint, if you have one (can be empty)",
    "prompt_hash": "hash of the prompt text, to spot repeated prompts without storing them",
    "input_tokens": "tokens in the request",
    "output_tokens": "tokens in the response",
    "cost_usd": "what that request cost you (optional: without it, pass --price-in/--price-out, "
                "or the scan runs on requests and tokens only)",
    "prompt_chars": "length of the prompt in characters (optional), so a short \"more\" is never taken for a bot template",
}

# Only when, who and how many tokens are a must. Everything else is optional: a
# missing column just means the checks that need it are skipped (and the report
# says so). Cost can be worked out from tokens if you pass prices.
OPTIONAL = {"email", "api_key_id", "ip", "country", "device_id", "prompt_hash", "cost_usd", "prompt_chars"}
REQUIRED = [c for c in COLUMNS if c not in OPTIONAL]

# Other names people commonly give these columns. Matched without caring about case.
# "api_key" is left out on purpose: in some exports it's the raw key, and we never want that.
ALIASES = {
    "timestamp": ["time", "ts", "date", "datetime", "created_at", "created", "request_time", "start_time"],
    "account_id": ["user_id", "user", "account", "customer_id", "end_user"],
    "email": ["user_email", "email_address"],
    "api_key_id": ["key_id", "api_key_hash", "key_hash", "hashed_api_key"],
    "ip": ["ip_address", "client_ip", "remote_addr", "requester_ip_address"],
    "country": ["country_code", "geo_country"],
    "device_id": ["device", "fingerprint", "device_fingerprint"],
    "prompt_hash": ["prompt_sha256", "input_hash"],
    "input_tokens": ["prompt_tokens", "tokens_in"],
    "output_tokens": ["completion_tokens", "tokens_out"],
    "cost_usd": ["cost", "total_cost", "spend", "response_cost"],
    "prompt_chars": ["prompt_length", "prompt_len", "prompt_chars_count"],
}


@dataclass(slots=True)
class Request:
    timestamp: datetime
    account_id: str
    email: str
    api_key_id: str
    ip: str
    country: str
    device_id: str
    prompt_hash: str
    input_tokens: int
    output_tokens: int
    cost_usd: float  # 0.0 when the cost isn't known, check cost_known before reading it as "free"
    prompt_chars: int | None = None  # None when the logs don't say
    cost_known: bool = True  # False when the logs had no cost for this request and there were no prices


class LogFormatError(ValueError):
    pass


# unix timestamps outside this range are almost surely something else (a date
# written as 20261001, a counter, an id...) and would quietly land in 1970
_EARLIEST, _LATEST = 946_684_800, 4_102_444_800  # 2000-01-01 and 2100-01-01


def shown(value, limit=40):
    """A value from the logs, safe to put in a message: control characters escaped (an
    ESC in a cell can't move the cursor or retitle the terminal) and long values cut."""
    text = "".join(c if c.isprintable() else repr(c)[1:-1] for c in str(value))
    return text if len(text) <= limit else text[:limit] + "…"


def _parse_time(value):
    """ISO 8601 or a unix timestamp (seconds or milliseconds), always returned in UTC.

    No timezone means UTC. A time with an offset is converted, so 01:30+02:00 and
    23:30Z the day before are the same instant and land on the same (UTC) day.
    """
    value = value.strip()
    try:
        number = float(value)
    except ValueError:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)
    if number > 1e11:  # milliseconds
        number /= 1000
    if not _EARLIEST <= number <= _LATEST:  # also catches nan
        raise ValueError(f"timestamp '{shown(value)}' doesn't look like a date (use ISO 8601 or unix seconds)")
    return datetime.fromtimestamp(number, tz=timezone.utc)


@lru_cache(maxsize=1 << 16)
def _valid_ip(ip):
    # the same IPs show up over and over, so parse each one once
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def _tokens(value, name):
    """A token count: a whole number, 0 or more. "12.0" is fine, "12.5" or "-3" aren't."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} should be a number, got '{shown(value)}'") from None
    if not math.isfinite(n) or n < 0 or n != int(n):
        raise ValueError(f"{name} should be a whole number, 0 or more, got '{shown(value)}'")
    return int(n)


def _money(value, name):
    """An amount of dollars: 0 or more, and a real number (no nan or inf)."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} should be a number, got '{shown(value)}'") from None
    if not math.isfinite(x) or x < 0:
        raise ValueError(f"{name} should be 0 or more, got '{shown(value)}'")
    return x


# How secret API keys usually look (OpenAI, Anthropic, Stripe, AWS, GitHub, Slack,
# Google...). Only a heuristic: it catches the common ones, not every secret there is.
_SECRET = re.compile(r"sk-|sk_|rk_|ghp_|gho_|github_pat_|xox[abpr]-|AIza|(?i:bearer\s)|(AKIA|ASIA)[A-Z0-9]{16}$")


def looks_like_secret(value):
    """True for values that look like a raw API key rather than an id or a hash of one."""
    return _SECRET.match(value) is not None


def redact(value):
    """A raw key -> a stable id that stands in for it. Same key, same id, so grouping still works,
    but the key itself never gets printed or exported."""
    return "redacted-" + hashlib.sha256(value.encode()).hexdigest()[:16]


def _separator(first_line):
    """Comma, semicolon or tab: whichever shows up most in the header line."""
    return max([",", ";", "\t"], key=first_line.count)


def resolve_columns(fieldnames, mapping=None):
    """Work out which column in the file holds each of ours.

    `mapping` is {our_name: their_name}, from --map. Returns ({our_name: their_name}, notes),
    where notes say which columns were matched by an alias, so nothing happens silently.
    """
    mapping = mapping or {}
    unknown = [ours for ours in mapping if ours not in COLUMNS]
    if unknown:
        raise LogFormatError(f"--map: don't know {', '.join(unknown)}. Columns are: {', '.join(COLUMNS)}")

    fieldnames = list(fieldnames or [])
    by_lower = {name.strip().lower(): name for name in fieldnames}
    resolved, notes = {}, []
    for ours in COLUMNS:
        if ours in mapping:
            theirs = mapping[ours]
            if theirs not in fieldnames:
                raise LogFormatError(
                    f"--map {ours}={theirs}: there's no column '{theirs}' in the file. "
                    f"It has: {', '.join(fieldnames)}"
                )
            resolved[ours] = theirs
        elif ours.lower() in by_lower:
            resolved[ours] = by_lower[ours.lower()]
        else:
            for alias in ALIASES.get(ours, []):
                if alias in by_lower:
                    resolved[ours] = by_lower[alias]
                    notes.append(f"using '{by_lower[alias]}' as {ours}")
                    break
    return resolved, notes


FORMATS = ["csv", "litellm"]


def load_logs(path, mapping=None, price_in=None, price_out=None, notes=None, skip_bad=False, fmt="csv",
              keep_duplicates=False):
    """Read a log file and return a list of Request. Fails loudly on bad input.

    mapping: {our_column: their_column} for files that name things differently.
    price_in / price_out: $ per 1M tokens, used to work out the cost when there's no cost column.
    notes: pass a list to get told which columns were matched automatically, and about duplicates etc.
    skip_bad: skip rows that don't parse instead of stopping (you're told how many and why).
    fmt: "csv" for our own columns (the default), or the export of a tool we know, like "litellm".
    keep_duplicates: keep rows that are an exact copy of an earlier one. By default they're
    dropped: a row identical in every column (request id and timestamp included) is almost
    always the same request exported twice, and counting it twice can push an account over a
    threshold and doubles its cost. If your logs have no request id and really do log
    identical requests in the same second, keep them.
    """
    if fmt == "litellm":
        if mapping:
            raise LogFormatError("--map doesn't go with --format litellm, the columns are already known")
        from luxvigil.formats.litellm import read_litellm

        fieldnames, records = read_litellm(path, notes)
        if not records:
            raise LogFormatError("no requests in this LiteLLM export")
        return _read_rows(fieldnames, records, None, price_in, price_out, notes, skip_bad, "request",
                          keep_duplicates)
    if fmt != "csv":
        raise LogFormatError(f"unknown format '{fmt}', use one of: {', '.join(FORMATS)}")

    # utf-8-sig quietly drops the BOM Excel puts at the start of a file, which
    # would otherwise turn the first column into "\ufefftimestamp"
    with open(path, newline="", encoding="utf-8-sig") as f:
        first_line = f.readline()
        if not first_line.strip():
            raise LogFormatError("the file is empty")
        f.seek(0)
        reader = csv.DictReader(f, delimiter=_separator(first_line))
        return _read_rows(reader.fieldnames, reader, mapping, price_in, price_out, notes, skip_bad, "line",
                          keep_duplicates)


def _read_rows(fieldnames, records, mapping, price_in, price_out, notes, skip_bad, unit, keep_duplicates=False):
    """Check and turn rows (dicts) into Requests. `unit` is how errors point at a row: line or request."""
    cols, found = resolve_columns(fieldnames, mapping)
    if notes is not None:
        notes.extend(found)

    missing = [c for c in REQUIRED if c not in cols]
    if missing:
        raise LogFormatError(
            f"missing columns: {', '.join(missing)}. "
            f"If they're there under another name, use --map, e.g. --map {missing[0]}=your_column_name"
        )
    priced = price_in is not None and price_out is not None
    if "cost_usd" not in cols and not priced and notes is not None:
        # still worth scanning: farms, keys and bots don't need dollars, only the spike check does
        notes.append("no cost column and no --price-in/--price-out, so there are no dollar amounts in this "
                     "report and spend spikes can't be checked. Add a cost_usd column (or --map cost_usd=...) "
                     "or pass prices ($ per 1M tokens) to get them")

    # 1. read and check every row
    rows = []
    seen, duplicates, bad_ips = set(), 0, 0
    # the same account, email, key, IP... repeat on thousands of rows: keep one copy of each string
    shared = {}

    def one(value):
        return shared.setdefault(value, value)

    def cell(r, name):
        """The value of one of our columns in this row, "" if the file doesn't have it."""
        return (r.get(cols[name]) or "").strip() if name in cols else ""

    skipped, first_error = 0, None
    unknown_cost, estimated_cost, redacted_keys = 0, 0, 0
    # a CSV's first row is line 2 (the header is line 1), an API export just counts requests
    for line, r in enumerate(records, start=2 if unit == "line" else 1):
        try:
            if None in r.values():
                raise ValueError("this row has fewer columns than the header")
            if None in r:  # csv puts the extra values under a None key
                raise ValueError("this row has more columns than the header")
            # the same request logged twice: count it once (see load_logs). Only a hash of
            # each row is kept, keeping the rows themselves would double memory on big files
            fingerprint = hash(tuple(r.values()))
            if fingerprint in seen:
                duplicates += 1
                if not keep_duplicates:
                    continue
            seen.add(fingerprint)
            if not r[cols["account_id"]].strip():
                raise ValueError("account_id is empty")
            tin = _tokens(r[cols["input_tokens"]], "input_tokens")
            tout = _tokens(r[cols["output_tokens"]], "output_tokens")
            # the cost is known (in the logs), worked out (from prices) or unknown. An empty
            # cell is unknown, not free: only an explicit 0 means the request cost nothing
            raw_cost = r[cols["cost_usd"]].strip() if "cost_usd" in cols else ""
            cost_known = True
            if raw_cost:
                cost = _money(raw_cost, "cost_usd")
            elif priced:
                cost = (tin * price_in + tout * price_out) / 1_000_000
                estimated_cost += "cost_usd" in cols  # a blank in a cost column, filled in from prices
            else:
                cost, cost_known = 0.0, False
            # optional, and an empty cell just means "don't know"
            raw_chars = r[cols["prompt_chars"]].strip() if "prompt_chars" in cols else ""
            chars = _tokens(raw_chars, "prompt_chars") if raw_chars else None
            key = cell(r, "api_key_id")
            if key and looks_like_secret(key):
                key = redact(key)
                redacted_keys += 1
            rows.append(
                Request(
                    timestamp=_parse_time(r[cols["timestamp"]]),
                    account_id=one(r[cols["account_id"]]),
                    email=one(cell(r, "email").lower()),
                    api_key_id=one(key),
                    ip=one(cell(r, "ip")),
                    country=one(cell(r, "country").upper()),
                    device_id=one(cell(r, "device_id")),
                    prompt_hash=one(cell(r, "prompt_hash")),
                    input_tokens=tin,
                    output_tokens=tout,
                    cost_usd=cost,
                    prompt_chars=chars,
                    cost_known=cost_known,
                )
            )
            unknown_cost += not cost_known
        except (ValueError, KeyError, TypeError) as e:
            if not skip_bad:
                raise LogFormatError(f"{unit} {line}: {e} (use --skip-bad-rows to skip rows like this)") from e
            skipped += 1
            first_error = first_error or f"{unit} {line}: {e}"
            continue

        # 2. smaller oddities: count them, don't stop
        if "ip" in cols and not _valid_ip(rows[-1].ip):
            bad_ips += 1

    # these don't stop the scan, but you should know about them
    if notes is not None:
        if skipped:
            notes.append(f"skipped {skipped:,} bad row{'s' if skipped != 1 else ''}, the first one was {first_error}")
        if duplicates:
            what = ("kept, because of --keep-duplicates" if keep_duplicates
                    else "left out, they'd count the same request twice")
            notes.append(f"{duplicates:,} duplicate row{'s' if duplicates != 1 else ''} (exact copy of an earlier one, "
                         f"every column the same), {what}. Check your export isn't doubled")
        if unknown_cost and "cost_usd" in cols:
            notes.append(f"{unknown_cost:,} of {len(rows):,} requests have no cost in the logs: they count as "
                         "unknown, not as free, and the dollar totals only cover the rest")
        if estimated_cost:
            notes.append(f"{estimated_cost:,} request{'s' if estimated_cost != 1 else ''} with no cost in the logs, "
                         "worked out from --price-in/--price-out instead")
        if redacted_keys:
            notes.append(f"{redacted_keys:,} api_key_id value{'s' if redacted_keys != 1 else ''} looked like a raw "
                         "API key, so a hash of it is used instead and the key itself is never shown")
        if bad_ips:
            notes.append(f"{bad_ips:,} row{'s' if bad_ips != 1 else ''} without a valid IP, left out of the network checks")
    return rows