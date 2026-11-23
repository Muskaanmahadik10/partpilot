# Security policy

## Supported versions

PartPilot is a portfolio project maintained by one person. Only the latest commit on the `main` branch receives fixes; there are no release branches.

## Reporting a vulnerability

Please report security problems privately, not in a public issue:

1. Open <https://github.com/Muskaanmahadik10/partpilot/security/advisories/new> ("Report a vulnerability" on the repository's **Security** tab).
2. Describe the problem, the affected file, and the steps to reproduce it.

If private reporting is unavailable, open a public issue that says only that you have a security report and asks for a private contact. Do not include details in that issue.

## What to expect

- An acknowledgement within 7 days.
- An assessment and, if the report is confirmed, a fix or a documented mitigation on `main` within 30 days.
- Credit in the fix commit, if you want it.

## Scope

In scope: the code in this repository (agent, tools, permission layer, cleaner, web UI, Docker setup).

Out of scope: third-party model providers and their APIs, and the sample data (public open-hardware BOMs plus synthetic stock, PO, price and supplier records). The project runs on a laptop by default and is not deployed as a public service.
