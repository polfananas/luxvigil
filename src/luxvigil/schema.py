"""The log format luxvigil reads: one row per API request, as a CSV.

This is the only place that touches your file. Everything after it works on
clean Request objects, so all the "is this row any good" checks live here:
bad numbers stop the scan (or get skipped with --skip-bad-rows), smaller
oddities like duplicated rows or missing IPs just get a warning.

Exports from Excel and friends are handled too: a BOM at the start of the
file, and ; or tab instead of , as the separator (European Excel uses ;).
"""

import csv
import ipaddress
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

# column -> what it is
COLUMNS = {
    "timestamp": "when the request happened, ISO 8601 (2026-10-01T14:03:22Z) or a unix timestamp",
    "account_id": "your internal id for the account",
    "email": "email the account signed up with",
    "api_key_id": "id or hash of the key used (never the key itself)",
    "ip": "client IP address",
    "country": "2-letter country code of the IP",
    "device_id": "device / browser fingerprint, if you have one (can be empty)",
    "prompt_hash": "hash of the prompt text, to spot repeated prompts without storing them",
    "input_tokens": "tokens in the request",
    "output_tokens": "tokens in the response",
    "cost_usd": "what that request cost you (optional if you pass --price-in/--price-out)",
    "prompt_chars": "length of the prompt in characters (optional), so a short \"more\" is never taken for a bot template",
}

OPTIONAL = {"device_id", "cost_usd", "prompt_chars"}  # cost can be worked out from tokens if you pass prices
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
    cost_usd: float
    prompt_chars: int | None = None  # None when the logs don't say


class LogFormatError(ValueError):
    pass


# unix timestamps outside this range are almost surely something else (a date
# written as 20261001, a counter, an id...) and would quietly land in 1970
_EARLIEST, _LATEST = 946_684_800, 4_102_444_800  # 2000-01-01 and 2100-01-01


def _parse_time(value):
    """ISO 8601 or a unix timestamp (seconds or milliseconds). No timezone means UTC."""
    value = value.strip()
    try:
        number = float(value)
    except ValueError:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    if number > 1e11:  # milliseconds
        number /= 1000
    if not _EARLIEST <= number <= _LATEST:  # also catches nan
        raise ValueError(f"timestamp '{value}' doesn't look like a date (use ISO 8601 or unix seconds)")
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
        raise ValueError(f"{name} should be a number, got '{value}'") from None
    if not math.isfinite(n) or n < 0 or n != int(n):
        raise ValueError(f"{name} should be a whole number, 0 or more, got '{value}'")
    return int(n)


def _money(value, name):
    """An amount of dollars: 0 or more, and a real number (no nan or inf)."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} should be a number, got '{value}'") from None
    if not math.isfinite(x) or x < 0:
        raise ValueError(f"{name} should be 0 or more, got '{value}'")
    return x


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


def load_logs(path, mapping=None, price_in=None, price_out=None, notes=None, skip_bad=False):
    """Read a log CSV and return a list of Request. Fails loudly on bad input.

    mapping: {our_column: their_column} for files that name things differently.
    price_in / price_out: $ per 1M tokens, used to work out the cost when there's no cost column.
    notes: pass a list to get told which columns were matched automatically, and about duplicates etc.
    skip_bad: skip rows that don't parse instead of stopping (you're told how many and why).
    """
    # utf-8-sig quietly drops the BOM Excel puts at the start of a file, which
    # would otherwise turn the first column into "\ufefftimestamp"
    with open(path, newline="", encoding="utf-8-sig") as f:
        first_line = f.readline()
        if not first_line.strip():
            raise LogFormatError("the file is empty")
        f.seek(0)
        reader = csv.DictReader(f, delimiter=_separator(first_line))
        cols, found = resolve_columns(reader.fieldnames, mapping)
        if notes is not None:
            notes.extend(found)

        missing = [c for c in REQUIRED if c not in cols]
        if missing:
            raise LogFormatError(
                f"missing columns: {', '.join(missing)}. "
                f"If they're there under another name, use --map, e.g. --map {missing[0]}=your_column_name"
            )
        if "cost_usd" not in cols and (price_in is None or price_out is None):
            raise LogFormatError(
                "no cost column. Add a cost_usd column, map yours with --map cost_usd=..., "
                "or pass --price-in and --price-out ($ per 1M tokens) to work it out from tokens"
            )

        # 1. read and check every row
        rows = []
        seen, duplicates, bad_ips = set(), 0, 0
        # the same account, email, key, IP... repeat on thousands of rows: keep one copy of each string
        shared = {}

        def one(value):
            return shared.setdefault(value, value)

        skipped, first_error = 0, None
        for line, r in enumerate(reader, start=2):
            try:
                if None in r.values():
                    raise ValueError("this row has fewer columns than the header")
                if not r[cols["account_id"]].strip():
                    raise ValueError("account_id is empty")
                tin = _tokens(r[cols["input_tokens"]], "input_tokens")
                tout = _tokens(r[cols["output_tokens"]], "output_tokens")
                if "cost_usd" in cols:
                    cost = _money(r[cols["cost_usd"]], "cost_usd")
                else:
                    cost = (tin * price_in + tout * price_out) / 1_000_000
                # optional, and an empty cell just means "don't know"
                raw_chars = r[cols["prompt_chars"]].strip() if "prompt_chars" in cols else ""
                chars = _tokens(raw_chars, "prompt_chars") if raw_chars else None
                rows.append(
                    Request(
                        timestamp=_parse_time(r[cols["timestamp"]]),
                        account_id=one(r[cols["account_id"]]),
                        email=one(r[cols["email"]].strip().lower()),
                        api_key_id=one(r[cols["api_key_id"]]),
                        ip=one(r[cols["ip"]].strip()),
                        country=one(r[cols["country"]].strip().upper()),
                        device_id=one(r.get(cols["device_id"]) or "") if "device_id" in cols else "",
                        prompt_hash=one(r[cols["prompt_hash"]]),
                        input_tokens=tin,
                        output_tokens=tout,
                        cost_usd=cost,
                        prompt_chars=chars,
                    )
                )
            except (ValueError, KeyError, TypeError) as e:
                if not skip_bad:
                    raise LogFormatError(f"line {line}: {e} (use --skip-bad-rows to skip rows like this)") from e
                skipped += 1
                first_error = first_error or f"line {line}: {e}"
                continue

            # 2. smaller oddities: count them, don't stop
            # only keep a hash of each row, keeping the rows themselves doubles memory on big files
            fingerprint = hash(tuple(r.values()))
            if fingerprint in seen:
                duplicates += 1
            seen.add(fingerprint)
            if not _valid_ip(rows[-1].ip):
                bad_ips += 1

    # these don't stop the scan, but you should know about them
    if notes is not None:
        if skipped:
            notes.append(f"skipped {skipped:,} bad row{'s' if skipped != 1 else ''}, the first one was {first_error}")
        if duplicates:
            notes.append(f"{duplicates:,} duplicate row{'s' if duplicates != 1 else ''} (exact copy of an earlier one), "
                         "check your export isn't doubled")
        if bad_ips:
            notes.append(f"{bad_ips:,} row{'s' if bad_ips != 1 else ''} without a valid IP, left out of the network checks")
    return rows