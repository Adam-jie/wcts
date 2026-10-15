# Flaskr engineering fixture

The original tutorial snapshot comes from Pallets/flask commit `2c1b30d0503cfb064f1cb252e6614a06915a362a`, subdirectory `examples/tutorial`, licensed BSD-3-Clause. Its full license is retained in `baseline/LICENSE.txt` and `UPSTREAM-LICENSE.txt`; the historical candidate retains the same notices.

The public contract adds article details, literal title search and five-item pagination while preserving registration, login, CRUD and SQLite behavior. `requirements.json` records exact semantics. The baseline is unmodified upstream; `candidate/` and `generated_tests/` are historical experimental derivatives for inspection and local demonstration, not answers used by a new autonomous run.

Initialize the main environment, then run `scripts/setup-project.ps1`. Start a fresh real-model run using `python -X utf8 -m testfix.cli complete-project --project Project/flaskr --provider codex_cli --model gpt-5.6-sol`. The controller copies the baseline into a new workspace and freezes independent acceptance and original tests. Registered checks include 24 original regressions, 20 independent acceptance cases, wheel building and 40 HTTP checks including SQLite restart.

Historical model captures, private machine paths and execution receipts are excluded from this public source release. Fresh evidence is generated under `artifacts/` and must be reviewed locally before any publication. Local results are not AgentArts cloud-deployment evidence.
