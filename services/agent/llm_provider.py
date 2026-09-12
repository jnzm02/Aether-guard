"""
LLM Provider Abstraction Layer for Aether-Guard Agent

Supports multiple LLM providers (Anthropic Claude, OpenAI GPT) through a unified interface.
Provider selection is controlled via the LLM_PROVIDER environment variable.

Environment variables:
  LLM_PROVIDER=anthropic|openai  (default: anthropic)

  For Anthropic:
    ANTHROPIC_API_KEY=sk-ant-...
    CLAUDE_MODEL=claude-sonnet-4-5-20250929

  For OpenAI:
    OPENAI_API_KEY=sk-...
    OPENAI_MODEL=gpt-4-turbo

Design:
  - Abstract base class (LLMProvider) defines common interface
  - Concrete implementations for each provider
  - Factory function creates appropriate provider based on env vars
  - All providers return raw text responses (caller handles JSON parsing)
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import Any

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Abstract Base Class
# ─────────────────────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
    ) -> str:
        """
        Generate a response from the LLM.

        Args:
            system: System prompt (context, instructions)
            user: User prompt (the actual query/task)
            max_tokens: Maximum tokens in response

        Returns:
            Raw text response from the LLM

        Raises:
            Exception: If API call fails
        """
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider name (e.g., 'anthropic', 'openai')."""
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Anthropic Provider (Claude)
# ─────────────────────────────────────────────────────────────────────────────

class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider implementation."""

    def __init__(self, api_key: str, model: str):
        super().__init__(model)
        try:
            import anthropic
            self.client = anthropic.AsyncAnthropic(api_key=api_key)
        except ImportError:
            raise RuntimeError(
                "anthropic package not installed. "
                "Install with: pip install anthropic"
            )

    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
    ) -> str:
        """Call Claude API and return raw text response."""
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        # Extract text from response
        if not response.content:
            raise ValueError("Claude API returned empty response")

        # Claude returns list of content blocks; we expect text
        content_block = response.content[0]
        if not hasattr(content_block, "text"):
            raise ValueError(f"Unexpected Claude response format: {content_block}")

        return content_block.text

    def get_provider_name(self) -> str:
        return "anthropic"


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI Provider (GPT-4, etc.)
# ─────────────────────────────────────────────────────────────────────────────

class OpenAIProvider(LLMProvider):
    """OpenAI GPT provider implementation."""

    def __init__(self, api_key: str, model: str):
        super().__init__(model)
        try:
            import openai
            self.client = openai.AsyncOpenAI(api_key=api_key)
        except ImportError:
            raise RuntimeError(
                "openai package not installed. "
                "Install with: pip install openai"
            )

    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
    ) -> str:
        """Call OpenAI API and return raw text response."""
        response = await self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        # Extract text from response
        if not response.choices:
            raise ValueError("OpenAI API returned empty response")

        choice = response.choices[0]
        if not choice.message or not choice.message.content:
            raise ValueError("OpenAI response missing content")

        return choice.message.content

    def get_provider_name(self) -> str:
        return "openai"


# ─────────────────────────────────────────────────────────────────────────────
# Factory Function
# ─────────────────────────────────────────────────────────────────────────────

# Default models per provider
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5-20250929",
    "openai": "gpt-4-turbo-2024-04-09",
}


def create_llm_provider() -> LLMProvider:
    """
    Create LLM provider based on environment variables.

    Environment variables:
      LLM_PROVIDER=anthropic|openai  (default: anthropic)

      For Anthropic:
        ANTHROPIC_API_KEY=sk-ant-...
        CLAUDE_MODEL=claude-sonnet-4-5-20250929 (optional)

      For OpenAI:
        OPENAI_API_KEY=sk-...
        OPENAI_MODEL=gpt-4-turbo (optional)

    Returns:
        LLMProvider instance (AnthropicProvider or OpenAIProvider)

    Raises:
        ValueError: If provider is invalid or required API key is missing
    """
    provider_name = os.getenv("LLM_PROVIDER", "anthropic").lower()

    if provider_name == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable is required when LLM_PROVIDER=anthropic"
            )

        model = os.getenv("CLAUDE_MODEL", DEFAULT_MODELS["anthropic"])
        log.info(f"Initializing Anthropic provider with model: {model}")
        return AnthropicProvider(api_key=api_key, model=model)

    elif provider_name == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY environment variable is required when LLM_PROVIDER=openai"
            )

        model = os.getenv("OPENAI_MODEL", DEFAULT_MODELS["openai"])
        log.info(f"Initializing OpenAI provider with model: {model}")
        return OpenAIProvider(api_key=api_key, model=model)

    else:
        raise ValueError(
            f"Invalid LLM_PROVIDER: {provider_name}. "
            f"Must be 'anthropic' or 'openai'"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Convenience Functions
# ─────────────────────────────────────────────────────────────────────────────

def get_provider_info() -> dict[str, Any]:
    """
    Get information about the configured LLM provider.

    Returns:
        Dict with provider name, model, and whether API key is set
    """
    provider_name = os.getenv("LLM_PROVIDER", "anthropic").lower()

    if provider_name == "anthropic":
        return {
            "provider": "anthropic",
            "model": os.getenv("CLAUDE_MODEL", DEFAULT_MODELS["anthropic"]),
            "api_key_set": bool(os.getenv("ANTHROPIC_API_KEY")),
        }
    elif provider_name == "openai":
        return {
            "provider": "openai",
            "model": os.getenv("OPENAI_MODEL", DEFAULT_MODELS["openai"]),
            "api_key_set": bool(os.getenv("OPENAI_API_KEY")),
        }
    else:
        return {
            "provider": provider_name,
            "model": "unknown",
            "api_key_set": False,
        }


def validate_provider_config() -> tuple[bool, str]:
    """
    Validate that LLM provider configuration is correct.

    Returns:
        Tuple of (is_valid: bool, error_message: str)
        If valid, error_message is empty string.
    """
    provider_name = os.getenv("LLM_PROVIDER", "anthropic").lower()

    if provider_name not in ["anthropic", "openai"]:
        return False, f"Invalid LLM_PROVIDER: {provider_name}. Must be 'anthropic' or 'openai'"

    if provider_name == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            return False, "ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic"

    elif provider_name == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY is required when LLM_PROVIDER=openai"

    return True, ""
