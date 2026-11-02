"""The log format luxvigil reads: one row per API request, as a CSV."""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone

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
}

OPTIONAL = {"device_id", "cost_usd"}  # cost can be worked out from tokens if you pass prices
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
}


@dataclass
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


class LogFormatError(ValueError):
    pass


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
    return datetime.fromtimestamp(number, tz=timezone.utc)


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


def load_logs(path, mapping=None, price_in=None, price_out=None, notes=None):
    """Read a log CSV and return a list of Request. Fails loudly on bad input.

    mapping: {our_column: their_column} for files that name things differently.
    price_in / price_out: $ per 1M tokens, used to work out the cost when there's no cost column.
    notes: pass a list to get told which columns were matched automatically.
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
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

        rows = []
        for line, r in enumerate(reader, start=2):
            try:
                tin = int(float(r[cols["input_tokens"]]))
                tout = int(float(r[cols["output_tokens"]]))
                if "cost_usd" in cols:
                    cost = float(r[cols["cost_usd"]])
                else:
                    cost = (tin * price_in + tout * price_out) / 1_000_000
                rows.append(
                    Request(
                        timestamp=_parse_time(r[cols["timestamp"]]),
                        account_id=r[cols["account_id"]],
                        email=r[cols["email"]].strip().lower(),
                        api_key_id=r[cols["api_key_id"]],
                        ip=r[cols["ip"]].strip(),
                        country=r[cols["country"]].strip().upper(),
                        device_id=(r.get(cols["device_id"]) or "") if "device_id" in cols else "",
                        prompt_hash=r[cols["prompt_hash"]],
                        input_tokens=tin,
                        output_tokens=tout,
                        cost_usd=cost,
                    )
                )
            except (ValueError, KeyError, TypeError) as e:
                raise LogFormatError(f"line {line}: {e}") from e
    return rows
