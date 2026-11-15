# Troubleshooting

Start with the offline checklist — it tells you which layer is broken:

```bash
python run.py smoke
```

## Setup problems

| Symptom | Cause | Fix |
|---|---|---|
| `python: command not found` / `python3` works | macOS/Linux often only has `python3` | Use `python3 -m venv .venv`; inside an activated venv, `python` works |
| `Missing Python package: openai` (or `pydantic`, `httpx`, `dotenv`) | Venv not activated or dependencies not installed | Activate (`source .venv/bin/activate` or `.venv\Scripts\Activate.ps1`) then `pip install -e ".[dev]"` |
| `ERROR: Package requires a different Python` | Python older than 3.10 | Install Python 3.10+ (3.12 recommended) and recreate the venv |
| PowerShell: "running scripts is disabled" | Execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or use `.venv\Scripts\activate.bat` in Command Prompt |
| `Could not find the project root` / `Missing file: …data/…` | Running from the wrong folder | `cd` into the repository folder (the one containing `run.py`) |
| `zsh: no matches found: .[dev]` | zsh treats brackets as a pattern | Quote it: `pip install -e ".[dev]"` |

## API key and model problems

| Symptom | Fix |
|---|---|
| `OPENAI_API_KEY is not set` | `cp .env.example .env` (Windows: `copy .env.example .env`) and paste your key after `OPENAI_API_KEY=` — no quotes or spaces |
| Result shows `status: error` with `401` / `invalid_api_key` | Key is wrong or revoked; create a new one in the OpenAI dashboard |
| `429` / `insufficient_quota` | Add billing or credits to your OpenAI account; wait and retry for rate limits |
| `model_not_found` / `does not exist` | Set `OPENAI_MODEL` in `.env` to a model your account can use |
| Error mentioning `reasoning` or `include` | Your chosen model doesn't support reasoning — set `OPENAI_REASONING_EFFORT=none` |
| Run ends with `iteration_limit` | Raise `AGENT_MAX_ITERATIONS` in `.env`, or check the trace for a model stuck repeating a tool |

When a run fails, the result is still printed with `escalation: REQUIRED` and
the error message, and the log line `run_error` contains details:

```bash
grep run_error logs/agent-*.jsonl          # macOS/Linux
Select-String run_error logs\agent-*.jsonl # Windows PowerShell
```

## Command problems

| Symptom | Fix |
|---|---|
| `Unknown scenario 'x'` | `python run.py demo --list` shows valid ids |
| `Unknown event 'x'` | `python run.py webhook --list` |
| Webhook says `DUPLICATE` | Working as intended — that event id was already processed. Add `--reset` to process it again |
| `approvals` shows nothing | Approval requests only appear after a model-backed run that creates one (e.g. `frustrated_customer`) |

## Unexpected tool results

| Symptom | Explanation |
|---|---|
| `order_not_found_for_requester` for an order that exists | The order belongs to a different customer than the ticket requester. This is the privacy check working. Try `python run.py tool get_shopify_order --ticket 5002 --arg order_id=1043` (Jordan's own ticket) |
| Delivery states look wrong (e.g. nothing is late) | `NORTHSTAR_REFERENCE_DATE` changed. The mock data assumes `2026-11-10`; `now` uses the real clock and every order will look very old |
| Search returns an unexpected document first | Search is keyword-based (BM25). Try more specific words, or add synonyms in `knowledge.py` |
| Changed a policy number and tests fail | Update the policy text, its front matter, and any FAQ that quotes it; `test_no_conflicting_sources_of_truth` names the mismatch |
| Edited `data/*.json` and smoke fails "Data & policy consistency" | The message names the inconsistent order/product (prices, totals, SKUs, dates) |

## Evaluation failures

Model behavior varies. When `python run.py eval` reports a failure:

1. Read the failed check line (it shows expected vs. actual).
2. Re-run that case a few times: `python run.py eval --case <id> --repeat 3 --verbose`.
3. Inspect the trace for that run's `trace_id` in `logs/`.
4. Decide whether to improve the prompt/workflow, the tool output, or the check
   itself (if the check was too strict for an acceptable answer).

## Claude Skill problems

| Symptom | Fix |
|---|---|
| Upload rejected | Upload `claude-skill/customer-support-skill.zip` (the download) or `dist/customer-support-skill.zip` from `python run.py build-skill`, not the `claude-skill/customer-support/` folder zipped by hand (it lacks the copied references) |
| "Could not find SKILL.md" | The ZIP must contain `customer-support/SKILL.md` (one top-level folder). The build script verifies this |
| Skill never triggers | Mention support tickets explicitly ("Use the customer-support skill to handle this ticket: …"); check the Skill is toggled on in Settings |
| Skills option not visible | Custom Skills require a paid plan and the Skills/code-execution capability enabled in Settings → Capabilities |
| Claude claims to look up a real order | It must not in Mode A. Remind it: "No connectors are configured; use sample data only." Consider tightening SKILL.md Step 0 |
