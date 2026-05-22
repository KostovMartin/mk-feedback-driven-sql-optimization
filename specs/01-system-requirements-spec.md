# System Requirements Specification

## Purpose

This document defines the required behavior of a feedback-driven SQL rewrite search system. It is implementation-oriented but technology-light: it specifies capabilities, safety constraints, evidence requirements, and operational boundaries without prescribing a specific programming language or deployment stack.

## Design Rationale

The system does not attempt to replace a database optimizer. It searches over alternative SQL formulations and keeps only candidates that survive validation and measurement. This distinction matters because a rewrite generator can be syntactically fluent, rule-based, or statistically plausible while still being semantically wrong or slower under the target workload.

The central design choice is therefore staged distrust. Candidate generators are allowed to be broad, but every later stage narrows authority: structural checks reject unsupported SQL, empirical validation checks observed result equivalence, paired benchmarking measures execution behavior, and promotion requires repeated evidence. A negative, null, rejected, or no-candidate outcome is still useful because it characterizes where the method does not help.

## System Boundary

- The system operates on controlled workload files, not transparent production traffic.
- The input is a set of read-only SQL templates or fixed queries, parameter data where applicable, schema context, and run configuration.
- The output is an interpreted result summary plus machine-readable raw run data for candidates, validation, measurements, decisions, workload outcomes, and monitoring.
- The system may use deterministic and language-model candidate sources, but neither source is trusted to establish semantic correctness.
- The system is scoped to empirical evidence under the configured database, data, parameters, and hardware conditions.

## Functional Requirements

- The system accepts controlled read-only SQL workload definitions.
- The system generates candidate rewrites using deterministic rules and local language-model proposals together in a single candidate pool.
- The system records candidate provenance, including whether a candidate came from a rule or a model proposal.
- The system treats all candidate sources as untrusted until validation and preflight complete.
- The system rejects unsafe, unsupported, or shape-changing candidates before benchmarking.
- The system validates candidate results empirically on controlled parameter sets.
- The system benchmarks validated candidates against the baseline with paired measurements.
- The system promotes a candidate only after repeated paired evidence satisfies configured statistical and practical thresholds.
- The system runs held-out measurements only after promotion.
- The system records positive, null, negative, no-candidate, and rejected outcomes for workload cases and candidate attempts.

## Workflow Requirements

For each workload case, the system must follow the same evidence-producing path: load the baseline SQL and parameters, collect schema context, request candidates, validate candidates, preflight executable candidates, benchmark surviving candidates against the baseline, update candidate-selection state, evaluate promotion criteria, run held-out measurement when promotion occurs, and export the resulting records. A case that produces no candidate, no validated candidate, no promotion, or a slower candidate is not a harness failure when the run completes and records the reason.

## Safety Requirements

- The system is limited to read-only `SELECT` queries in a restricted safe fragment.
- The system rejects candidates that change the output column count or output labels.
- The system preserves parameter mapping and rejects candidates that remove, add, or remap parameters unsafely.
- The system preserves duplicate-sensitive semantics and must not treat set semantics as sufficient for bag-sensitive SQL results.
- The system preserves outer `ORDER BY` behavior when ordered output is part of the observed query contract.
- The system handles NULL-sensitive semantics conservatively, especially for predicates, joins, aggregates, `IN`, `NOT IN`, `EXISTS`, and `NOT EXISTS`.
- The system prevents unsafe statements, side effects, unsupported SQL shapes, and validation failures from reaching the benchmarking or promotion stages.

## Measurement Requirements

- Benchmarking uses paired baseline and candidate executions so each candidate is compared under similar local conditions.
- Promotion depends on repeated measurements, a practical improvement threshold, statistical evidence, and stability checks rather than a single faster execution.
- Search measurements and held-out measurements remain separate so the same observations are not used both to choose and to report a promoted candidate.
- The system records enough timing, ordering, phase, run-pair, and workload-case data to distinguish search behavior from final held-out behavior.

## Evidence Requirements

Each run records a manifest, candidate records, equivalence results, benchmark rows, promotion decisions, workload-case outcomes, monitoring extracts, raw run-data archives, checksums, and interpreted summaries. The record must distinguish positive improvements, null results, negative results, no-candidate cases, and rejected candidates so that unsuccessful or unsupported attempts remain part of the experimental result rather than being filtered away.

## Maintainability Requirements

The implementation separates candidate generation, validation, benchmarking, promotion, metadata persistence, and orchestration into reviewable modules with clear responsibilities. The design keeps language-model proposal logic separate from semantic validation and keeps benchmark decision logic separate from candidate generation. This separation is required so a future change to prompts, models, rules, datasets, or promotion thresholds does not silently weaken semantic safeguards.
