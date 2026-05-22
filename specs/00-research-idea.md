# Research Idea: Feedback-Driven SQL Rewrite Search

## Problem

SQL optimizers are powerful, but query formulation can still affect execution time. Manual SQL rewriting can improve performance, but it requires expert knowledge, does not scale well, and can introduce semantic errors.

## Prior Work

Recent work has explored LLMs for SQL query rewriting. GenRewrite investigates LLM-based query rewriting with natural-language rewrite rules and counterexample-guided correction. LITHE studies LLM-assisted query rewrite advising with database-aware prompts, semantic checks, and performance safeguards. QUITE explores feedback-aware LLM-agent rewriting beyond fixed rewrite rules.

## Research Idea: Feedback-Driven SQL Rewrite Search

The central idea is to treat rewrite rules and language models as candidate generators, not semantic authorities. Candidate rewrites are useful only after structural safety checks, empirical result-set validation, paired benchmarking, and statistical promotion criteria.

The search process is therefore feedback-driven. Candidate generation proposes alternatives, validation filters unsafe or unsupported SQL, benchmarking measures observed behavior under controlled conditions, and promotion records candidates only when repeated paired evidence supports a practical improvement.

## Research Questions

- Can feedback-driven rewrite search identify semantically validated SQL rewrites that improve execution time under controlled PostgreSQL workloads?
- How often do deterministic rewrite rules and local language-model proposals produce useful, null, negative, rejected, or no-candidate outcomes?
- Does repeated empirical feedback provide enough evidence to promote candidates without treating generators as semantic authorities?

## Scope

This project targets read-only SQL queries in a restricted safe fragment. It uses controlled benchmark and workload files rather than transparent production query interception. The prototype and experiment protocol are bounded to reproducible local runs where generated candidates can be validated, measured, and recorded with provenance.

## Expected Contribution

The expected contribution is a reproducible prototype and experiment protocol for feedback-driven SQL rewrite search. The work demonstrates how deterministic rules and local language-model proposals can be used as candidate sources while preserving the central role of safety checks, empirical equivalence validation, paired benchmarking, and statistically cautious promotion.

## References

- GenRewrite: Query Rewriting via Large Language Models, https://arxiv.org/abs/2403.09060
- Query Rewriting via LLMs / LITHE, https://arxiv.org/abs/2502.12918
- QUITE: A Query Rewrite System Beyond Rules via LLM Agents, https://arxiv.org/abs/2506.07675
