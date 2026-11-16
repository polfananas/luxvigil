"""LiteLLM spend logs, read as they come out of LiteLLM.

LiteLLM (the LLM proxy) keeps one row per request in its LiteLLM_SpendLogs
table. You can get them two ways, and both work here:

  - the API: GET /spend/logs/v2 on your proxy, saved as JSON
  - the database: export the LiteLLM_SpendLogs table to CSV

What maps to what:

  startTime              -> timestamp
  end_user               -> account (falls back to `user`, the key owner, then the key)
  api_key                -> api_key_id. LiteLLM stores a SHA-256 hash of the key,
                            never the key itself. Anything that isn't a hash like
                            that (an export someone edited, an old version...) is
                            hashed here before it's used, so a raw key never shows
  requester_ip_address   -> ip (unless you turned IP logging off)
  prompt_tokens / completion_tokens / spend -> tokens and cost. No spend is
                            an unknown cost, not a free request
  messages               -> prompt_hash and prompt_chars, worked out here and the
                            text thrown away. Only there if you turned on storing
                            prompts in spend logs, otherwise the bot check is skipped

There's no email, country or device in these logs, so the checks that need them
are skipped (the report says which).
"""

import csv
import hashlib
import json
import re

from luxvigil.schema import redact

# what LiteLLM writes in api_key: the SHA-256 of the key, in hex
_HASHED_KEY = re.compile(r"[0-9a-f]{64}")

# which field the account came from, the first one that's there wins
IDENTITY = ("end_user", "user", "api_key")

# our column -> where it comes from in a LiteLLM spend log
COLUMNS = ["timestamp", "account_id", "api_key_id", "ip", "input_tokens", "output_tokens",
           "cost_usd", "prompt_hash", "prompt_chars"]


def prompt_hash(text):
    """Same idea as a hash in your own logs: "Hi  " and "hi" count as the same prompt."""
    return hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()[:16]


def _text(content):
    """A message's content is either a string or a list of parts like {"type": "text", "text": ...}."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _last_user_message(record):
    """The last thing the user sent, if LiteLLM stored the prompt at all."""
    for field in ("messages", "proxy_server_request"):
        value = record.get(field)
        if isinstance(value, str):
            try:
                value = json.loads(value) if value.strip() else None
            except json.JSONDecodeError:
                continue
        if field == "proxy_server_request" and isinstance(value, dict):
            value = (value.get("body") or {}).get("messages")
        if isinstance(value, list):
            for message in reversed(value):
                if isinstance(message, dict) and message.get("role") == "user":
                    text = _text(message.get("content"))
                    if text:
                        return text
    return ""


def _records(path):
    """The rows of the export as dicts, from JSON (the API) or CSV (the database)."""
    with open(path, encoding="utf-8-sig") as f:
        head = f.read(1)
        f.seek(0)
        if head in "[{":
            data = json.load(f)
            # /spend/logs/v2 returns {"data": [...], "page": ...}, older endpoints a plain list
            if isinstance(data, dict):
                data = data.get("data", [])
            return [r for r in data if isinstance(r, dict)]
        first_line = f.readline()
        f.seek(0)
        sep = max([",", ";", "\t"], key=first_line.count)
        return list(csv.DictReader(f, delimiter=sep))


def _get(record, name):
    """A field, whatever case the export used (Postgres exports often lowercase everything)."""
    value = record.get(name)
    if value is None:
        lowered = {k.lower(): v for k, v in record.items() if isinstance(k, str)}
        value = lowered.get(name.lower())
    return "" if value is None else str(value).strip()


def _key(record):
    """The key's id, safe to print: LiteLLM's hash as is, anything else hashed here."""
    key = _get(record, "api_key")
    if key.lower() in ("", "none", "null"):
        return ""
    return key if _HASHED_KEY.fullmatch(key.lower()) else redact(key)


def _account(record, key):
    """(account id, where it came from). end_user is your customer (the `user` you pass in
    the request). Without it, the best we have is the LiteLLM user who owns the key, or the key.

    The three are different things, and the same string could mean one thing as an end_user
    and another as a key, so the fallbacks get a prefix: "user:alice", "key:3f2a...".
    """
    for field in IDENTITY:
        value = key if field == "api_key" else _get(record, field)
        if value.lower() in ("", "none", "null"):
            continue
        return (value if field == "end_user" else f"{field.replace('api_', '')}:{value}"), field
    return "", None


def read_litellm(path, notes=None):
    """LiteLLM spend logs -> (our column names, rows as dicts in our columns)."""
    rows, with_prompt, without_ip, redacted = [], 0, 0, 0
    identity = dict.fromkeys(IDENTITY, 0)
    for record in _records(path):
        prompt = _last_user_message(record)
        with_prompt += bool(prompt)
        ip = _get(record, "requester_ip_address")
        if ip.lower() in ("none", "null"):
            ip = ""
        without_ip += not ip
        key = _key(record)
        redacted += key.startswith("redacted-")
        account, source = _account(record, key)
        if source:
            identity[source] += 1
        spend = _get(record, "spend")
        rows.append({
            # not one of our columns, but it makes two different requests that look alike
            # (same user, second and tokens) different rows, so they're not taken as duplicates
            "request_id": _get(record, "request_id"),
            "timestamp": _get(record, "startTime"),
            "account_id": account,
            "api_key_id": key,
            "ip": ip,
            "input_tokens": _get(record, "prompt_tokens") or "0",
            "output_tokens": _get(record, "completion_tokens") or "0",
            # missing spend is an unknown cost, an explicit 0 is free
            "cost_usd": "" if spend.lower() in ("none", "null") else spend,
            "prompt_hash": prompt_hash(prompt) if prompt else "",
            "prompt_chars": str(len(prompt)) if prompt else "",
        })

    if notes is not None:
        used = [f"{n:,} by {field}" for field, n in identity.items() if n]
        notes.append(f"reading LiteLLM spend logs: accounts are the end_user, else the key's owner (user:...), "
                     f"else the key (key:...). Requests per account type: {', '.join(used) or 'none'}")
        if identity["user"] or identity["api_key"]:
            notes.append("a key owner or a key can stand for many people, so groups built on them say less "
                         "than groups of end users. Pass `user` in your requests to get end users")
        if redacted:
            notes.append(f"{redacted:,} request{'s' if redacted != 1 else ''} had an api_key that isn't a LiteLLM "
                         "hash (maybe a raw key), so it was hashed here and is never shown")
        if rows and not with_prompt:
            notes.append("no prompts in these logs (LiteLLM only keeps them if you turn on storing prompts "
                         "in spend logs), so copy-paste bots can't be checked")
        if rows and without_ip == len(rows):
            notes.append("no IPs in these logs (requester_ip_address is empty), so farms can't be linked by network")
    # no IPs at all: leave the column out, so it's not reported as thousands of "bad" IPs
    columns = [c for c in COLUMNS if c != "ip"] if rows and without_ip == len(rows) else COLUMNS
    return columns, rows
