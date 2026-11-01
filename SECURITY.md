# Security

## Reporting a vulnerability

If you find a security issue in luxvigil, please don't open a public issue. Report it privately instead: go to the **Security** tab of this repo and click **Report a vulnerability**. Only you and the maintainer can see the report.

It helps a lot if you include:

- what the problem is and where it lives (file, function or command)
- how to reproduce it, ideally with a small CSV that triggers it
- what you think the impact is

I'll try to reply within a week, keep you posted while I work on a fix, and credit you in the release notes if you want.

## What counts

luxvigil reads logs that often hold sensitive stuff (emails, IPs, key ids), so these are the things I care most about:

- a crafted log file that crashes or hangs luxvigil, or makes it read or write files it shouldn't
- anything that sends data off your machine (it never should)

Not security issues, so a normal issue is fine:

- a check that misses abuse or flags something that isn't abuse
- the fake abuse in `luxvigil generate` output (that's planted on purpose)
- problems with the disposable email list itself, which comes from [disposable-email-domains](https://github.com/disposable-email-domains/disposable-email-domains) and should be fixed there

## Supported versions

luxvigil is still pre-1.0, so only the latest version gets fixes.
