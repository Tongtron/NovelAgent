from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(path: Path) -> None:
    """Load a small .env file without overwriting real environment variables."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 只能填写 true/false、1/0、yes/no 或 on/off")


@dataclass(frozen=True, slots=True)
class Settings:
    mode: str
    database_path: Path
    host: str
    port: int
    llm_provider: str
    llm_api_key: str | None
    llm_base_url: str | None
    llm_model: str | None
    llm_timeout_seconds: float
    llm_max_retries: int
    search_provider: str
    search_api_key: str | None
    search_base_url: str | None
    search_timeout_seconds: float
    search_max_results: int
    embedding_provider: str
    embedding_api_key: str | None
    embedding_base_url: str | None
    embedding_model: str | None
    embedding_timeout_seconds: float
    semantic_retrieval_limit: int
    publisher_provider: str
    publisher_api_key: str | None
    publisher_base_url: str | None
    story_skills_enabled: bool
    oh_story_enabled: bool
    max_revision_attempts: int = 2

    @classmethod
    def load(cls, root: Path | None = None) -> "Settings":
        root = (root or Path.cwd()).resolve()
        _load_env_file(root / ".env")
        _load_env_file(root / ".env.local")
        database = Path(os.getenv("NOVEL_AGENT_DATABASE", "data/novel_agent.db"))
        if not database.is_absolute():
            database = root / database
        llm_provider = os.getenv("LLM_PROVIDER", "disabled").lower()
        llm_api_key = os.getenv("LLM_API_KEY") or None
        if llm_provider == "deepseek":
            llm_api_key = llm_api_key or os.getenv("DEEPSEEK_API_KEY") or None
        llm_base_url = os.getenv("LLM_BASE_URL") or None
        llm_model = os.getenv("LLM_MODEL") or None
        if llm_provider == "deepseek":
            llm_base_url = llm_base_url or "https://api.deepseek.com"
            llm_model = llm_model or "deepseek-v4-pro"
        search_provider = os.getenv("SEARCH_PROVIDER", "disabled").lower()
        search_base_url = os.getenv("SEARCH_BASE_URL") or None
        if search_provider == "tavily":
            search_base_url = search_base_url or "https://api.tavily.com"
        embedding_provider = os.getenv("EMBEDDING_PROVIDER", "disabled").lower()
        embedding_base_url = os.getenv("EMBEDDING_BASE_URL") or None
        embedding_model = os.getenv("EMBEDDING_MODEL") or None
        if embedding_provider == "siliconflow":
            embedding_base_url = embedding_base_url or "https://api.siliconflow.cn/v1"
            embedding_model = embedding_model or "BAAI/bge-m3"
        return cls(
            mode=os.getenv("NOVEL_AGENT_MODE", "offline").lower(),
            database_path=database,
            host=os.getenv("NOVEL_AGENT_HOST", "127.0.0.1"),
            port=int(os.getenv("NOVEL_AGENT_PORT", "8000")),
            llm_provider=llm_provider,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
            search_provider=search_provider,
            search_api_key=os.getenv("SEARCH_API_KEY") or None,
            search_base_url=search_base_url,
            search_timeout_seconds=float(os.getenv("SEARCH_TIMEOUT_SECONDS", "30")),
            search_max_results=max(1, min(int(os.getenv("SEARCH_MAX_RESULTS", "3")), 5)),
            embedding_provider=embedding_provider,
            embedding_api_key=os.getenv("EMBEDDING_API_KEY") or None,
            embedding_base_url=embedding_base_url,
            embedding_model=embedding_model,
            embedding_timeout_seconds=float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "60")),
            semantic_retrieval_limit=max(
                1, min(int(os.getenv("SEMANTIC_RETRIEVAL_LIMIT", "6")), 20)
            ),
            publisher_provider=os.getenv("PUBLISHER_PROVIDER", "mock").lower(),
            publisher_api_key=os.getenv("PUBLISHER_API_KEY") or None,
            publisher_base_url=os.getenv("PUBLISHER_BASE_URL") or None,
            story_skills_enabled=_env_bool("STORY_SKILLS_ENABLED", True),
            oh_story_enabled=_env_bool("OH_STORY_ENABLED", True),
            max_revision_attempts=int(os.getenv("MAX_REVISION_ATTEMPTS", "2")),
        )

    def api_readiness(self) -> dict[str, dict[str, object]]:
        llm_configured = bool(
            self.llm_api_key and self.llm_model and self.llm_base_url
        )
        search_configured = bool(self.search_api_key and self.search_base_url)
        embedding_configured = bool(
            self.embedding_api_key and self.embedding_base_url and self.embedding_model
        )
        return {
            "llm": {
                "provider": self.llm_provider,
                "model": self.llm_model,
                "configured": llm_configured,
                "enabled": self.mode == "online"
                and self.llm_provider != "disabled"
                and llm_configured,
            },
            "search": {
                "provider": self.search_provider,
                "configured": search_configured,
                "enabled": self.mode == "online"
                and self.search_provider != "disabled"
                and search_configured,
            },
            "embedding": {
                "provider": self.embedding_provider,
                "model": self.embedding_model,
                "configured": embedding_configured,
                "enabled": self.mode == "online"
                and self.embedding_provider != "disabled"
                and embedding_configured,
            },
            "publisher": {
                "provider": self.publisher_provider,
                "configured": self.publisher_provider == "mock" or bool(self.publisher_api_key),
                "enabled": self.publisher_provider == "mock",
            },
        }
