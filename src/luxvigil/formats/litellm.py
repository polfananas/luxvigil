"""LiteLLM spend logs, read as they come out of LiteLLM.

LiteLLM (the LLM proxy) keeps one row per request in its LiteLLM_SpendLogs
table. You can get them two ways, and both work here:

  - the API: GET /spend/logs/v2 on your proxy, saved as JSON
  - the database: export the LiteLLM_SpendLogs table to CSV

What maps to what:

  startTime              -> timestamp
  end_user               -> account (falls back to `user`, the key owner, then the key)
  api_key                -> api_key_id. LiteLLM stores a SHA-256 hash of the key,
                            never the key itself, so this one is safe to read
  requester_ip_address   -> ip (unless you turned IP logging off)
  prompt_tokens / completion_tokens / spend -> tokens and cost
  messages               -> prompt_hash and prompt_chars, worked out here and the
                            text thrown away. Only there if you turned on storing
                            prompts in spend logs, otherwise the bot check is skipped

There's no email, country or device in these logs, so the checks that need them
are skipped (the report says which).
"""

import csv
import hashlib
import json

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


def read_litellm(path, notes=None):
    """LiteLLM spend logs -> (our column names, rows as dicts in our columns)."""
    rows, with_prompt, without_ip = [], 0, 0
    for record in _records(path):
        prompt = _last_user_message(record)
        with_prompt += bool(prompt)
        ip = _get(record, "requester_ip_address")
        if ip.lower() in ("none", "null"):
            ip = ""
        without_ip += not ip
        rows.append({
            "timestamp": _get(record, "startTime"),
            # end_user is your customer (the `user` you pass in the request). Without it,
            # the best we have is the LiteLLM user who owns the key, or the key itself
            "account_id": _get(record, "end_user") or _get(record, "user") or _get(record, "api_key"),
            "api_key_id": _get(record, "api_key"),
            "ip": ip,
            "input_tokens": _get(record, "prompt_tokens") or "0",
            "output_tokens": _get(record, "completion_tokens") or "0",
            "cost_usd": _get(record, "spend") or "0",
            "prompt_hash": prompt_hash(prompt) if prompt else "",
            "prompt_chars": str(len(prompt)) if prompt else "",
        })

    if notes is not None:
        notes.append("reading LiteLLM spend logs: each account is the end_user, or the key owner when there isn't one")
        if rows and not with_prompt:
            notes.append("no prompts in these logs (LiteLLM only keeps them if you turn on storing prompts "
                         "in spend logs), so copy-paste bots can't be checked")
        if rows and without_ip == len(rows):
            notes.append("no IPs in these logs (requester_ip_address is empty), so farms can't be linked by network")
    # no IPs at all: leave the column out, so it's not reported as thousands of "bad" IPs
    columns = [c for c in COLUMNS if c != "ip"] if rows and without_ip == len(rows) else COLUMNS
    return columns, rows
