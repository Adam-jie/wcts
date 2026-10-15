# rich-cli real-defect fixture

Upstream: [Textualize/rich-cli](https://github.com/Textualize/rich-cli), MIT, commit `46f4d2469097be395558768714a5f07ebccf1412`. The original 34-file snapshot and license are preserved in `baseline/`; `source.json` fixes file hashes. The target is the real [CSV literal-data problem #147](https://github.com/Textualize/rich-cli/issues/147). No defect was injected.

`requirements.json` specifies CSV/TSV literal text, file/stdin/delimiter/row-selection behavior, HTML/SVG export and preservation of existing JSON/Markdown/error/`--print` behavior. Only `src/rich_cli/__main__.py` may change. Independent acceptance, original upstream tests and additional behavioral regression are separate fixtures.

Run `scripts/setup-richcli.ps1`, then use the ordinary `complete-project --project Project/rich-cli` entry or the manager/employee request in `examples/rich-cli-parallel-request.txt`. The latter requires independently authorized WCTP source, as documented in the root README. Setup only installs pinned dependencies and registers the local paths; it does not call a model.

The 2026-10-15 local parallel experiment used 13 real CLI model calls, requested `gpt-5.6-sol`, and finished in 427.709 seconds. The provider did not confirm the actual serving model name. Two independent exploration workers overlapped, then a repair worker used both handoffs. Generated tests improved 3/19 to 19/19, independent acceptance 3/21 to 21/21; original tests were 1/1, frozen behavior checks 13/13, wheel build succeeded and installed module-entry CLI checks were 16/16. See `docs/wctp-parallel.md` for limits, including extra formatting/docstring changes and the absence of a scheduling-speedup study.

Raw model captures, baseline execution receipts and machine-specific demo assets are excluded. For the public release, the evaluation seal was regenerated to cover only the retained test source and delivery-check configuration; baseline source hashes and test source bytes are unchanged. This publication seal is not represented as a new pre-model experiment timestamp. New local runs produce new immutable evidence under `artifacts/`.
