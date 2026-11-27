"""Environment loading and hard limits for the agent loop."""
from dataclasses import dataclass
import os

from dotenv import load_dotenv

load_dotenv()

# Hard caps (not overridable from .env; .env only tightens or leaves defaults).
MAX_OUTPUT_CHARS = 8000        # tool results are truncated to this
READ_FILE_MAX_LINES = 400      # read_file never returns more lines than this
SEARCH_MAX_MATCHES = 50        # search_code caps matched lines
DIFF_MAX_CHARS = 20000         # final diff truncated to this in reports/logs


@dataclass
class Settings:
    llm_provider: str
    anthropic_api_key: str | None
    anthropic_model: str
    openai_api_key: str | None
    openai_model: str
    xai_api_key: str | None
    xai_model: str
    xai_base_url: str
    max_attempts: int
    command_timeout: int
    max_tool_steps: int


def load_settings() -> Settings:
    """Build Settings from the environment (.env)."""
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    return Settings(
        llm_provider=provider,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        xai_api_key=(os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")) or None,
        # Set XAI_MODEL to the exact Grok model enabled for your xAI account.
        xai_model=os.getenv("XAI_MODEL", "grok-4-latest"),
        xai_base_url=os.getenv("XAI_BASE_URL", "https://api.x.ai/v1"),
        max_attempts=int(os.getenv("MAX_ATTEMPTS", "3")),
        command_timeout=int(os.getenv("COMMAND_TIMEOUT", "60")),
        max_tool_steps=int(os.getenv("MAX_TOOL_STEPS", "12")),
    )
