# Third-party notices

The root MIT license covers original TestFix-Agent contributions. It does not replace the notices below or grant rights in separately supplied dependencies.

## Bundled source fixtures

| Location | Upstream and fixed revision | License and retained notice |
| --- | --- | --- |
| `experiments/rich-cli/baseline/` | [Textualize/rich-cli](https://github.com/Textualize/rich-cli/tree/46f4d2469097be395558768714a5f07ebccf1412), commit `46f4d2469097be395558768714a5f07ebccf1412` | MIT, Copyright (c) 2022 Will McGugan; complete text retained in `experiments/rich-cli/baseline/LICENSE` |
| `experiments/flaskr/baseline/` and `candidate/` | [Pallets/flask](https://github.com/pallets/flask/tree/2c1b30d0503cfb064f1cb252e6614a06915a362a/examples/tutorial), commit `2c1b30d0503cfb064f1cb252e6614a06915a362a` | BSD-3-Clause, Copyright 2010 Pallets; complete text retained in both `LICENSE.txt` and `UPSTREAM-LICENSE.txt` under each tree |

The Flaskr candidate is an experimental derivative, not an unmodified upstream release. The associated acceptance, regression and generated test fixtures are TestFix project contributions. Upstream fixture files are preserved byte-for-byte where specified by `source.json`; their original copyright and license notices remain effective.

## Separately obtained source and data

WCTP is an external, source-pinned dependency for dynamic manager/employee collaboration only. Its original source and private device/GUI stack are **not bundled**. No upstream redistribution license was established from the supplied materials. Obtain permission independently before supplying it via `TESTFIX_WCTP_ROOT`; the TestFix MIT license does not cover it. The new `testfix/wctp_runtime.py` and `wctp_bridge.py` adapters are TestFix contributions. Missing or changed WCTP source produces a clear failure and never a substitute implementation.

HumanEval data are not bundled. The downloader obtains [openai/human-eval](https://github.com/openai/human-eval/tree/6d43fb980f9fee3c892a914eda09951f772ad10d), commit `6d43fb980f9fee3c892a914eda09951f772ad10d`, and preserves its MIT license with the downloaded data. Historical session capture JSON files are omitted from this public release.

## Skill and architecture references

All nine `testfix/skill_library/` Skill documents and contracts were written for TestFix. No third-party Skill package or long prompt is vendored. They refer to the Agent Skills file format, mini-SWE-agent execution-feedback patterns and the RoboAgent capability-interface paper. Source-specific revision, license and reuse boundaries are recorded in `docs/skill-sources.json`. Agent Skills reference code is Apache-2.0 and its documentation CC-BY-4.0; mini-SWE-agent is MIT. The paper is a research citation, not an open-source code license. Other architecture references in `docs/research.md` and `docs/repair-research.md` are not installed or vendored frameworks.

## Installed dependencies

Python dependency source distributions, wheels, model credentials and SDK source trees are not bundled. Runtime and development dependencies are declared in `pyproject.toml` / `uv.lock`; AgentArts SDK is separately pinned in `agentarts/requirements.txt`. Their own licenses apply when obtained through the relevant package manager. This source release does not relicense those dependencies or imply upstream endorsement.
