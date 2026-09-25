"""Test configuration.

Unit tests need nothing. Integration tests (tests/test_integration.py) use a
separate database, `archaeologist_test`, recreated per run, so they never touch
the development data; they are skipped when PostgreSQL is not reachable.
"""

import os

TEST_DATABASE = "archaeologist_test"
_base = os.environ.get("TEST_DATABASE_URL_BASE", "postgresql://localhost:5432")

# Environment variables take precedence over backend/.env in pydantic-settings.
os.environ["DATABASE_URL"] = f"{_base}/{TEST_DATABASE}"
os.environ["ANSWER_PROVIDER"] = "briefing"  # no model calls in tests
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["GITHUB_TOKEN"] = ""
