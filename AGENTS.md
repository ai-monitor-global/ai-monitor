# AI Monitor

Read `RUNBOOK.md`, the full `ROUTINE_PROMPT.md`, `CRITERIA.md`, and `automation/task.json`
before a weekly run. Research rules and the existing data gates are model-independent.

- Work runs research and scripts directly in its cloud environment. No Dot worker,
  fixed workspace path, local computer, provider SDK, or API key is required for this path.
- Use `work_pipeline.py prepare` and its versioned research-bundle interface.
- All data changes still go through the existing `apply.py` and `validate.py`.
- Routine commits may change only `data.json` and the current weekly `runs/` ledger.
- Do not run the API fallback scripts or `fetch_openrouter.py` in a Work research run.
- Respect repository and remote revision checks. Never force-push or merge data.json blindly.
- Keep research completion, Git publication and live Pages verification distinct.
- Credentials and private account data must never be written into this public repository.

For authorized implementation maintenance, run `python3 validate.py --selftest`,
`python3 validate.py` and `python3 -m unittest discover -s tests -v`.
Use temporary repositories for synthetic tests; never publish test research to main.
