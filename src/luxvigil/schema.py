"""The log format luxvigil reads: one row per API request, as a CSV."""

import csv
from dataclasses import dataclass
from datetime import datetime

# column -> what it is
COLUMNS = {
    "timestamp": "when the request happened, ISO 8601 (2026-10-01T14:03:22Z)",
    "account_id": "your internal id for the account",
    "email": "email the account signed up with",
    "api_key_id": "id or hash of the key used (never the key itself)",
    "ip": "client IP address",
    "country": "2-letter country code of the IP",
    "device_id": "device / browser fingerprint, if you have one (can be empty)",
    "prompt_hash": "hash of the prompt text, to spot repeated prompts without storing them",
    "input_tokens": "tokens in the request",
    "output_tokens": "tokens in the response",
    "cost_usd": "what that request cost you",
}

REQUIRED = [c for c in COLUMNS if c != "device_id"]


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
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_logs(path):
    """Read a log CSV and return a list of Request. Fails loudly on bad input."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise LogFormatError(f"missing columns: {', '.join(missing)}")

        rows = []
        for line, r in enumerate(reader, start=2):
            try:
                rows.append(
                    Request(
                        timestamp=_parse_time(r["timestamp"]),
                        account_id=r["account_id"],
                        email=r["email"].strip().lower(),
                        api_key_id=r["api_key_id"],
                        ip=r["ip"],
                        country=r["country"].upper(),
                        device_id=r.get("device_id") or "",
                        prompt_hash=r["prompt_hash"],
                        input_tokens=int(r["input_tokens"]),
                        output_tokens=int(r["output_tokens"]),
                        cost_usd=float(r["cost_usd"]),
                    )
                )
            except (ValueError, KeyError) as e:
                raise LogFormatError(f"line {line}: {e}") from e
    return rows
