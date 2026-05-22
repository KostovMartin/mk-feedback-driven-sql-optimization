from __future__ import annotations

import argparse
import json
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QUERY_DIR = "queries"
PARAMETER_DIR = "parameters"
BASELINE_MANIFEST = "job-imdb-baseline-corpus.json"
RULE_MANIFEST = "job-imdb-rule-corpus.json"
LLM_MANIFEST = "job-imdb-local-llm-corpus.json"
MIXED_MANIFEST = "job-imdb-mixed-corpus.json"
METADATA_FILE = "JOB_IMDB_METADATA.json"
MIXED_CANDIDATE_SELECTOR = "candidate-pool"
DEFAULT_SEARCH_PAIR_COUNT = 30
DEFAULT_HELD_OUT_PAIR_COUNT = 30

_QUERY_NAME_RE = re.compile(r"^(?P<family>\d+)(?P<variant>[a-z])\.sql$", re.IGNORECASE)


@dataclass(frozen=True)
class JobQuerySpec:
    source_path: Path
    family: int
    variant: str

    @property
    def file_name(self) -> str:
        return self.source_path.name.lower()

    @property
    def query_id(self) -> str:
        return f"{self.family}{self.variant.lower()}"

    @property
    def label(self) -> str:
        return f"job_imdb_{self.query_id}_fixed_literal"


def write_job_imdb_workload(
    query_source: Path,
    output_root: Path,
    *,
    search_pair_count: int = DEFAULT_SEARCH_PAIR_COUNT,
    held_out_pair_count: int = DEFAULT_HELD_OUT_PAIR_COUNT,
    query_ids: Iterable[str] | None = None,
) -> None:
    specs = _discover_query_specs(query_source)
    query_filter = _normalize_query_ids(query_ids or [])
    if query_filter:
        specs = _filter_query_specs(specs, query_filter)
    if not specs:
        raise ValueError(f"No JOB query files found in {query_source}. Expected files like 1a.sql.")

    query_dir = output_root / QUERY_DIR
    parameter_dir = output_root / PARAMETER_DIR
    query_dir.mkdir(parents=True, exist_ok=True)
    parameter_dir.mkdir(parents=True, exist_ok=True)

    baseline_manifest = []
    rule_manifest = []
    llm_manifest = []
    mixed_manifest = []

    for spec in specs:
        query_text = spec.source_path.read_text(encoding="utf-8")
        (query_dir / spec.file_name).write_text(query_text.strip() + "\n", encoding="utf-8", newline="\n")

        search_file = f"{spec.query_id}_search_params.json"
        held_out_file = f"{spec.query_id}_held_out_params.json"
        _write_json(
            parameter_dir / search_file,
            _empty_parameter_sets(spec.query_id, "search", search_pair_count),
        )
        _write_json(
            parameter_dir / held_out_file,
            _empty_parameter_sets(spec.query_id, "held-out", held_out_pair_count),
        )

        baseline_manifest.append(_manifest_entry(spec, search_file, held_out_file, "baseline-only"))
        rule_manifest.append(_manifest_entry(spec, search_file, held_out_file, "first-rule-candidate"))
        llm_manifest.append(_manifest_entry(spec, search_file, held_out_file, "first-llm-candidate"))
        mixed_manifest.append(_manifest_entry(spec, search_file, held_out_file, MIXED_CANDIDATE_SELECTOR))

    _write_json(output_root / BASELINE_MANIFEST, baseline_manifest)
    _write_json(output_root / RULE_MANIFEST, rule_manifest)
    _write_json(output_root / LLM_MANIFEST, llm_manifest)
    _write_json(output_root / MIXED_MANIFEST, mixed_manifest)
    _write_metadata(output_root, specs, search_pair_count, held_out_pair_count, query_filter)


def job_imdb_artifact_paths(output_root: Path) -> list[Path]:
    files: list[Path] = []
    files.extend(sorted((output_root / QUERY_DIR).glob("*.sql")))
    files.extend(sorted((output_root / PARAMETER_DIR).glob("*.json")))
    files.extend(
        [
            output_root / BASELINE_MANIFEST,
            output_root / RULE_MANIFEST,
            output_root / LLM_MANIFEST,
            output_root / MIXED_MANIFEST,
            output_root / METADATA_FILE,
        ]
    )
    return files


def _discover_query_specs(query_source: Path) -> list[JobQuerySpec]:
    specs: list[JobQuerySpec] = []
    for path in query_source.glob("*.sql"):
        match = _QUERY_NAME_RE.match(path.name)
        if not match:
            continue
        specs.append(
            JobQuerySpec(
                source_path=path,
                family=int(match.group("family")),
                variant=match.group("variant").lower(),
            )
        )
    return sorted(specs, key=lambda spec: (spec.family, spec.variant))


def _normalize_query_ids(query_ids: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for query_id in query_ids:
        value = query_id.strip().lower()
        if not value:
            continue
        if not re.match(r"^\d+[a-z]$", value):
            raise ValueError(f"Invalid JOB query id '{query_id}'. Expected values like 1a or 33c.")
        normalized.append(value)
    return normalized


def _filter_query_specs(specs: list[JobQuerySpec], query_ids: list[str]) -> list[JobQuerySpec]:
    requested = set(query_ids)
    available = {spec.query_id for spec in specs}
    missing = sorted(requested - available)
    if missing:
        raise ValueError(f"Missing requested JOB query files: {', '.join(missing)}")
    return [spec for spec in specs if spec.query_id in requested]


def _empty_parameter_sets(query_id: str, phase: str, count: int) -> list[dict[str, Any]]:
    return [
        {
            "parameter_set_id": f"job-{query_id}-{phase}-{index:03}",
            "parameters": [],
        }
        for index in range(1, count + 1)
    ]


def _manifest_entry(
    spec: JobQuerySpec,
    search_file: str,
    held_out_file: str,
    candidate_selector: str,
) -> dict[str, Any]:
    return {
        "query_file": spec.file_name,
        "parameter_file": search_file,
        "held_out_parameter_file": held_out_file,
        "expected_candidate_source_detail": candidate_selector,
        "workload_label": spec.label,
        "workload_description": (
            f"JOB/IMDB fixed-literal query {spec.query_id}; repeated baseline/candidate "
            "pairs are used because the official Join Order Benchmark does not provide "
            "parameterized variants."
        ),
    }


def _write_metadata(
    output_root: Path,
    specs: list[JobQuerySpec],
    search_pair_count: int,
    held_out_pair_count: int,
    query_filter: list[str],
) -> None:
    metadata = {
        "corpus": "job-imdb",
        "target_engine": "postgresql",
        "query_count": len(specs),
        "query_filter": query_filter,
        "query_source": "official Join Order Benchmark SQL files, provided locally",
        "parameter_policy": (
            "JOB queries are fixed-literal SQL files. The generated parameter files contain "
            "empty parameter lists so the existing manifest runner can perform repeated "
            "paired search and held-out measurements."
        ),
        "search_pairs_per_query": search_pair_count,
        "held_out_pairs_per_query": held_out_pair_count,
        "manifests": {
            BASELINE_MANIFEST: "baseline-only calibration for JOB fixed-literal queries",
            RULE_MANIFEST: "rule-candidate search for JOB fixed-literal queries",
            LLM_MANIFEST: "local LLM candidate search for JOB fixed-literal queries",
            MIXED_MANIFEST: (
                "mixed candidate-pool search for JOB fixed-literal queries"
            ),
        },
    }
    _write_json(output_root / METADATA_FILE, metadata)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def _reset_output_root(path: Path) -> None:
    if path.exists():
        resolved = path.resolve()
        if len(resolved.parts) < 3:
            raise ValueError(f"Refusing to remove shallow output path: {resolved}")
        shutil.rmtree(resolved)
    path.mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare JOB/IMDB workload manifests.")
    parser.add_argument("--query-source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--search-pairs", type=int, default=DEFAULT_SEARCH_PAIR_COUNT)
    parser.add_argument("--held-out-pairs", type=int, default=DEFAULT_HELD_OUT_PAIR_COUNT)
    parser.add_argument("--query-id", action="append", default=[])
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    if args.clean:
        _reset_output_root(args.output_root)

    write_job_imdb_workload(
        args.query_source,
        args.output_root,
        search_pair_count=args.search_pairs,
        held_out_pair_count=args.held_out_pairs,
        query_ids=args.query_id,
    )
    print(f"Prepared JOB/IMDB workload artifacts in {args.output_root}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
