import tempfile
from pathlib import Path

import pytest

from nao_core.dbt_indexer import (
    ModelInfo,
    SourceInfo,
    find_dbt_projects,
    generate_manifest_md,
    generate_sources_md,
    index_dbt_project,
    parse_sql_config,
    parse_sql_dependencies,
    parse_yaml_descriptions,
    parse_yaml_sources,
    read_project_config,
)


# ---------------------------------------------------------------------------
# SQL parsing
# ---------------------------------------------------------------------------


class TestParseSqlDependencies:
    def test_basic_refs_and_sources(self):
        sql = """
        select * from {{ ref('stg_events') }}
        left join {{ source('core', 'dim_offers') }} on 1=1
        """
        refs, sources = parse_sql_dependencies(sql)
        assert refs == ["stg_events"]
        assert sources == [("core", "dim_offers")]

    def test_multiple_refs(self):
        sql = """
        from {{ ref('stg_events') }}
        join {{ ref('dim_device') }}
        join {{ ref('stg_page_view') }}
        """
        refs, sources = parse_sql_dependencies(sql)
        assert refs == ["dim_device", "stg_events", "stg_page_view"]
        assert sources == []

    def test_multiline_jinja(self):
        sql = """
        from {{ ref(
            'stg_events'
        ) }}
        """
        refs, _ = parse_sql_dependencies(sql)
        assert refs == ["stg_events"]

    def test_double_quoted_ref(self):
        sql = """from {{ ref("my_model") }}"""
        refs, _ = parse_sql_dependencies(sql)
        assert refs == ["my_model"]

    def test_deduplication(self):
        sql = """
        from {{ ref('stg_events') }}
        join {{ ref('stg_events') }} on 1=1
        """
        refs, _ = parse_sql_dependencies(sql)
        assert refs == ["stg_events"]

    def test_no_refs_or_sources(self):
        sql = "select 1 as id"
        refs, sources = parse_sql_dependencies(sql)
        assert refs == []
        assert sources == []

    def test_depends_on_comment(self):
        sql = """
        -- depends_on: {{ ref('stg_events') }}
        select 1
        """
        refs, _ = parse_sql_dependencies(sql)
        assert refs == ["stg_events"]


class TestParseSqlConfig:
    def test_materialized_single_line(self):
        sql = "{{ config(materialized='incremental') }}"
        config = parse_sql_config(sql)
        assert config["materialized"] == "incremental"

    def test_materialized_multiline(self):
        sql = """
        {{
            config(
                materialized = 'table',
                schema = 'analytics'
            )
        }}
        """
        config = parse_sql_config(sql)
        assert config["materialized"] == "table"

    def test_materialized_double_quotes(self):
        sql = '{{ config(materialized="view") }}'
        config = parse_sql_config(sql)
        assert config["materialized"] == "view"

    def test_no_config(self):
        sql = "select 1"
        config = parse_sql_config(sql)
        assert config == {}


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


class TestParseYamlSources:
    def test_basic_sources(self):
        with tempfile.NamedTemporaryFile(
            suffix=".yml", mode="w", delete=False
        ) as f:
            f.write("""
version: 2

sources:
  - name: core
    schema: REPORT4
    tables:
      - name: dim_offers
      - name: fact_events
""")
            f.flush()
            sources = parse_yaml_sources(Path(f.name))

        assert len(sources) == 1
        assert sources[0].name == "core"
        assert sources[0].schema_name == "REPORT4"
        assert sources[0].database is None
        assert "dim_offers" in sources[0].tables
        assert "fact_events" in sources[0].tables

    def test_sources_with_jinja_database(self):
        with tempfile.NamedTemporaryFile(
            suffix=".yml", mode="w", delete=False
        ) as f:
            f.write("""
version: 2

sources:
  - name: gbr_raw_events
    database: |
      {%- if target.database | lower == "prod" -%} prod_datalake
      {%- else -%} prod_datalake
      {%- endif -%}
    schema: gbr_events_data
    tables:
      - name: raw_page_view
      - name: raw_offer_impression
""")
            f.flush()
            sources = parse_yaml_sources(Path(f.name))

        assert len(sources) == 1
        assert sources[0].name == "gbr_raw_events"
        assert sources[0].schema_name == "gbr_events_data"
        assert "raw_page_view" in sources[0].tables

    def test_no_sources_key(self):
        with tempfile.NamedTemporaryFile(
            suffix=".yml", mode="w", delete=False
        ) as f:
            f.write("""
version: 2

models:
  - name: my_model
""")
            f.flush()
            sources = parse_yaml_sources(Path(f.name))

        assert sources == []

    def test_invalid_yaml(self):
        with tempfile.NamedTemporaryFile(
            suffix=".yml", mode="w", delete=False
        ) as f:
            f.write("{{{{invalid yaml::::")
            f.flush()
            sources = parse_yaml_sources(Path(f.name))

        assert sources == []


class TestParseYamlDescriptions:
    def test_basic_descriptions(self):
        with tempfile.NamedTemporaryFile(
            suffix=".yml", mode="w", delete=False
        ) as f:
            f.write("""
version: 2

models:
  - name: fact_offer_impression
    description: >
      Fact table capturing each offer impression event at the page level.
  - name: dim_device
    description: "Dimension table for device"
""")
            f.flush()
            descriptions = parse_yaml_descriptions(Path(f.name))

        assert "fact_offer_impression" in descriptions
        assert "offer impression" in descriptions["fact_offer_impression"]
        assert descriptions["dim_device"] == "Dimension table for device"

    def test_skip_doc_references(self):
        with tempfile.NamedTemporaryFile(
            suffix=".yml", mode="w", delete=False
        ) as f:
            f.write("""
version: 2

models:
  - name: my_model
    description: "{{ doc('my_model_docs') }}"
""")
            f.flush()
            descriptions = parse_yaml_descriptions(Path(f.name))

        assert "my_model" not in descriptions

    def test_no_models_key(self):
        with tempfile.NamedTemporaryFile(
            suffix=".yml", mode="w", delete=False
        ) as f:
            f.write("""
version: 2

sources:
  - name: core
    schema: REPORT4
""")
            f.flush()
            descriptions = parse_yaml_descriptions(Path(f.name))

        assert descriptions == {}


# ---------------------------------------------------------------------------
# Project discovery
# ---------------------------------------------------------------------------


class TestFindDbtProjects:
    def test_root_layout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos = Path(tmpdir)
            project = repos / "my-repo"
            project.mkdir()
            (project / "dbt_project.yml").write_text("name: test")

            results = find_dbt_projects(repos)
            assert len(results) == 1
            assert results[0][0] == "my-repo"
            assert results[0][1] == project

    def test_nested_layout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos = Path(tmpdir)
            project = repos / "my-repo" / "dbt"
            project.mkdir(parents=True)
            (project / "dbt_project.yml").write_text("name: test")

            results = find_dbt_projects(repos)
            assert len(results) == 1
            assert results[0][0] == "my-repo"
            assert results[0][1] == project

    def test_root_takes_precedence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos = Path(tmpdir)
            repo = repos / "my-repo"
            repo.mkdir()
            (repo / "dbt_project.yml").write_text("name: root")
            nested = repo / "dbt"
            nested.mkdir()
            (nested / "dbt_project.yml").write_text("name: nested")

            results = find_dbt_projects(repos)
            assert len(results) == 1
            assert results[0][1] == repo

    def test_no_dbt_projects(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos = Path(tmpdir)
            (repos / "some-repo").mkdir()

            results = find_dbt_projects(repos)
            assert results == []

    def test_skips_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos = Path(tmpdir)
            (repos / "README.md").write_text("hello")

            results = find_dbt_projects(repos)
            assert results == []


# ---------------------------------------------------------------------------
# Skip directories
# ---------------------------------------------------------------------------


class TestSkipDirectories:
    def test_skip_dbt_packages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir)
            (project / "dbt_project.yml").write_text("name: test")
            models = project / "models"
            models.mkdir()

            # Real model
            (models / "my_model.sql").write_text(
                "{{ config(materialized='view') }}\nselect 1"
            )

            # Vendor model in dbt_packages — should be skipped
            vendor = models / "dbt_packages" / "some_package" / "models"
            vendor.mkdir(parents=True)
            (vendor / "vendor_model.sql").write_text("select 2")

            indexed_models, _ = index_dbt_project(project)
            model_names = [m.name for m in indexed_models]
            assert "my_model" in model_names
            assert "vendor_model" not in model_names

    def test_skip_target(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir)
            (project / "dbt_project.yml").write_text("name: test")
            models = project / "models"
            models.mkdir()

            (models / "real.sql").write_text("select 1")

            target = models / "target"
            target.mkdir()
            (target / "compiled.sql").write_text("select 2")

            indexed_models, _ = index_dbt_project(project)
            model_names = [m.name for m in indexed_models]
            assert "real" in model_names
            assert "compiled" not in model_names


# ---------------------------------------------------------------------------
# Full integration
# ---------------------------------------------------------------------------


class TestIndexFullProject:
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir) / "repos" / "my-repo" / "dbt"
            models = project / "models" / "staging"
            models.mkdir(parents=True)

            (project / "dbt_project.yml").write_text(
                "name: my_dbt_project\nversion: '1.0.0'"
            )

            (models / "stg_users.sql").write_text(
                "{{ config(materialized='view') }}\n"
                "select * from {{ source('raw', 'users') }}"
            )
            (models / "stg_orders.sql").write_text(
                "{{ config(materialized='table') }}\n"
                "select * from {{ ref('stg_users') }}"
            )
            (models / "_schema.yml").write_text("""
version: 2

sources:
  - name: raw
    schema: RAW_DATA
    tables:
      - name: users
      - name: orders

models:
  - name: stg_users
    description: Staged users table from raw source
  - name: stg_orders
    description: Staged orders with user references
""")

            indexed_models, sources = index_dbt_project(project)

            assert len(indexed_models) == 2
            model_names = [m.name for m in indexed_models]
            assert "stg_orders" in model_names
            assert "stg_users" in model_names

            users_model = next(m for m in indexed_models if m.name == "stg_users")
            assert users_model.materialized == "view"
            assert users_model.sources == [("raw", "users")]
            assert users_model.refs == []
            assert users_model.description == "Staged users table from raw source"

            orders_model = next(m for m in indexed_models if m.name == "stg_orders")
            assert orders_model.materialized == "table"
            assert orders_model.refs == ["stg_users"]

            assert len(sources) == 1
            assert sources[0].name == "raw"
            assert sources[0].schema_name == "RAW_DATA"
            assert "users" in sources[0].tables

    def test_manifest_md_output(self):
        models = [
            ModelInfo(
                name="stg_users",
                path="models/staging/stg_users.sql",
                materialized="view",
                refs=[],
                sources=[("raw", "users")],
                description="Staged users",
            ),
        ]

        md = generate_manifest_md(models, "my-repo", Path("repos/my-repo/dbt"), "my_dbt")
        assert "# dbt Models Index: my-repo" in md
        assert "### stg_users" in md
        assert "**materialized:** view" in md
        assert "raw.users" in md
        assert "**description:** Staged users" in md
        assert "**Total models:** 1" in md

    def test_sources_md_output(self):
        sources = [
            SourceInfo(
                name="raw",
                database="prod_datalake",
                schema_name="RAW_DATA",
                tables=["users", "orders"],
            ),
        ]

        md = generate_sources_md(sources, "my-repo")
        assert "# dbt Sources: my-repo" in md
        assert "### raw" in md
        assert "**Database:** prod_datalake" in md
        assert "**Schema:** RAW_DATA" in md
        assert "users, orders" in md


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_bad_yaml_does_not_crash(self):
        with tempfile.NamedTemporaryFile(
            suffix=".yml", mode="w", delete=False
        ) as f:
            f.write("{{{{ totally broken yaml\n  - not valid: [")
            f.flush()

            sources = parse_yaml_sources(Path(f.name))
            assert sources == []

            descriptions = parse_yaml_descriptions(Path(f.name))
            assert descriptions == {}

    def test_missing_models_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir)
            (project / "dbt_project.yml").write_text("name: test")

            models, sources = index_dbt_project(project)
            assert models == []
            assert sources == []

    def test_read_project_config_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = read_project_config(Path(tmpdir))
            assert config == {}

    def test_binary_sql_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir)
            (project / "dbt_project.yml").write_text("name: test")
            models = project / "models"
            models.mkdir()

            (models / "good.sql").write_text("select 1")
            (models / "binary.sql").write_bytes(b"\x00\x01\x02\x03")

            indexed_models, _ = index_dbt_project(project)
            model_names = [m.name for m in indexed_models]
            assert "good" in model_names
