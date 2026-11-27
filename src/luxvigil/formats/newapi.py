"""New API and One API logs, read as they come out of the admin panel.

New API (QuantumNous/new-api) and One API (songquanpeng/one-api), the project it
was forked from, are the gateways most AI resellers and "relay" sites run on.
Both keep one row per event in a `logs` table, with the same fields. Three ways
to get them out, and all of them work here:

  - New API's export: GET /api/log/export (admin), one JSON object per line
  - the log list API: GET /api/log/ (admin), saved as JSON, any page size
  - the database: export the `logs` table to CSV

What maps to what:

  created_at           -> timestamp (unix seconds)
  username             -> account (falls back to user_id: "id:42")
  token_id             -> api_key_id ("token:17"). Never the key itself, these logs don't have it
  ip                   -> ip. New API only records it for users who turned on
                          IP logging in their settings, so it's often empty
  prompt_tokens / completion_tokens -> tokens
  quota                -> cost, quota / 500,000 = $1 (QuotaPerUnit, the default
                          in both). That's what the request took from the user's
                          balance on your site, not what the upstream provider
                          charged you. Changed QuotaPerUnit? Pass --quota-per-usd
  model_name           -> model
  request_id           -> request id, to tell a request exported twice apart

Only usage rows (type 2) are requests. Top-ups, logins, admin changes and errors
are left out, and you're told how many.

There's no email, country, device or prompt in these logs, so the checks that
need them are skipped (the report says which). The email lives in the `users`
table: pass it with --users (the database export, or GET /api/user/ saved as
JSON) and it's joined in by user id. That matters more than it looks: a shared
network only links accounts that have something else off, like a throwaway
email (see checks/farms.py), so without emails signup farms can't be found.
Only id, username and email are read from it. The rest of that table (password
hashes, access tokens) is never touched, but it's in the file, so keep it safe.
"""

import csv
import json

from luxvigil.schema import LogFormatError

QUOTA_PER_USD = 500_000  # QuotaPerUnit in common/constants.go (new-api) and common/config (one-api)
CONSUME = "2"  # LogTypeConsume, the same number in both

COLUMNS = ["timestamp", "account_id", "api_key_id", "ip", "input_tokens", "output_tokens", "cost_usd", "model"]


def _get(record, name):
    """A field, whatever case the export used. None and null come out as ""."""
    value = record.get(name)
    if value is None:
        lowered = {k.lower(): v for k, v in record.items() if isinstance(k, str)}
        value = lowered.get(name.lower())
    value = "" if value is None else str(value).strip()
    return "" if value.lower() in ("none", "null") else value


def _from_json(data):
    """The rows from whatever JSON the API gave back: a list, {"data": [...]} (One API)
    or {"data": {"items": [...]}} (New API)."""
    if isinstance(data, dict):
        if data.get("success") is False:
            raise LogFormatError(f"the export says it failed: {data.get('message') or 'no reason given'}")
        data = data.get("data", [])
        if isinstance(data, dict):
            data = data.get("items") or []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _ndjson(lines):
    """New API's export: one log per line, and a last line saying how it went."""
    rows = []
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as e:
            raise LogFormatError(f"line {n} isn't valid JSON: {e}") from e
        if isinstance(item, dict) and "success" in item and "created_at" not in item:
            if item["success"] is False:
                # the export broke halfway: what's above is only part of it
                raise LogFormatError(f"the export stopped with an error after {len(rows):,} rows: "
                                     f"{item.get('message') or 'no reason given'}. Export it again")
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _records(path):
    """The rows of the export as dicts, from JSON, JSON lines or CSV."""
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    start = text.lstrip()[:1]
    if start == "[":
        return _from_json(json.loads(text))
    if start == "{":
        try:
            return _from_json(json.loads(text))
        except json.JSONDecodeError:
            return _ndjson(text.splitlines())  # more than one object: JSON lines
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    sep = max([",", ";", "\t"], key=lines[0].count)
    return list(csv.DictReader(lines, delimiter=sep))


def _quota_to_usd(raw, per_usd):
    """quota -> $. Empty is unknown, not free."""
    if raw == "":
        return ""
    try:
        return repr(float(raw) / per_usd)
    except ValueError:
        return raw  # let the loader say what's wrong with it, with the row number


def read_emails(path):
    """The users table -> {user id or username: email}. Nothing else from it is kept."""
    emails = {}
    for user in _records(path):
        email = _get(user, "email")
        if not email:
            continue
        for field in ("id", "username"):
            value = _get(user, field)
            if value:
                emails[f"{field}:{value}"] = email
    return emails


def read_newapi(path, notes=None, quota_per_usd=None, users=None):
    """New API / One API logs -> (our column names, rows as dicts in our columns).
    `users`: path to the users table, to bring in emails."""
    per_usd = quota_per_usd or QUOTA_PER_USD
    emails = read_emails(users) if users else {}
    with_email = set()
    rows, other_types, without_ip, by_id = [], 0, 0, 0
    for record in _records(path):
        kind = _get(record, "type")
        if kind and kind != CONSUME:
            other_types += 1
            continue
        user = _get(record, "username")
        if not user and _get(record, "user_id"):
            user = f"id:{_get(record, 'user_id')}"
            by_id += 1
        email = emails.get(f"id:{_get(record, 'user_id')}") or emails.get(f"username:{_get(record, 'username')}", "")
        if email:
            with_email.add(user)
        token = _get(record, "token_id")
        ip = _get(record, "ip")
        without_ip += not ip
        rows.append({
            "request_id": _get(record, "request_id"),
            "timestamp": _get(record, "created_at"),
            "account_id": user,
            "email": email,
            "api_key_id": f"token:{token}" if token not in ("", "0") else "",
            "ip": ip,
            "input_tokens": _get(record, "prompt_tokens") or "0",
            "output_tokens": _get(record, "completion_tokens") or "0",
            "cost_usd": _quota_to_usd(_get(record, "quota"), per_usd),
            "model": _get(record, "model_name"),
        })

    if notes is not None:
        rate = f"{per_usd:,.0f}"
        notes.append(f"reading New API / One API logs: accounts are usernames, keys are token ids, and $ is "
                     f"quota / {rate}. That's what each request took from the user's balance on your site, "
                     "not what the upstream provider charged you"
                     + ("" if quota_per_usd else ". Changed QuotaPerUnit? Pass --quota-per-usd"))
        if other_types:
            notes.append(f"left out {other_types:,} log row{'s' if other_types != 1 else ''} that aren't usage "
                         "(top-ups, logins, admin changes, errors...)")
        if by_id:
            notes.append(f"{by_id:,} request{'s' if by_id != 1 else ''} had no username, so the account is the "
                         "user id (id:...)")
        if users:
            accounts = len({r["account_id"] for r in rows})
            notes.append(f"emails from {users}: found for {len(with_email):,} of {accounts:,} accounts")
        if rows and without_ip == len(rows):
            notes.append("no IPs in these logs (New API only records them for users who turn on IP logging), "
                         "so farms can't be linked by network")
    # no IPs at all: leave the column out, so it's not reported as thousands of "bad" IPs
    columns = [c for c in COLUMNS if c != "ip"] if rows and without_ip == len(rows) else list(COLUMNS)
    if users:
        columns.append("email")
    return [*columns, "request_id"], rows
