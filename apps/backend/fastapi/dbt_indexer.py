"""Re-export from nao_core for backward compatibility.

The actual implementation lives in nao_core.dbt_indexer so it can be used
by both the FastAPI server and the CLI (nao sync).
"""

from nao_core.dbt_indexer import (  # noqa: F401
    ModelInfo,
    SourceInfo,
    find_dbt_projects,
    generate_manifest_md,
    generate_sources_md,
    index_all_projects,
    index_dbt_project,
    parse_sql_config,
    parse_sql_dependencies,
    parse_yaml_descriptions,
    parse_yaml_sources,
    read_project_config,
)
