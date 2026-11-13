# Contributing

Thanks for wanting to help. luxvigil is small on purpose, so a few things first.

## Ground rules

- **Offline and explainable.** Every check is a rule you can read and every finding says why it fired. No ML models, no calls to outside services, nothing that sends logs anywhere.
- **No raw secrets or content.** Never read a raw API key, never put an email or a prompt's text in the report or the JSON. Hashes and ids only.
- **Rather miss some abuse than accuse a real user.** If a new rule flags normal people, it's not ready. Real-data checks like the ones in `benchmarks/` are the bar.

## Setting up

```
git clone https://github.com/polfananas/luxvigil
cd luxvigil
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
luxvigil demo
```

## Making a change

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Keep pull requests small and focused, with tests. `pytest -q` has to pass on Python 3.11+.
3. If the change touches what the report says, check `luxvigil demo` still makes sense.
4. Write it the way the rest of the code is written: comments that say why, plain names.

## Ideas welcome

The "What's next" list in the README is where things are going. Real-world cases are gold: if luxvigil missed something in your logs, or flagged something it shouldn't have, an issue describing the pattern (no data needed) helps a lot.

## Security issues

Please don't open a public issue, see [SECURITY.md](SECURITY.md).
