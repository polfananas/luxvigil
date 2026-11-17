# Maintaining luxvigil

Notes for whoever has the keys: how a release goes out, and what to do when something goes wrong. If you just want to contribute, [CONTRIBUTING](CONTRIBUTING.md) is the one you want.

## Releasing

1. Bump `__version__` in `src/luxvigil/__init__.py` and move the "Unreleased" notes in `CHANGELOG.md` under the new version, in a PR like any other change.
2. Once it's on `main`, draft a GitHub release with a tag that matches the version (`v0.3.0` for `0.3.0`) and publish it.
3. The `release` workflow checks the tag matches the code, runs the tests, builds and uploads to PyPI. There's no PyPI token anywhere: PyPI trusts this repo's workflow directly ("trusted publishing"), so there's nothing to leak.
4. Check it landed: `pipx run --spec luxvigil==0.3.0 luxvigil demo` from a clean shell.

## When something goes wrong

**A broken release on PyPI.** Don't delete it, yank it: on pypi.org, Manage project → Releases → that version → Options → Yank, with a one-line reason. A yanked version stays downloadable for whoever pinned it exactly, but `pip install luxvigil` skips it. Then fix it and release a patch version. A version number can never be reused on PyPI, even after deleting it.

**A bad change on `main`.** Revert it with a PR (`git revert <commit>`), don't force-push. `main` only takes changes through pull requests with the tests green.

**Lost access, or the GitHub or PyPI account might be compromised.**

- Get back in with the 2FA recovery codes (keep them in a password manager, not only on the phone).
- GitHub: Settings → Sessions, sign out everywhere. Developer settings, revoke any token you don't recognise. Check the Security log for what happened.
- PyPI: change the password, check the 2FA devices and the account's security history.
- If the release workflow itself could be tampered with, remove the trusted publisher on PyPI (Manage project → Publishing) until it's clean. Nothing gets to PyPI without it.
- Look at the latest releases. If one is suspicious, yank it and say so in a GitHub security advisory.

**Someone reports a vulnerability** (privately, see [SECURITY](SECURITY.md)): reply, reproduce it, fix it in a private fork from the advisory (Security → Advisories), release, then publish the advisory with credit to whoever found it.

**A dependency of the workflows changes.** The actions are pinned to a commit, not a tag. Dependabot opens a PR when there's a new version, and it gets reviewed like any other change.

## Backups

The code lives in git, so every clone is a full backup. Released versions on PyPI can't be changed. The few things that only live in the GitHub and PyPI settings (rules, trusted publisher, who has access) are listed above, so they can be set up again.
