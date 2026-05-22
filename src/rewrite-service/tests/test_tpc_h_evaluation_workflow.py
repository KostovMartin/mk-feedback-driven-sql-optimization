from __future__ import annotations

import csv
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

from app.parser.sql_parser import parse_and_analyze
from app.rules.engine import apply_rule_candidates

REPO_ROOT = Path(__file__).resolve().parents[3]
PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def _load_tpch_workload_module() -> ModuleType:
    module_path = REPO_ROOT / "tpch-generator" / "tpch_parameterized_workload.py"
    spec = importlib.util.spec_from_file_location("tpch_parameterized_workload", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_real_world_workload_module() -> ModuleType:
    module_path = REPO_ROOT / "tpch-generator" / "real_world_workload.py"
    spec = importlib.util.spec_from_file_location("real_world_workload", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_job_imdb_workload_module() -> ModuleType:
    module_path = REPO_ROOT / "tpch-generator" / "job_imdb_workload.py"
    spec = importlib.util.spec_from_file_location("job_imdb_workload", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_csv(
    path: Path,
    rows: list[dict[str, object]],
    fieldnames: list[str] | None = None,
) -> None:
    if fieldnames is None:
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_workload_runner_source_files(*relative_paths: str) -> str:
    runner_root = REPO_ROOT / "src" / "QueryOptimizer.WorkloadRunner"
    return "\n".join(
        (runner_root / relative_path).read_text(encoding="utf-8")
        for relative_path in relative_paths
    )


def _placeholder_positions(sql: str) -> set[str]:
    return {f"${match}" for match in PLACEHOLDER_RE.findall(sql)}


@contextmanager
def _workspace_temp_dir() -> Iterator[Path]:
    base = REPO_ROOT / ".test-output" / "tpch-evaluation-workflow"
    path = base / uuid.uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        resolved_base = base.resolve()
        resolved_path = path.resolve()
        if resolved_path == resolved_base or resolved_base not in resolved_path.parents:
            raise AssertionError(f"Refusing to delete unexpected test path: {resolved_path}")
        shutil.rmtree(resolved_path, ignore_errors=True)


def test_parameterized_tpch_workload_has_reportable_split_artifacts() -> None:
    module = _load_tpch_workload_module()
    with _workspace_temp_dir() as temp_dir:
        scale_root = temp_dir / "sf1"
        module.write_parameterized_workload(scale_root, scale_factor="1", seed=20260506)

        query_dir = scale_root / module.QUERY_DIR
        parameter_dir = scale_root / module.PARAMETER_DIR
        query_files = sorted(query_dir.glob("q*.sql"))
        search_parameter_files = sorted(parameter_dir.glob("q*_search_params.json"))
        held_out_parameter_files = sorted(parameter_dir.glob("q*_held_out_params.json"))

        assert len(query_files) == 22
        assert len(search_parameter_files) == 22
        assert len(held_out_parameter_files) == 22

        for query_file in query_files:
            query_nr = query_file.stem[1:]
            placeholders = _placeholder_positions(query_file.read_text(encoding="utf-8"))
            search_sets = _read_json(parameter_dir / f"q{query_nr}_search_params.json")
            held_out_sets = _read_json(parameter_dir / f"q{query_nr}_held_out_params.json")

            assert isinstance(search_sets, list)
            assert isinstance(held_out_sets, list)
            assert len(search_sets) == module.SEARCH_PARAMETER_COUNT == 70
            assert len(held_out_sets) == module.HELD_OUT_PARAMETER_COUNT == 30

            search_ids = {item["parameter_set_id"] for item in search_sets}
            held_out_ids = {item["parameter_set_id"] for item in held_out_sets}
            assert search_ids.isdisjoint(held_out_ids)
            assert all("-search-" in parameter_set_id for parameter_set_id in search_ids)
            assert all("-held-out-" in parameter_set_id for parameter_set_id in held_out_ids)

            for parameter_set in [*search_sets, *held_out_sets]:
                positions = {parameter["position"] for parameter in parameter_set["parameters"]}
                assert positions == placeholders


def test_parameterized_tpch_manifests_select_expected_candidate_sources() -> None:
    module = _load_tpch_workload_module()
    with _workspace_temp_dir() as temp_dir:
        scale_root = temp_dir / "sf1"

        module.write_parameterized_workload(scale_root, scale_factor="1", seed=20260506)

        expected_selectors = {
            module.BASELINE_MANIFEST: "baseline-only",
            module.RULE_MANIFEST: "first-rule-candidate",
            module.LLM_MANIFEST: "first-llm-candidate",
            module.MIXED_MANIFEST: "candidate-pool",
        }

        for manifest_file, selector in expected_selectors.items():
            manifest = _read_json(scale_root / manifest_file)
            assert isinstance(manifest, list)
            assert len(manifest) == 22
            assert {item["expected_candidate_source_detail"] for item in manifest} == {selector}
            assert all(item["parameter_file"].endswith("_search_params.json") for item in manifest)
            assert all(
                item["held_out_parameter_file"].endswith("_held_out_params.json")
                for item in manifest
            )

        artifact_paths = module.parameterized_artifact_paths(scale_root)
        assert len(artifact_paths) == 70
        assert all(path.exists() for path in artifact_paths)


def test_parameterized_tpch_generation_is_deterministic() -> None:
    module = _load_tpch_workload_module()
    with _workspace_temp_dir() as temp_dir:
        first_root = temp_dir / "first"
        second_root = temp_dir / "second"

        module.write_parameterized_workload(first_root, scale_factor="1", seed=20260506)
        module.write_parameterized_workload(second_root, scale_factor="1", seed=20260506)

        first_artifacts = module.parameterized_artifact_paths(first_root)
        second_artifacts = module.parameterized_artifact_paths(second_root)

        for first_path, second_path in zip(first_artifacts, second_artifacts, strict=True):
            assert first_path.relative_to(first_root) == second_path.relative_to(second_root)
            assert first_path.read_bytes() == second_path.read_bytes()


def test_parameterized_tpch_mixed_manifest_uses_one_candidate_pool_entry_per_query() -> None:
    module = _load_tpch_workload_module()
    with _workspace_temp_dir() as temp_dir:
        scale_root = temp_dir / "sf1"
        module.write_parameterized_workload(scale_root, scale_factor="1", seed=20260506)

        mixed_manifest = _read_json(scale_root / module.MIXED_MANIFEST)

        assert isinstance(mixed_manifest, list)
        assert len(mixed_manifest) == 22
        assert {item["expected_candidate_source_detail"] for item in mixed_manifest} == {
            "candidate-pool"
        }
        assert all(
            item["parameter_file"].endswith("_search_params.json") for item in mixed_manifest
        )
        assert all(
            item["held_out_parameter_file"].endswith("_held_out_params.json")
            for item in mixed_manifest
        )


def test_real_world_workload_has_reportable_split_artifacts() -> None:
    module = _load_real_world_workload_module()
    with _workspace_temp_dir() as temp_dir:
        scale_root = temp_dir / "sf1"
        module.write_real_world_workload(scale_root, scale_factor="1", seed=20260512)

        query_dir = scale_root / module.QUERY_DIR
        parameter_dir = scale_root / module.PARAMETER_DIR
        query_files = sorted(query_dir.glob("rw_*.sql"))
        search_parameter_files = sorted(parameter_dir.glob("rw_*_search_params.json"))
        held_out_parameter_files = sorted(parameter_dir.glob("rw_*_held_out_params.json"))

        assert len(query_files) == 10
        assert len(search_parameter_files) == 10
        assert len(held_out_parameter_files) == 10

        for query_file in query_files:
            scenario_id = query_file.stem
            placeholders = _placeholder_positions(query_file.read_text(encoding="utf-8"))
            search_sets = _read_json(parameter_dir / f"{scenario_id}_search_params.json")
            held_out_sets = _read_json(parameter_dir / f"{scenario_id}_held_out_params.json")

            assert isinstance(search_sets, list)
            assert isinstance(held_out_sets, list)
            assert len(search_sets) == module.SEARCH_PARAMETER_COUNT == 70
            assert len(held_out_sets) == module.HELD_OUT_PARAMETER_COUNT == 30

            search_ids = {item["parameter_set_id"] for item in search_sets}
            held_out_ids = {item["parameter_set_id"] for item in held_out_sets}
            assert search_ids.isdisjoint(held_out_ids)
            assert all("-search-" in parameter_set_id for parameter_set_id in search_ids)
            assert all("-held-out-" in parameter_set_id for parameter_set_id in held_out_ids)

            for parameter_set in [*search_sets, *held_out_sets]:
                positions = {parameter["position"] for parameter in parameter_set["parameters"]}
                assert positions == placeholders


def test_real_world_manifests_select_expected_candidate_sources() -> None:
    module = _load_real_world_workload_module()
    with _workspace_temp_dir() as temp_dir:
        scale_root = temp_dir / "sf1"
        module.write_real_world_workload(scale_root, scale_factor="1", seed=20260512)

        expected_selectors = {
            module.BASELINE_MANIFEST: {"baseline-only"},
            module.RULE_MANIFEST: {"first-rule-candidate"},
            module.LLM_MANIFEST: {"first-llm-candidate"},
            module.MIXED_MANIFEST: {"candidate-pool"},
        }

        for manifest_file, selectors in expected_selectors.items():
            manifest = _read_json(scale_root / manifest_file)
            assert isinstance(manifest, list)
            assert len(manifest) == 10
            assert {item["expected_candidate_source_detail"] for item in manifest} == selectors
            assert all(item["parameter_file"].endswith("_search_params.json") for item in manifest)
            assert all(
                item["held_out_parameter_file"].endswith("_held_out_params.json")
                for item in manifest
            )

        artifact_paths = module.real_world_artifact_paths(scale_root)
        assert len(artifact_paths) == 35
        assert all(path.exists() for path in artifact_paths)


def test_real_world_generated_queries_parse_and_rule_supported_selectors_exist() -> None:
    module = _load_real_world_workload_module()
    with _workspace_temp_dir() as temp_dir:
        scale_root = temp_dir / "sf1"
        module.write_real_world_workload(scale_root, scale_factor="1", seed=20260512)

        query_dir = scale_root / module.QUERY_DIR
        for query_file in sorted(query_dir.glob("rw_*.sql")):
            analysis = parse_and_analyze(
                query_file.read_text(encoding="utf-8"),
                check_fragment=True,
            )
            assert analysis.parsed, query_file.name
            assert analysis.in_supported_fragment, query_file.name

        rw02_sql = (query_dir / "rw_02.sql").read_text(encoding="utf-8")
        rw03_sql = (query_dir / "rw_03.sql").read_text(encoding="utf-8")
        rw02_candidates, _ = apply_rule_candidates(
            rw02_sql,
            max_candidates=50,
            allowed_rule_families=["B"],
        )
        rw03_candidates, _ = apply_rule_candidates(
            rw03_sql,
            max_candidates=50,
            allowed_rule_families=["D"],
        )

        assert {candidate.source_detail for candidate in rw02_candidates} == {"rule:in_to_exists"}
        assert {candidate.source_detail for candidate in rw03_candidates} == {
            "rule:redundant_group_by_elimination"
        }


def test_real_world_generation_is_deterministic() -> None:
    module = _load_real_world_workload_module()
    with _workspace_temp_dir() as temp_dir:
        first_root = temp_dir / "first"
        second_root = temp_dir / "second"

        module.write_real_world_workload(first_root, scale_factor="1", seed=20260512)
        module.write_real_world_workload(second_root, scale_factor="1", seed=20260512)

        first_artifacts = module.real_world_artifact_paths(first_root)
        second_artifacts = module.real_world_artifact_paths(second_root)

        for first_path, second_path in zip(first_artifacts, second_artifacts, strict=True):
            assert first_path.relative_to(first_root) == second_path.relative_to(second_root)
            assert first_path.read_bytes() == second_path.read_bytes()


def test_job_imdb_workload_prepares_fixed_literal_manifests() -> None:
    module = _load_job_imdb_workload_module()
    with _workspace_temp_dir() as temp_dir:
        query_source = temp_dir / "job-source"
        output_root = temp_dir / "job-output"
        query_source.mkdir()
        (query_source / "10b.sql").write_text("SELECT COUNT(*) FROM title;\n", encoding="utf-8")
        (query_source / "1a.sql").write_text("SELECT id FROM title;\n", encoding="utf-8")
        (query_source / "notes.sql").write_text("SELECT 1;\n", encoding="utf-8")

        module.write_job_imdb_workload(
            query_source,
            output_root,
            search_pair_count=4,
            held_out_pair_count=3,
        )

        query_dir = output_root / module.QUERY_DIR
        parameter_dir = output_root / module.PARAMETER_DIR
        assert [path.name for path in sorted(query_dir.glob("*.sql"))] == ["10b.sql", "1a.sql"]

        baseline_manifest = _read_json(output_root / module.BASELINE_MANIFEST)
        rule_manifest = _read_json(output_root / module.RULE_MANIFEST)
        llm_manifest = _read_json(output_root / module.LLM_MANIFEST)
        mixed_manifest = _read_json(output_root / module.MIXED_MANIFEST)
        assert [item["query_file"] for item in baseline_manifest] == ["1a.sql", "10b.sql"]
        assert {item["expected_candidate_source_detail"] for item in baseline_manifest} == {
            "baseline-only"
        }
        assert {item["expected_candidate_source_detail"] for item in rule_manifest} == {
            "first-rule-candidate"
        }
        assert {item["expected_candidate_source_detail"] for item in llm_manifest} == {
            "first-llm-candidate"
        }
        assert [item["query_file"] for item in mixed_manifest] == [
            "1a.sql",
            "10b.sql",
        ]
        assert {item["expected_candidate_source_detail"] for item in mixed_manifest} == {
            "candidate-pool",
        }

        search_sets = _read_json(parameter_dir / "1a_search_params.json")
        held_out_sets = _read_json(parameter_dir / "1a_held_out_params.json")
        assert len(search_sets) == 4
        assert len(held_out_sets) == 3
        assert all(item["parameters"] == [] for item in [*search_sets, *held_out_sets])
        assert search_sets[0]["parameter_set_id"] == "job-1a-search-001"
        assert held_out_sets[0]["parameter_set_id"] == "job-1a-held-out-001"

        metadata = _read_json(output_root / module.METADATA_FILE)
        assert metadata["corpus"] == "job-imdb"
        assert metadata["query_count"] == 2

        artifact_paths = module.job_imdb_artifact_paths(output_root)
        assert len(artifact_paths) == 11
        assert all(path.exists() for path in artifact_paths)


def test_job_imdb_generation_is_deterministic() -> None:
    module = _load_job_imdb_workload_module()
    with _workspace_temp_dir() as temp_dir:
        query_source = temp_dir / "job-source"
        first_root = temp_dir / "first"
        second_root = temp_dir / "second"
        query_source.mkdir()
        (query_source / "2a.sql").write_text(
            "SELECT id FROM title WHERE production_year > 2000;\n",
            encoding="utf-8",
        )
        (query_source / "1b.sql").write_text("SELECT id FROM name;\n", encoding="utf-8")

        module.write_job_imdb_workload(
            query_source,
            first_root,
            search_pair_count=2,
            held_out_pair_count=2,
        )
        module.write_job_imdb_workload(
            query_source,
            second_root,
            search_pair_count=2,
            held_out_pair_count=2,
        )

        first_artifacts = module.job_imdb_artifact_paths(first_root)
        second_artifacts = module.job_imdb_artifact_paths(second_root)

        for first_path, second_path in zip(first_artifacts, second_artifacts, strict=True):
            assert first_path.relative_to(first_root) == second_path.relative_to(second_root)
            assert first_path.read_bytes() == second_path.read_bytes()


def test_job_imdb_workload_can_filter_query_ids_for_dry_runs() -> None:
    module = _load_job_imdb_workload_module()
    with _workspace_temp_dir() as temp_dir:
        query_source = temp_dir / "job-source"
        output_root = temp_dir / "job-output"
        query_source.mkdir()
        (query_source / "1a.sql").write_text("SELECT id FROM title;\n", encoding="utf-8")
        (query_source / "10a.sql").write_text("SELECT COUNT(*) FROM title;\n", encoding="utf-8")
        (query_source / "10b.sql").write_text("SELECT MIN(id) FROM title;\n", encoding="utf-8")

        module.write_job_imdb_workload(query_source, output_root, query_ids=["10b"])

        manifest = _read_json(output_root / module.RULE_MANIFEST)
        assert isinstance(manifest, list)
        assert [item["query_file"] for item in manifest] == ["10b.sql"]
        metadata = _read_json(output_root / module.METADATA_FILE)
        assert metadata["query_count"] == 1
        assert metadata["query_filter"] == ["10b"]


def test_job_imdb_compose_overlay_mounts_data_and_generated_workload() -> None:
    compose = (REPO_ROOT / "docker-compose.job-imdb.yml").read_text(encoding="utf-8")

    assert 'shm_size: "${JOB_IMDB_TARGET_DB_SHM_SIZE:-4gb}"' in compose
    assert "BENCHMARK: job-imdb" in compose
    assert "./data/job-imdb:/workspace/data/job-imdb:ro" in compose
    assert 'JOB_IMDB_SKIP_TARGET_LOAD: "${JOB_IMDB_SKIP_TARGET_LOAD:-false}"' in compose
    assert (
        "${JOB_IMDB_WORKLOAD_HOST_PATH:-./benchmark-data/job-imdb/workload}:/app/job-imdb:ro"
        in compose
    )
    assert 'QUERIES_PATH: "/app/job-imdb/queries"' in compose
    assert 'PARAMETERS_PATH: "/app/job-imdb/parameters"' in compose
    assert (
        'WORKLOAD_MANIFEST_FILE: "${JOB_IMDB_WORKLOAD_MANIFEST_FILE:-/app/job-imdb/'
        'job-imdb-rule-corpus.json}"'
        in compose
    )
    assert 'BANDIT_STRATEGY: "${JOB_IMDB_BANDIT_STRATEGY:-thompson}"' in compose
    assert 'BANDIT_RANDOM_SEED: "${JOB_IMDB_BANDIT_RANDOM_SEED:-12345}"' in compose
    assert 'BACKGROUND_OPTIMIZER_ROUNDS: "${JOB_IMDB_BACKGROUND_OPTIMIZER_ROUNDS:-1}"' in compose
    assert (
        'BACKGROUND_OPTIMIZER_PARAMETER_LIMIT: '
        '"${JOB_IMDB_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT:-30}"'
    ) in compose


def test_job_imdb_data_loader_supports_official_csv_import() -> None:
    loader = (REPO_ROOT / "data-loader" / "load-smoke.sh").read_text(encoding="utf-8")

    assert "job-imdb)" in loader
    assert "JOB_IMDB_DATA_DIR" in loader
    assert "JOB_IMDB_SKIP_TARGET_LOAD" in loader
    assert "aka_name aka_title cast_info char_name comp_cast_type company_name" in loader
    assert (
        'psql "$TARGET_CONNECTION" -v ON_ERROR_STOP=1 -f "$JOB_IMDB_DATA_DIR/schema.sql"'
        in loader
    )
    assert r"\copy ${table_name} FROM '${csv_path}' WITH (FORMAT csv, ESCAPE '\')" in loader
    assert "fkindexes.sql" in loader
    assert "ANALYZE" in loader


def test_workload_runner_dockerfile_restores_runtime_project_only() -> None:
    dockerfile = (
        REPO_ROOT / "src" / "QueryOptimizer.WorkloadRunner" / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert (
        "RUN dotnet restore src/QueryOptimizer.WorkloadRunner/"
        "QueryOptimizer.WorkloadRunner.csproj"
    ) in dockerfile
    assert "RUN dotnet restore QueryOptimizer.sln" not in dockerfile


def test_job_imdb_evaluation_runner_can_reuse_seeded_target_database() -> None:
    script = (REPO_ROOT / "scripts" / "lib" / "run-job-imdb-evaluation.ps1").read_text(
        encoding="utf-8"
    )

    assert "[switch] $ReuseTargetDatabase" in script
    assert "[switch] $CleanTargetDatabase" in script
    assert "$reuseTargetDatabaseEffective = -not $CleanTargetDatabase" in script
    assert "function Reset-ComposeTargetDataVolume" in script
    assert "function Reset-ComposeMetadataDataVolume" in script
    assert "function Get-JobImdbSeededTableCount" in script
    assert "JOB_IMDB_REQUIRED_TABLES" in script
    assert 'if (-not $reuseTargetDatabaseEffective) {' in script
    assert '$env:JOB_IMDB_SKIP_TARGET_LOAD = "true"' in script
    assert '$env:JOB_IMDB_SKIP_TARGET_LOAD = "false"' in script
    assert "Reset-ComposeMetadataDataVolume" in script


def test_job_imdb_evaluation_runner_defaults_to_mixed_reuse_for_local_tests() -> None:
    script = (REPO_ROOT / "scripts" / "lib" / "run-job-imdb-evaluation.ps1").read_text(
        encoding="utf-8"
    )

    assert '[string] $CandidateSource = "mixed"' in script
    assert "target_database_reuse = $reuseTargetDatabaseEffective" in script
    assert "target_database_clean_requested = [bool]$CleanTargetDatabase" in script


def test_job_imdb_evaluation_runner_exports_appendix_provenance_snapshot() -> None:
    script = (REPO_ROOT / "scripts" / "lib" / "run-job-imdb-evaluation.ps1").read_text(
        encoding="utf-8"
    )

    assert "function Get-GitMetadata" in script
    assert "git_commit = $gitMetadata.Commit" in script
    assert "git_branch = $gitMetadata.Branch" in script
    assert "git_worktree_dirty = $gitMetadata.IsDirty" in script
    assert "git_status_short = $gitMetadata.StatusShort" in script
    assert 'workload_snapshot_path = "workload-snapshot"' in script
    assert "Copy-Item -LiteralPath $resolvedWorkloadRoot" in script


def test_job_imdb_evaluation_runner_uses_generated_fixed_literal_corpus() -> None:
    script = (REPO_ROOT / "scripts" / "lib" / "run-job-imdb-evaluation.ps1").read_text(
        encoding="utf-8"
    )

    assert '[ValidateSet("baseline", "rule", "llm", "mixed")]' in script
    assert "prepare-job-imdb-workload.ps1" in script
    assert "docker-compose.job-imdb.yml" in script
    assert "[string[]] $QueryIds" in script
    assert "QueryIds = $QueryIds" in script
    assert "JOB_IMDB_BANDIT_STRATEGY" in script
    assert "JOB_IMDB_BACKGROUND_OPTIMIZER_ROUNDS" in script
    assert 'JOB_IMDB_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT)) { "30" }' in script
    assert 'JOB_IMDB_VALIDATION_PARAMETER_SET_LIMIT)) { "1" }' in script
    assert "bandit_strategy = $env:JOB_IMDB_BANDIT_STRATEGY" in script
    assert "job-imdb-baseline-corpus.json" in script
    assert "job-imdb-rule-corpus.json" in script
    assert "job-imdb-local-llm-corpus.json" in script
    assert "job-imdb-mixed-corpus.json" in script
    assert 'JOB_IMDB_RUN_HELD_OUT_EVALUATION = if ($CandidateSource -eq "baseline")' in script
    assert "fixed_literal_repeated_measurement" in script


def test_job_imdb_resource_preparer_downloads_official_inputs() -> None:
    script_path = REPO_ROOT / "scripts" / "lib" / "prepare-job-imdb-resources.ps1"
    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")

    job_repo_zip_url = (
        "https://github.com/gregrahn/join-order-benchmark/archive/refs/heads/master.zip"
    )
    assert job_repo_zip_url in script
    assert "https://event.cwi.nl/da/job/imdb.tgz" in script
    assert "https://bonsai.cedardb.com/job/imdb.tgz" in script
    assert 'queries\\job-imdb' in script
    assert 'data\\job-imdb' in script
    assert "[switch] $SkipData" in script
    assert "Invoke-WebRequest" in script
    assert "tar" in script
    assert "aka_name aka_title cast_info char_name comp_cast_type company_name" in script
    assert "schema.sql" in script
    assert "fkindexes.sql" in script


def test_job_imdb_workload_preparer_accepts_absolute_paths() -> None:
    script = (REPO_ROOT / "scripts" / "lib" / "prepare-job-imdb-workload.ps1").read_text(
        encoding="utf-8"
    )

    assert "function Resolve-RepoPath" in script
    assert 'Join-Path $PSScriptRoot "..\\.."' in script
    assert "[System.IO.Path]::IsPathRooted($Path)" in script
    assert "Resolve-RepoPath -Path $QuerySourcePath" in script
    assert "Resolve-RepoPath -Path $OutputRoot" in script
    assert "[string[]] $QueryIds" in script
    assert "--query-id" in script


def test_gitignore_keeps_job_imdb_external_artifacts_local() -> None:
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "queries/job-imdb/" in ignore
    assert "data/*" in ignore
    assert "benchmark-data/" in ignore


def test_tpch_evaluation_runner_uses_reportable_defaults() -> None:
    script = (REPO_ROOT / "scripts" / "lib" / "run-tpch-evaluation.ps1").read_text(
        encoding="utf-8"
    )

    assert '[ValidateSet("baseline", "rule", "llm", "mixed")]' in script
    assert '[string] $CandidateSource = "mixed"' in script
    assert "experiment-runs\\tpch-evaluation" in script
    assert "run_id = $RunId" in script
    assert '"/app/tpch/queries-parameterized"' in script
    assert '"/app/tpch/parameters-parameterized"' in script
    assert "tpch-parameterized-rule-corpus.json" in script
    assert "tpch-parameterized-local-llm-corpus.json" in script
    assert "tpch-parameterized-mixed-corpus.json" in script
    assert 'TPCH_RUN_HELD_OUT_EVALUATION = if ($CandidateSource -eq "baseline")' in script
    assert '$CandidateSource -in @("llm", "mixed")' in script
    assert 'TPCH_MIN_PROMOTION_PAIRS)) { "30" }' in script
    assert 'TPCH_VALIDATION_PARAMETER_SET_LIMIT)) { "3" }' in script
    assert 'TPCH_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT)) { "30" }' in script
    assert (
        "background_optimizer_parameter_limit = "
        "$env:TPCH_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT"
    ) in script
    assert 'TPCH_CAPTURE_EXPLAIN_PLANS)) { "true" }' in script
    assert "down -v" not in script
    assert "${projectName}_target-db-data" in script
    assert "${projectName}_metadata-db-data" in script
    assert "ollama-models" not in script


def test_real_world_evaluation_runner_uses_generated_real_world_corpus() -> None:
    script = (REPO_ROOT / "scripts" / "lib" / "run-real-world-evaluation.ps1").read_text(
        encoding="utf-8"
    )
    compose = (REPO_ROOT / "docker-compose.tpch.yml").read_text(encoding="utf-8")

    assert "experiment-runs\\real-world-evaluation" in script
    assert '"/app/tpch/real-world/queries"' in script
    assert '"/app/tpch/real-world/parameters"' in script
    assert "real-world-baseline-corpus.json" in script
    assert "real-world-rule-corpus.json" in script
    assert "real-world-local-llm-corpus.json" in script
    assert "real-world-mixed-corpus.json" in script
    assert 'TPCH_RUN_HELD_OUT_EVALUATION = if ($CandidateSource -eq "baseline")' in script
    assert 'TPCH_EQUIVALENCE_MAX_ROWS_FULL_COMPARE)) { "1000000" }' in script
    assert 'TPCH_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT)) { "30" }' in script
    assert '"${TPCH_OPTIMIZER_PROFILE:-tpch}"' in compose
    assert '"${TPCH_EQUIVALENCE_MAX_ROWS_FULL_COMPARE:-100000}"' in compose
    assert (
        'BACKGROUND_OPTIMIZER_PARAMETER_LIMIT: '
        '"${TPCH_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT:-30}"'
    ) in compose


def test_root_public_powershell_surface_is_simplified() -> None:
    root_scripts = sorted(path.name for path in (REPO_ROOT / "scripts").glob("*.ps1"))

    assert root_scripts == ["run-fast-check.ps1", "run-main-run.ps1"]


def test_fast_check_syntax_checks_public_surface_and_private_helpers() -> None:
    script = (REPO_ROOT / "scripts" / "run-fast-check.ps1").read_text(encoding="utf-8")

    expected_paths = {
        "scripts\\run-fast-check.ps1",
        "scripts\\run-main-run.ps1",
        "scripts\\lib\\ExperimentRun.psm1",
        "scripts\\lib\\analyze-results.ps1",
        "scripts\\lib\\export-monitoring-window.ps1",
        "scripts\\lib\\monitoring-functions.ps1",
        "scripts\\lib\\prepare-job-imdb-resources.ps1",
        "scripts\\lib\\prepare-job-imdb-workload.ps1",
        "scripts\\lib\\run-job-imdb-evaluation.ps1",
        "scripts\\lib\\run-real-world-evaluation.ps1",
        "scripts\\lib\\run-tpch-evaluation.ps1",
    }

    for expected_path in expected_paths:
        assert expected_path in script

    assert "src\\rewrite-service\\tests" in script
    assert "Skipping rewrite-service pytest" in script
    assert (
        "dotnet restore src\\QueryOptimizer.WorkloadRunner\\QueryOptimizer.WorkloadRunner.csproj"
        in script
    )
    assert "dotnet restore QueryOptimizer.sln" not in script
    assert (
        "tests\\QueryOptimizer.WorkloadRunner.Tests\\QueryOptimizer.WorkloadRunner.Tests.csproj"
        in script
    )
    assert "Skipping workload-runner tests" in script
    assert "scripts\\run-smoke.ps1" not in script
    assert "scripts\\run-controlled-pilot.ps1" not in script
    assert "scripts\\run-test-set.ps1" not in script
    assert "scripts\\test-monitoring-export.ps1" not in script


def test_main_run_module_invokes_private_corpus_helpers() -> None:
    module = (REPO_ROOT / "scripts" / "lib" / "ExperimentRun.psm1").read_text(
        encoding="utf-8"
    )

    assert 'Join-Path $repositoryRoot "scripts\\lib\\$ScriptName"' in module
    assert '-ScriptName "run-tpch-evaluation.ps1"' in module
    assert '-ScriptName "run-real-world-evaluation.ps1"' in module
    assert '-ScriptName "run-job-imdb-evaluation.ps1"' in module


def test_main_run_module_uses_named_splat_for_private_script_parameters() -> None:
    module = (REPO_ROOT / "scripts" / "lib" / "ExperimentRun.psm1").read_text(
        encoding="utf-8"
    )

    assert "[hashtable] $AdditionalParameters" in module
    assert "$scriptParameters = @{" in module
    assert 'CandidateSource = "mixed"' in module
    assert "& $scriptPath @scriptParameters" in module
    assert "& $scriptPath @arguments" not in module


def test_main_run_module_requests_gpu_monitoring_for_reportable_runs() -> None:
    module = (REPO_ROOT / "scripts" / "lib" / "ExperimentRun.psm1").read_text(
        encoding="utf-8"
    )

    assert "GpuMonitoring = $true" in module


def test_main_run_raw_run_data_bundle_uses_zip_and_checksum_without_readme() -> None:
    module = (REPO_ROOT / "scripts" / "lib" / "ExperimentRun.psm1").read_text(
        encoding="utf-8"
    )

    assert '$zipPath = Join-Path $artifactDirectory "$RunId-raw-run-data.zip"' in module
    assert '$checksumPath = Join-Path $artifactDirectory "$RunId-SHA256SUMS.txt"' in module
    assert "-evidence.zip" not in module
    assert '$readmePath = Join-Path $artifactDirectory "$RunId-README.md"' not in module
    assert "-README.md" not in module
    assert "Interpreted result reference" not in module


def test_workload_runner_preflights_typed_candidate_execution_before_benchmarking() -> None:
    runner_sources = _read_workload_runner_source_files(
        "Workloads/ControlledWorkloadRunner.cs",
        "Workloads/CandidateFlow.cs",
        "Benchmarking/BenchmarkExecutor.cs",
    )

    assert 'stage = "candidate_preflight";' in runner_sources
    assert "PreflightCandidateSqlAsync" in runner_sources
    assert "EXPLAIN {explainTarget}" in runner_sources
    assert "typed_preflight_failed" in runner_sources
    assert "candidate_failed_typed_preflight" in runner_sources
    assert "MapParameterType" in runner_sources
    assert "NpgsqlDbType.Date" in runner_sources
    assert "DateOnly.Parse" in runner_sources


def test_workload_runner_treats_empty_candidate_pools_as_completed_outcomes() -> None:
    runner_sources = _read_workload_runner_source_files(
        "Workloads/ControlledWorkloadRunner.cs",
        "Workloads/CandidateFlow.cs",
    )

    assert 'private const string CandidatePoolSourceDetail = "candidate-pool";' in runner_sources
    assert "return generation.Candidates.ToArray();" in runner_sources
    assert 'outcome: "no_candidate"' in runner_sources
    assert '"no_validated_candidate"' in runner_sources
    assert 'status: "completed"' in runner_sources


def test_tpch_exporter_filters_every_run_scoped_table_by_run_id() -> None:
    script = (REPO_ROOT / "scripts" / "export-tpch-results.sh").read_text(encoding="utf-8")

    assert "sql_run_id=$(printf" in script
    assert "run_id_literal=\"'${sql_run_id}'\"" in script
    assert "SELECT *\nFROM invocations\nWHERE experiment_run_id = ${run_id_literal}" in script
    assert "SELECT *\nFROM benchmark_runs\nWHERE experiment_run_id = ${run_id_literal}" in script
    workload_case_filter = (
        "SELECT *\nFROM workload_case_results\nWHERE experiment_run_id = ${run_id_literal}"
    )
    assert workload_case_filter in script
    assert "JOIN run_invocations i ON i.id = c.invocation_id" in script
    assert "SELECT * FROM benchmark_runs ORDER BY" not in script


def test_posthoc_analysis_keeps_single_workload_case_export_as_array() -> None:
    script = (REPO_ROOT / "scripts" / "lib" / "analyze-results.ps1").read_text(
        encoding="utf-8"
    )

    assert "$workloadCaseRows = @(" in script
    assert "if (Test-Path -LiteralPath $workloadCasePath)" in script
    assert "@(Import-Csv -LiteralPath $workloadCasePath)" not in script


def test_posthoc_analysis_markdown_covers_full_run_evidence() -> None:
    with _workspace_temp_dir() as export_dir:
        run_id = "sample-run"
        invocation_id = str(uuid.uuid4())
        candidate_id = str(uuid.uuid4())
        rejected_candidate_id = str(uuid.uuid4())
        template_fingerprint = "template-promoted"
        empty_template_fingerprint = "template-empty"

        (export_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "candidate_source": "mixed",
                    "model": "qwen-test-model",
                    "scale_factor": "1",
                    "promotion_alpha": "0.05",
                    "promotion_min_improvement_pct": "2.0",
                    "monitoring_enabled": False,
                }
            ),
            encoding="utf-8",
        )

        _write_csv(
            export_dir / "query_templates.csv",
            [
                {
                    "template_fingerprint": template_fingerprint,
                    "normalized_sql": "select * from lineitem",
                },
                {
                    "template_fingerprint": empty_template_fingerprint,
                    "normalized_sql": "select * from orders",
                },
            ],
        )
        _write_csv(
            export_dir / "invocations.csv",
            [
                {
                    "id": invocation_id,
                    "experiment_run_id": run_id,
                    "template_fingerprint": template_fingerprint,
                }
            ],
        )
        _write_csv(
            export_dir / "candidates.csv",
            [
                {
                    "id": candidate_id,
                    "template_fingerprint": template_fingerprint,
                    "sql_text": "select * from lineitem where l_quantity > 10",
                    "source_type": "rule",
                    "source_detail": "rule_a",
                    "safety_status": "safe",
                    "semantic_status": "validated",
                    "promotion_status": "promoted",
                    "rejection_reason": "",
                },
                {
                    "id": rejected_candidate_id,
                    "template_fingerprint": template_fingerprint,
                    "sql_text": "select * from lineitem where l_quantity >= 10",
                    "source_type": "llm",
                    "source_detail": "qwen-test-model",
                    "safety_status": "safe",
                    "semantic_status": "failed",
                    "promotion_status": "pool",
                    "rejection_reason": "semantic_mismatch",
                },
            ],
        )
        _write_csv(
            export_dir / "equivalence_checks.csv",
            [
                {
                    "candidate_id": candidate_id,
                    "check_type": "initial",
                    "passed": "t",
                    "method": "full_comparison",
                    "original_row_count": 100,
                    "candidate_row_count": 100,
                    "rows_compared": 100,
                    "mismatch_detail": "",
                    "execution_time_ms": 25,
                },
                {
                    "candidate_id": rejected_candidate_id,
                    "check_type": "initial",
                    "passed": "f",
                    "method": "full_comparison",
                    "original_row_count": 100,
                    "candidate_row_count": 99,
                    "rows_compared": 100,
                    "mismatch_detail": '{"reason":"row_count"}',
                    "execution_time_ms": 20,
                },
            ],
        )

        def benchmark_row(
            *,
            phase: str,
            pair_id: str,
            candidate: bool,
            median_ms: int,
            parameter_set_id: str,
        ) -> dict[str, object]:
            return {
                "experiment_run_id": run_id,
                "candidate_id": candidate_id if candidate else "",
                "template_fingerprint": template_fingerprint,
                "benchmark_phase": phase,
                "run_pair_id": pair_id,
                "parameter_set_id": parameter_set_id,
                "execution_order": 2 if candidate else 1,
                "median_execution_time": median_ms,
                "rows_returned": 10,
                "plan_json": "{}",
                "plan_analysis": "{}",
                "reproducibility_metadata": (
                    '{"workload_label":"Q1","query_file":"q1.sql"}'
                ),
                "is_baseline": "f" if candidate else "t",
                "error_message": "",
                "timed_out": "f",
            }

        _write_csv(
            export_dir / "benchmark_runs.csv",
            [
                benchmark_row(
                    phase="search",
                    pair_id="11111111-1111-1111-1111-111111111111",
                    candidate=False,
                    median_ms=100,
                    parameter_set_id="search-1",
                ),
                benchmark_row(
                    phase="search",
                    pair_id="11111111-1111-1111-1111-111111111111",
                    candidate=True,
                    median_ms=92,
                    parameter_set_id="search-1",
                ),
                benchmark_row(
                    phase="held_out",
                    pair_id="22222222-2222-2222-2222-222222222222",
                    candidate=False,
                    median_ms=120,
                    parameter_set_id="held-1",
                ),
                benchmark_row(
                    phase="held_out",
                    pair_id="22222222-2222-2222-2222-222222222222",
                    candidate=True,
                    median_ms=110,
                    parameter_set_id="held-1",
                ),
            ],
        )
        _write_csv(
            export_dir / "decisions.csv",
            [
                {
                    "candidate_id": candidate_id,
                    "decision_type": "promote",
                    "reason": "sample promotion",
                }
            ],
        )
        _write_csv(export_dir / "bandit_state.csv", [{"candidate_id": candidate_id}])
        _write_csv(
            export_dir / "pool_summary.csv",
            [{"template_fingerprint": template_fingerprint}],
        )
        _write_csv(
            export_dir / "workload_case_results.csv",
            [
                {
                    "experiment_run_id": run_id,
                    "workload_label": "Q1",
                    "template_fingerprint": template_fingerprint,
                    "status": "completed",
                    "outcome": "promoted",
                    "failure_stage": "",
                    "failure_reason": "",
                    "candidates_generated": 4,
                    "candidates_returned": 2,
                    "candidates_rejected": 2,
                    "candidates_after_dedup": 2,
                    "candidates_after_safety": 1,
                    "benchmark_pairs": 30,
                    "held_out_benchmark_pairs": 30,
                },
                {
                    "experiment_run_id": run_id,
                    "workload_label": "Q2",
                    "template_fingerprint": empty_template_fingerprint,
                    "status": "completed",
                    "outcome": "no_candidate",
                    "failure_stage": "candidate_generation",
                    "failure_reason": "no_candidate",
                    "candidates_generated": 0,
                    "candidates_returned": 0,
                    "candidates_rejected": 0,
                    "candidates_after_dedup": 0,
                    "candidates_after_safety": 0,
                    "benchmark_pairs": 0,
                    "held_out_benchmark_pairs": 0,
                },
            ],
        )

        result = subprocess.run(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(REPO_ROOT / "scripts" / "lib" / "analyze-results.ps1"),
                "-InputDirectory",
                str(export_dir),
                "-BootstrapIterations",
                "10",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        report = (export_dir / "controlled_hypothesis_summary.md").read_text(
            encoding="utf-8-sig"
        )

        expected_sections = [
            "## Run Provenance",
            "## Workload Outcomes",
            "## Candidate Funnel",
            "## Candidate Source Breakdown",
            "## Search-Phase Evidence",
            "## Held-Out Evidence",
            "## Promoted Query Text",
            "## Equivalence Evidence",
            "## Negative and Null Outcomes",
            "## Monitoring Evidence",
        ]
        for section in expected_sections:
            assert section in report

        assert run_id in report
        assert "qwen-test-model" in report
        assert "completed / no_candidate" in report
        assert "rule_a" in report
        assert "Original SQL" in report
        assert "Promoted SQL" in report
        assert "select * from lineitem" in report
        assert "select * from lineitem where l_quantity > 10" in report
        assert "semantic_mismatch" in report
        assert "full_comparison" in report
        assert "not captured" in report
