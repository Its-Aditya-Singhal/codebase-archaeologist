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

    # Answering model. Credentials are resolved by the Anthropic SDK
    # (ANTHROPIC_API_KEY or an `ant auth login` profile).
    answer_model: str = "claude-opus-5"
    answer_effort: str = "medium"
    answer_max_tokens: int = 16000

    # Ingestion limits.
    max_file_bytes: int = 400_000
    max_files: int = 20_000

    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
