# Third-party notices

The root MIT license covers original TestFix-Agent contributions. It does not replace the notices below or grant rights in separately supplied dependencies.

## Bundled source fixtures

| Location | Upstream and fixed revision | License and retained notice |
| --- | --- | --- |
| `experiments/rich-cli/baseline/` | [Textualize/rich-cli](https://github.com/Textualize/rich-cli/tree/46f4d2469097be395558768714a5f07ebccf1412), commit `46f4d2469097be395558768714a5f07ebccf1412` | MIT, Copyright (c) 2022 Will McGugan; complete text retained in `experiments/rich-cli/baseline/LICENSE` |
| `experiments/flaskr/baseline/` and `candidate/` | [Pallets/flask](https://github.com/pallets/flask/tree/2c1b30d0503cfb064f1cb252e6614a06915a362a/examples/tutorial), commit `2c1b30d0503cfb064f1cb252e6614a06915a362a` | BSD-3-Clause, Copyright 2010 Pallets; complete text retained in both `LICENSE.txt` and `UPSTREAM-LICENSE.txt` under each tree |

The Flaskr candidate is an experimental derivative, not an unmodified upstream release. The associated acceptance, regression and generated test fixtures are TestFix project contributions. Upstream fixture files are preserved byte-for-byte where specified by `source.json`; their original copyright and license notices remain effective.

## Separately obtained source and data

The seven WCTP core files under `testfix/_vendor/wctp/` are supplied by the project team, which confirmed authorization to publish this subset on 2026-11-07. `SOURCE.json` records the pinned SHA-256 values. The adapter loads the original graph executor, parser, registry and context directly, checks their hashes and relocates imports to a private namespace. The source-provider's original internal-use Markdown documentation is omitted. The TestFix MIT license covers the adapters and original TestFix contributions and does not replace third-party rights in the WCTP dependency.

HumanEval data are not bundled. The downloader obtains [openai/human-eval](https://github.com/openai/human-eval/tree/6d43fb980f9fee3c892a914eda09951f772ad10d), commit `6d43fb980f9fee3c892a914eda09951f772ad10d`, and preserves its MIT license with the downloaded data. Historical session capture JSON files are omitted from this public release.

## Skill and architecture references

All nine `testfix/skill_library/` Skill documents and contracts were written for TestFix. No third-party Skill package or long prompt is vendored. They refer to the Agent Skills file format, mini-SWE-agent execution-feedback patterns and the RoboAgent capability-interface paper. Source-specific revision, license and reuse boundaries are recorded in `docs/skill-sources.json`. Agent Skills reference code is Apache-2.0 and its documentation CC-BY-4.0; mini-SWE-agent is MIT. The paper is a research citation, not an open-source code license. Other architecture references in `docs/research.md` and `docs/repair-research.md` are not installed or vendored frameworks.

## Installed dependencies

Python dependency source distributions, wheels, model credentials and SDK source trees are not bundled. Runtime and development dependencies are declared in `pyproject.toml` / `uv.lock`; AgentArts SDK is separately pinned in `agentarts/requirements.txt`. Their own licenses apply when obtained through the relevant package manager. This source release does not relicense those dependencies or imply upstream endorsement.

## Bundled visualization dependency

`web/lab/vendor/three.module.min.js` is Three.js 0.170.0 from the official [r170 release](https://github.com/mrdoob/three/tree/r170), distributed under MIT. Its original license is retained in `web/lab/vendor/THREE-LICENSE.txt`; source URL and the fixed SHA-256 are recorded in `web/lab/vendor/SOURCE.md`. The local application does not request a CDN at runtime.

The hardware workbench bundle includes third-party frontend dependencies; their license texts are retained in `web/hardware-app/THIRD_PARTY_NOTICES.txt`.

The six office character GLB files in `web/implementation/vendor/models/` are from Gobkit Free Character Pack Vol.2 (`gobkit-human002-pack`), published under CC0 1.0. The complete CC0 text and source description are retained in that directory. `palette.png` is extracted from the unchanged models by the provided script. The accompanying GLTFLoader, BufferGeometryUtils and SkeletonUtils modules are Three.js r170 under MIT; their local import paths are adjusted, and the MIT license is retained in `web/implementation/vendor/THREE-LICENSE.txt`.

The pinned upstream [microsoft/FEA-Bench](https://github.com/microsoft/FEA-Bench) source snapshot and Lite task-ID list are retained under `third_party/FEA-Bench/` with Microsoft's original MIT `LICENSE`. Snapshot provenance is recorded in `third_party/SOURCE.json`. Its source is unmodified. Rebuilt task datasets, Docker images and local evaluator results are obtained separately. The TestFix integration is distinct from the upstream evaluator.

SWE-bench Lite and BugsInPy raw datasets are not bundled. `scripts/collect_benchmarks.py` downloads separately pinned metadata and retains evaluator-only source evidence locally. SWE-bench declares MIT for its harness; original project licenses remain effective. The pinned BugsInPy archive has no identified LICENSE, so its metadata is not redistributed by this source release. Collection and environment limitations are recorded in `docs/benchmark-datasets.md`.
