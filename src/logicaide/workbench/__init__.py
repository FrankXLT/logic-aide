"""Agent Brief generators, readiness checks, and CLI wiring for the LogicAIDE workbench."""

from __future__ import annotations

from logicaide.workbench.briefs import (
    DEFAULT_STAGING_ROOT,
    NOT_IN_WATCHLIST,
    build_candidate_build_brief,
    build_issue_brief,
    build_load_cell_brief,
    build_session_brief,
    format_brief_markdown,
    generate_agent_brief,
)
from logicaide.workbench.readiness import (
    check_workbench_readiness,
    format_readiness_text,
)

__all__ = [
    "DEFAULT_STAGING_ROOT",
    "NOT_IN_WATCHLIST",
    "build_candidate_build_brief",
    "build_issue_brief",
    "build_load_cell_brief",
    "build_session_brief",
    "check_workbench_readiness",
    "format_brief_markdown",
    "format_readiness_text",
    "generate_agent_brief",
]
