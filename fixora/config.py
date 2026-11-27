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
    groq_api_key: str | None
    groq_model: str
    groq_base_url: str
    max_attempts: int
    command_timeout: int
    max_tool_steps: int
    llm_providers: str          # raw LLM_PROVIDERS chain, e.g. "groq,openai" or explicit triples
    run_timeout: int            # overall wall-clock cap for one run (API background task)
    clone_timeout: int          # git clone timeout for GitHub URLs
    max_repo_mb: int            # refuse to clone repos larger than this
    read_cache_chars: int       # cap on already-read file contents passed into prompts
    allow_dependency_install: bool = True   # permit install_dependencies (repo-declared deps only)
    install_timeout: int = 300              # pip --target timeout for dependency installation


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
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        # Groq Cloud is OpenAI-compatible. llama-3.3-70b-versatile /
        # llama-3.1-8b-instant were shut down for free/developer tiers on
        # 2026-08-16; openai/gpt-oss-120b is Groq's recommended replacement.
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        groq_base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        max_attempts=int(os.getenv("MAX_ATTEMPTS", "3")),
        command_timeout=int(os.getenv("COMMAND_TIMEOUT", "60")),
        max_tool_steps=int(os.getenv("MAX_TOOL_STEPS", "12")),
        llm_providers=os.getenv("LLM_PROVIDERS", "").strip(),
        run_timeout=int(os.getenv("RUN_TIMEOUT", "900")),
        clone_timeout=int(os.getenv("CLONE_TIMEOUT", "120")),
        max_repo_mb=int(os.getenv("MAX_REPO_MB", "50")),
        read_cache_chars=int(os.getenv("READ_CACHE_CHARS", "24000")),
        allow_dependency_install=os.getenv("FIXORA_ALLOW_DEPENDENCY_INSTALL", "1").strip().lower()
        not in ("0", "false", "no", "off"),
        install_timeout=int(os.getenv("FIXORA_INSTALL_TIMEOUT", "300")),
    )
