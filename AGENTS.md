# AGENTS.md

## Agent Role

Use this file as practical guidance for coding agents working in this repository. The agent's main jobs are:

- implement or repair the current prototype from the committed specs,
- run the appropriate verification commands,
- keep run artifacts and result interpretation reproducible,
- avoid claims that exceed the implemented code and recorded measurements.

This is an academic research prototype for feedback-driven SQL rewrite search. It is not a production optimizer and it is not a formal SQL equivalence prover.

## Build From The Specs

Read these files before broad implementation work:

1. `README.md`
2. `specs/00-research-idea.md`
3. `specs/01-system-requirements-spec.md`
4. `specs/02-implementation-and-experiment-spec.md`
5. `specs/03-future-work-not-implemented.md`

Use the specs to preserve the intended architecture:

- The rewrite service owns SQL parsing, deterministic rewrite rules, prompt construction, model response parsing, candidate structural checks, and result-set equivalence validation.
- The workload runner owns manifest loading, schema context, service orchestration, metadata persistence, paired benchmarking, candidate-selection state, promotion, held-out measurement, monitoring export, and raw run-data packaging.
- Rules and local LLMs are candidate generators only. They do not establish semantic correctness.
- Candidates must pass validation and benchmark gates before promotion.
- Held-out measurement remains separate from search and promotion data.

If code behavior changes, update relevant specs and tests when practical. If a requested feature is not implemented, place it under future work instead of implying it exists.

## Public Commands

Use only the public command surface unless debugging an implementation helper:

```powershell
.\scripts\run-fast-check.ps1
.\scripts\run-main-run.ps1 -Model <ollama-tag> -Corpus tpch
.\scripts\run-main-run.ps1 -Model <ollama-tag> -Corpus real-world
.\scripts\run-main-run.ps1 -Model <ollama-tag> -Corpus job-imdb
.\scripts\run-main-run.ps1 -Model <ollama-tag> -All
```

`scripts/lib/` contains private helpers called by the public scripts.

## Verification Guidance

Prefer the smallest check that proves the change:

- General check: `.\scripts\run-fast-check.ps1`
- Rewrite service: `uv --directory src\rewrite-service run pytest`
- Rewrite service lint/types: `uv --directory src\rewrite-service run ruff check app tests` and `uv --directory src\rewrite-service run mypy app`
- Workload runner: `dotnet build src\QueryOptimizer.WorkloadRunner\QueryOptimizer.WorkloadRunner.csproj --no-restore`
- Runner tests: `dotnet run --project tests\QueryOptimizer.WorkloadRunner.Tests\QueryOptimizer.WorkloadRunner.Tests.csproj --no-restore`

Run expensive Docker/Ollama benchmark commands only when the task requires experiment validation. If Docker, Ollama, external datasets, GPU-backed checks, or long benchmark runs are skipped, say so explicitly.

## Experiment Runs And Artifacts

- Human-readable result interpretation lives in `experiment-results/`.
- Raw run-data archives live in `experiment-artifacts/` as `<run-id>-raw-run-data.zip` with `<run-id>-SHA256SUMS.txt`.
- Do not commit generated benchmark data, database volumes, downloaded model files, external JOB/IMDB data, secrets, or unbundled run outputs unless the task explicitly asks for a curated recorded artifact.
- JOB/IMDB official SQL and IMDB data are external staged inputs, not repository contents.

## Research Guardrails

- Scope is read-only `SELECT` queries in a restricted safe fragment.
- PostgreSQL is the reported target database engine.
- Workloads are controlled and file-based, not production query interception.
- Empirical result-set comparison is evidence for the configured data and parameters, not universal SQL equivalence.
- Promotion requires repeated paired evidence, not a single faster execution.
- Negative, null, rejected, and no-candidate outcomes are valid research results.
- Keep claims limited to implemented behavior and recorded measurements.
- Preserve theme-neutral Mermaid diagrams unless an architecture change requires updating them.
