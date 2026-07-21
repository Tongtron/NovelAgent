from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from novel_agent.adapters import (
    build_embedding_gateway,
    build_gateway,
    build_search_adapter,
)
from novel_agent.api import AppContext
from novel_agent.config import Settings
from novel_agent.repository import SQLiteRepository
from novel_agent.service import NovelAgentService
from novel_agent.story_skills import StorySkillsRuntime
from novel_agent.writing_skills import OhStoryWritingRuntime


def create_context(root: Path | None = None, database_path: Path | None = None) -> AppContext:
    root = (root or Path.cwd()).resolve()
    settings = Settings.load(root)
    if database_path is not None:
        settings = replace(settings, database_path=database_path)
    repository = SQLiteRepository(settings.database_path)
    gateway = build_gateway(
        settings.mode,
        settings.llm_provider,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
    search = build_search_adapter(
        settings.mode,
        settings.search_provider,
        api_key=settings.search_api_key,
        base_url=settings.search_base_url,
        timeout_seconds=settings.search_timeout_seconds,
        max_results=settings.search_max_results,
    )
    embedding = build_embedding_gateway(
        settings.mode,
        settings.embedding_provider,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    story_skills = StorySkillsRuntime.load(
        root,
        enabled=settings.story_skills_enabled,
    )
    writing_skills = OhStoryWritingRuntime.load(
        root,
        enabled=settings.oh_story_enabled,
    )
    service = NovelAgentService(
        repository=repository,
        gateway=gateway,
        search=search,
        embedding=embedding,
        max_revision_attempts=settings.max_revision_attempts,
        semantic_retrieval_limit=settings.semantic_retrieval_limit,
        story_skills=story_skills,
        writing_skills=writing_skills,
    )
    return AppContext(settings, service, repository, root / "apps" / "web")
