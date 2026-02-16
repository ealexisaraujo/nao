"""Index dbt projects in repos/ and generate searchable markdown files.

Parses SQL files for refs/sources/config and YAML files for source definitions
and model descriptions. Outputs manifest.md and sources.md per repository into
the dbt-index/ folder.
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import yaml

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class ModelInfo(NamedTuple):
    name: str
    path: str
    materialized: str | None
    refs: list[str]
    sources: list[tuple[str, str]]
    description: str | None


class SourceInfo(NamedTuple):
    name: str
    database: str | None
    schema_name: str
    tables: list[str]


# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

RE_REF = re.compile(
    r"""\{\{\s*ref\(\s*['"]([^'"]+)['"]\s*\)\s*\}\}""",
)
RE_SOURCE = re.compile(
    r"""\{\{\s*source\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*\}\}""",
)
RE_CONFIG_MATERIALIZED = re.compile(
    r"""\{\{\s*config\([^)]*materialized\s*=\s*['"]([^'"]+)['"]""",
    re.DOTALL,
)
RE_JINJA_BLOCK = re.compile(r"\{%.*?%\}", re.DOTALL)
RE_JINJA_EXPR = re.compile(r"\{\{.*?\}\}", re.DOTALL)

SKIP_DIRS = {"dbt_packages", "dbt_modules", "target", "logs", ".git"}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def index_all_projects(project_folder: Path) -> None:
    """Scan repos/, find dbt projects, index each, write to dbt-index/."""
    repos_path = project_folder / "repos"
    if not repos_path.is_dir():
        return

    projects = find_dbt_projects(repos_path)
    if not projects:
        return

    index_root = project_folder / "dbt-index"
    index_root.mkdir(exist_ok=True)

    for repo_name, project_path in projects:
        try:
            _index_single_project(repo_name, project_path, index_root, project_folder)
        except Exception as e:
            print(f"[dbt-indexer] Error indexing {repo_name}: {e}")


def _index_single_project(
    repo_name: str,
    project_path: Path,
    index_root: Path,
    project_folder: Path,
) -> None:
    project_config = read_project_config(project_path)
    project_name = project_config.get("name", repo_name)
    default_materializations = _extract_default_materializations(project_config)

    models, sources = index_dbt_project(project_path, default_materializations)

    out_dir = index_root / repo_name
    out_dir.mkdir(parents=True, exist_ok=True)

    rel_project_path = project_path.relative_to(project_folder)
    manifest = generate_manifest_md(models, repo_name, rel_project_path, project_name)
    (out_dir / "manifest.md").write_text(manifest, encoding="utf-8")

    sources_md = generate_sources_md(sources, repo_name)
    (out_dir / "sources.md").write_text(sources_md, encoding="utf-8")

    print(f"[dbt-indexer] Indexed {repo_name}: {len(models)} models, {len(sources)} sources")


# ---------------------------------------------------------------------------
# Project discovery
# ---------------------------------------------------------------------------


def find_dbt_projects(repos_path: Path) -> list[tuple[str, Path]]:
    """Return (repo_name, dbt_project_dir) for each repo with a dbt project."""
    results: list[tuple[str, Path]] = []

    for entry in sorted(repos_path.iterdir()):
        if not entry.is_dir():
            continue

        root_project = entry / "dbt_project.yml"
        nested_project = entry / "dbt" / "dbt_project.yml"

        if root_project.is_file():
            results.append((entry.name, entry))
        elif nested_project.is_file():
            results.append((entry.name, entry / "dbt"))

    return results


# ---------------------------------------------------------------------------
# Project indexing
# ---------------------------------------------------------------------------


def index_dbt_project(
    project_path: Path,
    default_materializations: dict[str, str] | None = None,
) -> tuple[list[ModelInfo], list[SourceInfo]]:
    """Walk models/ and parse SQL + YAML files."""
    models_dir = project_path / "models"
    if not models_dir.is_dir():
        return [], []

    models: list[ModelInfo] = []
    sources: list[SourceInfo] = []
    descriptions: dict[str, str] = {}

    # First pass: collect YAML descriptions and sources
    for yaml_path in models_dir.rglob("*.yml"):
        if _should_skip(yaml_path) or not yaml_path.is_file():
            continue
        try:
            descriptions.update(parse_yaml_descriptions(yaml_path))
        except Exception as e:
            print(f"[dbt-indexer] Warning: failed to parse descriptions from {yaml_path}: {e}")
        try:
            sources.extend(parse_yaml_sources(yaml_path))
        except Exception as e:
            print(f"[dbt-indexer] Warning: failed to parse sources from {yaml_path}: {e}")

    for yaml_path in models_dir.rglob("*.yaml"):
        if _should_skip(yaml_path) or not yaml_path.is_file():
            continue
        try:
            descriptions.update(parse_yaml_descriptions(yaml_path))
        except Exception as e:
            print(f"[dbt-indexer] Warning: failed to parse descriptions from {yaml_path}: {e}")
        try:
            sources.extend(parse_yaml_sources(yaml_path))
        except Exception as e:
            print(f"[dbt-indexer] Warning: failed to parse sources from {yaml_path}: {e}")

    # Second pass: parse SQL models
    for sql_path in models_dir.rglob("*.sql"):
        if _should_skip(sql_path) or not sql_path.is_file():
            continue
        try:
            model = _parse_sql_model(sql_path, models_dir, descriptions, default_materializations)
            models.append(model)
        except Exception as e:
            print(f"[dbt-indexer] Warning: failed to parse {sql_path}: {e}")

    models.sort(key=lambda m: m.name)
    return models, sources


def _parse_sql_model(
    sql_path: Path,
    models_dir: Path,
    descriptions: dict[str, str],
    default_materializations: dict[str, str] | None,
) -> ModelInfo:
    content = sql_path.read_text(encoding="utf-8", errors="replace")
    name = sql_path.stem
    rel_path = str(sql_path.relative_to(models_dir.parent))

    refs, srcs = parse_sql_dependencies(content)
    config = parse_sql_config(content)

    materialized = config.get("materialized")
    if not materialized and default_materializations:
        materialized = _resolve_default_materialization(sql_path, models_dir, default_materializations)

    description = descriptions.get(name)

    return ModelInfo(
        name=name,
        path=rel_path,
        materialized=materialized,
        refs=refs,
        sources=srcs,
        description=description,
    )


def _should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _resolve_default_materialization(
    sql_path: Path,
    models_dir: Path,
    default_materializations: dict[str, str],
) -> str | None:
    """Walk from the model's directory up to models/ checking for defaults."""
    current = sql_path.parent
    while current >= models_dir:
        dir_name = current.name
        if dir_name in default_materializations:
            return default_materializations[dir_name]
        current = current.parent
    return default_materializations.get("_root_")


def _extract_default_materializations(config: dict) -> dict[str, str]:
    """Extract directory-level default materializations from dbt_project.yml."""
    result: dict[str, str] = {}
    models_config = config.get("models", {})
    if not isinstance(models_config, dict):
        return result

    for project_models in models_config.values():
        if isinstance(project_models, dict):
            _walk_materialization_config(project_models, result)

    return result


def _walk_materialization_config(node: dict, result: dict[str, str]) -> None:
    mat = node.get("+materialized") or node.get("materialized")
    if isinstance(mat, str):
        # We need the parent key to map this, but we don't have it here.
        # Instead, walk children and use their keys.
        pass

    for key, value in node.items():
        if key.startswith("+"):
            continue
        if isinstance(value, dict):
            child_mat = value.get("+materialized") or value.get("materialized")
            if isinstance(child_mat, str):
                result[key] = child_mat
            _walk_materialization_config(value, result)


# ---------------------------------------------------------------------------
# SQL parsing
# ---------------------------------------------------------------------------


def parse_sql_dependencies(content: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Extract ref() and source() calls from SQL content."""
    refs = sorted(set(RE_REF.findall(content)))
    sources = sorted(set(RE_SOURCE.findall(content)))
    return refs, sources


def parse_sql_config(content: str) -> dict:
    """Extract config properties from SQL content."""
    result: dict[str, str] = {}
    match = RE_CONFIG_MATERIALIZED.search(content)
    if match:
        result["materialized"] = match.group(1)
    return result


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


def _strip_jinja(text: str) -> str:
    """Remove Jinja blocks/expressions so yaml.safe_load can parse the file."""
    text = RE_JINJA_BLOCK.sub("", text)
    text = RE_JINJA_EXPR.sub('""', text)
    return text


def parse_yaml_sources(path: Path) -> list[SourceInfo]:
    """Parse source definitions from a dbt YAML file."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    cleaned = _strip_jinja(raw)

    try:
        data = yaml.safe_load(cleaned)
    except yaml.YAMLError:
        return []

    if not isinstance(data, dict):
        return []

    results: list[SourceInfo] = []
    for src in data.get("sources", []) or []:
        if not isinstance(src, dict):
            continue

        name = src.get("name", "")
        database = src.get("database")
        if isinstance(database, str):
            # After Jinja stripping, multiline conditionals may leave
            # duplicate values — take the first non-empty line.
            lines = [ln.strip() for ln in database.splitlines() if ln.strip()]
            database = lines[0] if lines else None
        schema_name = src.get("schema", "")

        tables: list[str] = []
        for tbl in src.get("tables", []) or []:
            if isinstance(tbl, dict) and tbl.get("name"):
                tables.append(tbl["name"])

        if name:
            results.append(
                SourceInfo(
                    name=name,
                    database=database,
                    schema_name=str(schema_name),
                    tables=sorted(tables),
                )
            )

    return results


def parse_yaml_descriptions(path: Path) -> dict[str, str]:
    """Extract model name -> description mapping from a dbt YAML file."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    cleaned = _strip_jinja(raw)

    try:
        data = yaml.safe_load(cleaned)
    except yaml.YAMLError:
        return {}

    if not isinstance(data, dict):
        return {}

    result: dict[str, str] = {}
    for model in data.get("models", []) or []:
        if not isinstance(model, dict):
            continue
        name = model.get("name", "")
        desc = model.get("description", "")
        if name and desc and not desc.startswith("{{"):
            result[name] = desc.strip()

    return result


# ---------------------------------------------------------------------------
# Config reading
# ---------------------------------------------------------------------------


RE_DBT_PROJECT_NAME = re.compile(r"""^name:\s*['"]?([^'"#\n]+)""", re.MULTILINE)


def read_project_config(path: Path) -> dict:
    """Read dbt_project.yml for project name and default materializations."""
    config_path = path / "dbt_project.yml"
    if not config_path.is_file():
        return {}

    raw = config_path.read_text(encoding="utf-8", errors="replace")

    # Try Jinja-stripped YAML parse first; fall back to regex for name
    cleaned = _strip_jinja(raw)
    try:
        data = yaml.safe_load(cleaned)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError:
        pass

    # Fallback: extract name via regex from raw content
    result: dict = {}
    match = RE_DBT_PROJECT_NAME.search(raw)
    if match:
        result["name"] = match.group(1).strip().strip("'\"")
    return result


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------


def generate_manifest_md(
    models: list[ModelInfo],
    repo_name: str,
    project_path: Path | str,
    project_name: str,
) -> str:
    """Generate manifest.md with all indexed dbt models."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        f"# dbt Models Index: {repo_name}",
        "",
        f"- **Project name:** {project_name}",
        f"- **dbt project path:** {project_path}",
        f"- **Total models:** {len(models)}",
        f"- **Indexed at:** {now}",
        "",
        "---",
    ]

    for model in models:
        lines.append("")
        lines.append(f"### {model.name}")
        lines.append(f"- **path:** {model.path}")

        if model.materialized:
            lines.append(f"- **materialized:** {model.materialized}")

        refs_str = ", ".join(model.refs) if model.refs else "\u2014"
        lines.append(f"- **refs:** {refs_str}")

        if model.sources:
            sources_str = ", ".join(f"{s[0]}.{s[1]}" for s in model.sources)
        else:
            sources_str = "\u2014"
        lines.append(f"- **sources:** {sources_str}")

        if model.description:
            lines.append(f"- **description:** {model.description}")

    lines.append("")
    return "\n".join(lines)


def generate_sources_md(sources: list[SourceInfo], repo_name: str) -> str:
    """Generate sources.md with source-to-database mappings."""
    lines = [
        f"# dbt Sources: {repo_name}",
        "",
    ]

    sorted_sources = sorted(sources, key=lambda s: s.name)

    for src in sorted_sources:
        lines.append(f"### {src.name}")

        if src.database:
            lines.append(f"- **Database:** {src.database}")
        else:
            lines.append("- **Database:** (varies by target)")

        lines.append(f"- **Schema:** {src.schema_name}")

        if src.tables:
            lines.append(f"- **Tables:** {', '.join(src.tables)}")

        lines.append("")

    return "\n".join(lines)
