from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql://localhost:5432/archaeologist"

    # Where cloned repositories live on disk.
    repos_dir: Path = BACKEND_ROOT / "data" / "repos"

    # Optional; used for cloning private repos (and GitHub API access in phase 2).
    github_token: str | None = None

    # Embeddings run locally so indexing works without any API key.
    # Changing the model/dimension requires re-creating the chunks table.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # Who writes answers: "auto" picks Claude when an API key is set, else a
    # local Ollama model when one is running, else an evidence briefing (no model).
    # Force one with "anthropic" | "ollama" | "briefing".
    answer_provider: str = "auto"

    # Claude (paid API). The key is read from backend/.env or the environment.
    anthropic_api_key: str | None = None
    answer_model: str = "claude-opus-5"
    answer_effort: str = "medium"
    answer_max_tokens: int = 16000

    # Local model via Ollama (free, runs on this machine).
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_num_ctx: int = 16384  # context window; evidence is trimmed to fit

    # Ingestion limits.
    max_file_bytes: int = 400_000
    max_files: int = 20_000
    max_commits: int = 20_000
    # Pages of 100 per GitHub listing (pull requests, issues) per indexing run.
    github_max_pages: int = 10
    # GitHub requests per indexing run for PR/issue discussion threads.
    github_max_comment_requests: int = 200

    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
