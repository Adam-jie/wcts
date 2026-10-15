# Contributing

Use Python >= 3.11 and `uv sync --extra dev --locked`. Start with the WCTP-independent check command in README; run the complete suite only with an independently authorized, hash-matching WCTP dependency. Never copy private WCTP source into a pull request.

Keep original, generated, regression and independent acceptance tests separate. Do not weaken assertions, remove failed requirements or replace model errors with replay success. Bind patches and reviews to the exact candidate hash, and preserve existing run evidence. New adapters need a pinned source revision, an explicit allowed-change scope and independent acceptance before reporting completion.

Do not commit `.env`, keys, OAuth files, personal contact details, local absolute paths, captured model prompts/responses or `artifacts/`. Use synthetic, explicitly labeled fixtures for unit tests. Report real model runs separately from mocks and replay, including limitations and actual reported model identity.

Changes to scheduling should verify dependency order, resource limits and file conflicts. New performance claims require comparable tasks and controlled measurements; a feasible CP-SAT solution or one overlapping call is not evidence of general speedup.
