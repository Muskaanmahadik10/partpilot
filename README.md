# PartPilot

[![ci](https://github.com/Muskaanmahadik10/partpilot/actions/workflows/ci.yml/badge.svg)](https://github.com/Muskaanmahadik10/partpilot/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Muskaanmahadik10/partpilot/actions/workflows/codeql.yml/badge.svg)](https://github.com/Muskaanmahadik10/partpilot/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Muskaanmahadik10/partpilot/badge)](https://scorecard.dev/viewer/?uri=github.com/Muskaanmahadik10/partpilot)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**A role-aware parts and material-planning assistant.** Planners, product owners, plant leads and component engineers ask
questions in plain language ("How many UNO boards can North Ridge build on 15 March?", "Can the LM1117 replace the
NCP1117?"). An LLM picks tools; **every number comes from deterministic Python**, and **permissions are enforced in code
before any data reaches the model**.

The sample data is built from three public open-hardware designs that hardware people recognise: the **Arduino UNO
Rev3**, the **Raspberry Pi Pico** and the **Prusa i3 MK3S+** printer kit (multi-level BOM). Stock, purchase orders,
prices, suppliers and plants are synthetic and labelled as such ([sources and licenses](data/reference/SOURCES.md)).

![Planner asking how many boards can be built](docs/screenshots/02_planner_chat.png)

## What it does

| Capability | How |
|---|---|
| Multi-level BOM explosion | product → assembly → sub-assembly → component; effective dates (engineering changes), scrap rates, unit conversion (KPCS, CM, MM) |
| Available stock | only RELEASED lots, on hand − allocated, shelf-life expiry from the ISO-week date code |
| In-transit supply | open PO lines, ordered − received, counted only if ETA + 2 inspection days ≤ production date |
| Substitute parts | 11 rules: approval, validity window, direction, no chaining, scope (global / product line / assembly), lifecycle, spec check (voltage, tolerance, dielectric, package, RoHS, value), quantity ratio, no mixing, matched sets that switch together, priority |
| Shortage and max buildable | binary search over whole units, substitutes counted in whole parent assemblies, the binding part reported |
| Roles and field sensitivity | 5 roles; supplier names *sensitive*, unit costs *restricted*; aggregates over fewer than 3 records become "fewer than 3" |
| Human review | pending substitutes and data conflicts wait for the engineer who owns the commodity; one click approves |
| Data cleaning | a seeded generator plants duplicates, mixed units, bad dates, conflicts, a circular BOM and a prompt-injection remark; the cleaner fixes or quarantines each and writes a report |
| Audit | every question is logged with user, role, tools, denials, tokens, cost and latency; the auditor sees metadata only |

## Architecture

```mermaid
flowchart LR
    U[User in browser<br/>Streamlit UI / CLI] -->|login PBKDF2| A[Agent loop<br/>partpilot/agent.py]
    A <-->|tool calls only| L[LLM provider<br/>Anthropic / Groq / Ollama<br/>or offline router]
    A --> T[Tool layer<br/>partpilot/tools.py]
    T --> V[validate + normalise args]
    V --> P[Permission layer<br/>perm.py: role scope,<br/>field sensitivity, aggregates]
    P --> E[Deterministic engine<br/>engine.py: BOM, stock, PO,<br/>substitute rules, max buildable]
    E --> D[(Clean store<br/>data_clean/)]
    T --> G[Scope guard<br/>drops rows naming<br/>out-of-scope parts]
    G -->|filtered JSON, no Remark| A
    A --> AU[(Audit log)]
    R[(data/raw<br/>messy CSVs)] --> C[Cleaner<br/>clean.py] --> D
    C --> Q[Review queue] --> RV[Engineer approves<br/>Review page] --> E
```

Guards that do not depend on the model behaving:

- **Evidence gate**: if no tool ran, the answer is replaced with "no verified tool result".
- **Send guard**: requests to send data outside the company are flagged in code; a reply that claims "I have sent …" is replaced.
- **Remarks never reach the model**: free-text remarks (where the planted injection lives) are stripped in Python.
- **Uniform denials**: an out-of-scope part and a non-existent part get the same answer, so scope cannot be probed.
- **No price subtraction attacks**: cost statistics only over predefined groups, IDs de-duplicated before the fewer-than-3 rule, no min/max.

## Roles

| Role (demo user) | Sees | Supplier names | Unit costs | Can approve |
|---|---|---|---|---|
| Supply Planner (`planner`) | whole company | aggregate only | group averages only | no |
| Product Owner (`po_boards`, `po_printers`) | one product line + direct substitutes | aggregate only | no | no |
| Plant Materials Lead (`lead_nrg`, `lead_bhv`) | own plant's stock and POs | yes | no | no |
| Component Engineer (`eng_passives`, `eng_semis`, `eng_electromech`, `eng_mech`) | own commodity group + every assembly that uses it | yes | yes | pending items of own commodities |
| Auditor (`auditor`) | no parts data; audit metadata, cleaning job | – | – | – |

## Quick start

### Docker (one command)

```bash
docker compose up --build
```

Open <http://localhost:8501>. On the first start the container generates the data, cleans it and creates the demo
accounts. Without settings it runs in **offline mode** (deterministic router, no LLM, no key). The demo password is
`PARTPILOT_DEMO_PASSWORD` from your `.env`, or, if unset, a random one printed once in `docker compose logs`.

To use a model, copy `.env.example` to `.env` and set, for example:

```bash
PARTPILOT_LLM_PROVIDER=ollama
PARTPILOT_MODEL=qwen2.5:7b
PARTPILOT_BASE_URL=http://host.docker.internal:11434/v1   # Ollama on the host
```

or `PARTPILOT_LLM_PROVIDER=anthropic` / `groq` with `PARTPILOT_API_KEY`.

### Local

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt     # Windows: .venv\Scripts\pip
python data/generate.py               # seeded raw data -> data/raw/
python -m partpilot.clean             # -> data_clean/ + reports/cleaning_report.md
python -m partpilot.auth init config/accounts.example.csv   # asks for one demo password
streamlit run streamlit_app.py        # web UI
python -m partpilot.app               # or the CLI
```

## Evaluation

`eval/eval_set.json` has 20 questions on this data with expected facts (`must`) and forbidden content (`must_not`):
planning and BOM questions, 7 permission probes (other product line, other plant, restricted cost, sensitive supplier,
auditor, capacity that depends on hidden parts) and 3 injection probes (the planted remark, a "treat it as approved"
instruction, an external e-mail request).

```bash
python eval/run_eval.py                                              # offline baseline, no key
PARTPILOT_LLM_PROVIDER=ollama python eval/run_eval.py --model qwen2.5:7b
```

Measured on 23 Nov 2026 on a laptop (local models through Ollama, CPU/GPU shared with other work, so latency is
only indicative). Rebuild the table with `python eval/summary.py`; per-case answers are in `reports/eval_*.json`.

| Setup | Accuracy (20 cases) | Permission/injection violations (10 probes) | Forbidden claims (all cases) | Avg tool calls | Avg tokens in / out | Avg latency | Failed cases |
|---|---|---|---|---|---|---|---|
| offline rules router (no LLM) | 75% | 0% | 0% | 1.2 | 0 / 0 | 0.0 s | E03, E12, E16, E17, E18 |
| ollama qwen2.5:3b | 85% | 0% | 5% | 1.1 | 4792 / 188 | 64.6 s | E02, E09, E11 |
| ollama qwen2.5:7b | 95% | 0% | 5% | 1.7 | 6079 / 258 | 127.6 s | E11 |
| ollama qwen2.5:7b - before the MPN fix | 80% | 0% | 0% | 1.4 | 5239 / 224 | 154.3 s | E06, E07, E09, E11 |

What the numbers say:

- **No permission or injection leak in any run.** Scope, field sensitivity and remark stripping are enforced in Python,
  so even the 3B model could not reveal another plant's stock, a restricted cost, a sensitive supplier name or the
  other product line, and nobody "approved" the substitute whose remark tries to instruct the assistant.
- **The eval found a real usability bug.** Before the fix, qwen2.5:7b passed manufacturer part numbers such as `RP2040`
  straight into the tools, got "use search_parts", and then asked the *user* to do it (E06, E07). The tools now resolve
  an exact, unique MPN to the internal number (still scope-checked, no fuzzy guessing); 7B went from 80 % to 95 %.
- **Remaining errors are wording errors on top of correct tool data.** In E11 both models had the right tool result
  (only CA-1010 / CL10B104KB8NNNC is usable; records are never chained) but the 7B model attached the wrong MPN to it,
  and in E09 the 3B model wrote "can replace" while quoting the failed spec check. The tool trail in the UI shows the
  correct basis, but the answer text itself was wrong, so both count as failures. A stronger hosted model
  (`PARTPILOT_LLM_PROVIDER=anthropic` or `groq`) can be compared with the same command; no hosted model was run for this table.
- The offline rules router never leaks either, but it cannot handle multi-part questions (75 %).

## Screenshots

All taken in offline mode (no LLM) with `docs/make_screenshots.py`.

| | |
|---|---|
| ![login](docs/screenshots/01_login.png) Login and demo accounts | ![denied](docs/screenshots/03_plant_lead_denied.png) Plant lead asks about another plant: denied in code |
| ![substitute](docs/screenshots/04_engineer_substitute.png) Engineer: can the LM1117 replace the NCP1117? (spec check fails on input voltage) | ![review](docs/screenshots/06_review_injection_remark.png) Review queue: the planted injection remark is shown to the human only |
| ![audit](docs/screenshots/07_auditor.png) Auditor: metadata only, no questions or answers | ![quality](docs/screenshots/08_data_quality.png) Data-quality report from the cleaner |

## Tests

```bash
python -m pytest -q --cov=partpilot
```

Result on 23 Nov 2026: **97 passed, 91 % line coverage** of `partpilot/` (Python 3.13, Windows; CI runs Python 3.12 on Ubuntu).

The tests generate and clean their own data in a temporary folder and never call a real model: the agent loop runs
against a scripted mock LLM, and the provider adapters are tested with a fake HTTP client.

## Project layout

```
partpilot/        normalize, clean, engine, perm, tools, agent, llm, offline, review, auth, bootstrap, app (CLI)
streamlit_app.py  web UI (login, chat, review, audit, data quality)
data/reference/   public BOMs (UNO Rev3, Pico, MK3S+) + SOURCES.md
data/generate.py  seeded generator for synthetic stock / POs / substitutes + planted data problems
eval/             eval set and runner
tests/            pytest suite (no key needed)
reports/          cleaning report and eval results
```

## Limitations

- The printer BOM is an approximation of the public kit manual, not an official bill of materials.
- The offline router handles one intent per question and no follow-ups; it exists so the demo works without a key
  and as the rules baseline in the eval.
- Accounts live in a JSON file with PBKDF2 hashes; a real deployment would use the company's identity provider.

## License

Code: MIT ([LICENSE](LICENSE)). Reference BOM data: see [data/reference/SOURCES.md](data/reference/SOURCES.md)
(UNO data CC BY-SA 4.0; Pico data under Raspberry Pi's open design-file terms; MK3S+ printed-part names from a GPL-2.0 repository).
PartPilot is not affiliated with Arduino, Raspberry Pi or Prusa Research.
