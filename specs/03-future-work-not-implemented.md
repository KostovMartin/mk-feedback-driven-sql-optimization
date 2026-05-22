# Future Work Not Implemented

## Purpose

This document separates possible extensions from the implemented and evaluated system. The items below are not implemented or reported unless the repository contains both implementation support and recorded evidence for them.

## Hosted Model Comparison

Hosted or cloud model comparison is not part of the current implementation or reported evidence. The current model path is local Ollama candidate proposal, and hosted providers should not be treated as implemented without explicit code, configuration, and run evidence.

## Local Model Size and Iteration Trade-Offs

The current reported configuration uses one selected local model for candidate generation. A separate study could evaluate whether smaller local models, when given more sampling iterations or larger candidate budgets, can reach similar validated rewrite outcomes to a larger model.

This would separate model capacity from search budget. Useful measurements would include candidate yield, validation pass rate, unique candidate diversity, promoted-candidate rate, search cost, model latency, GPU memory use, GPU power or energy, and final held-out performance. Such a study should treat null and negative outcomes as evidence, because smaller models may produce more invalid or redundant candidates even when allowed more attempts.

No claim is made here that smaller models match the selected reference model; this remains a separate empirical question.

## Resource and Energy Analysis

The monitoring pipeline can export host, container, rewrite-service, and optional DCGM GPU metrics, including GPU power and total energy, when GPU monitoring is enabled. Future work is to make resource and energy analysis part of the main reported evaluation across all corpora, models, and hardware configurations, rather than treating it as optional run evidence.

## Iterative Specification-Guided Experimental Prototyping

This repository uses specifications as research scaffolding rather than as a single deterministic implementation source. The research idea, system requirements, implementation design, experiment protocol, executable scripts, run artifacts, and result interpretation are kept separate so the prototype can be audited against its stated design.

A separate methodological study could evaluate iterative specification-guided prototyping for reproducible research software, especially when implementation is assisted by prompts or coding agents. In such a workflow, the specification provides a stable target, while implementation may require multiple prompts, manual corrections, verification runs, and revisions before the executable prototype matches the intended method.

For reproducibility, readers are not expected to regenerate the implementation from prompts. The reproducible artifact is the committed codebase: specifications, source code, dependency declarations, scripts, run protocols, curated raw evidence when included, and result interpretation. This avoids making nondeterministic code generation part of the reproduction requirement.

The current repository uses this workflow pragmatically, but it does not claim to evaluate specification-guided prototyping as an independent research result.

## Richer Rewrite Strategies

More complex rewrite rules, multi-step rewrite planning, agentic rewrite search, and broader SQL fragment support are future work. Any added rewrite strategy must define safety preconditions, failure cases, validation behavior, and evidence requirements before it is reported as implemented.

## Production Deployment

The project is not a transparent production proxy or production optimizer. Production query interception, deployment hardening, and operational dashboards are outside the current scope.

## Scalable and Formal Validation Extensions

The current prototype uses empirical result-set comparison over the configured datasets and parameter sets, together with structural safety checks and typed execution preflight. This evidence is sufficient for the reported experimental runs, but it is not a database-wide proof of SQL equivalence.

Future versions could add scalable large-result comparison, such as deterministic result hashing or partitioned checksums, and formal or symbolic equivalence tools for supported SQL fragments. These extensions would broaden assurance and reduce validation cost; they are not part of the current implementation and are not required for interpreting the reported measurements.
