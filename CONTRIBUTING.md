# Contributing to LOCUS-X

Use a short feature branch, such as `feature/range-log-import`. The V3 foundation is on `feat/locus-x-v3-foundation`. See [the V3 handoff](docs/v3-handoff.md) for disjoint workstreams and shared integration contracts.

Keep estimator inputs limited to measured ranges, timestamps and known anchor positions. Never use simulator ground truth, true ranges or injected fault labels as features. Tests must exercise that boundary. Evaluate both methods on the same observation stream and report failures as well as improvements.

Before review, run the Python tests, Ruff check/format check, frontend typecheck/lint/tests/build, and the relevant reproducible benchmark command in README.md. Keep benchmark configurations with any claimed result. Report numerical dependency versions and keep wall-clock runtime out of reproducibility assertions.

Never overwrite the V1 environment, competition protocol or historical result artifacts when evaluating V3 changes. Use new configuration versions and output directories. The CLI protects existing result files. Scope production Ruff checks to `backend/locus_x`, `backend/tests` and `scripts`; the preserved `backend/research` currently has separate pre-existing lint/format findings.

Floor changes are manual resets of independent 2D runs. Never turn configured elevation or simulator floor identity into an inference claim. Keep door descriptions consistent with solid-wall gaps, and validate complete routes and aligned cores. Candidate sites must remain separate from installed anchors until an actual optimizer supplies a validated layout.

Use typed contracts, small modules, stable metre coordinates, and explicit unavailable states. Add tests for meaningful changes to estimation, tracking, session revision/reset behavior or the API. Avoid cosmetic tests that mirror component markup.

Do not commit `.venv`, `node_modules`, local secrets, generated build output or bulk experiment artifacts. Runtime does not need credentials or environment variables. Use the checked-in npm lockfile for dependency installation.
