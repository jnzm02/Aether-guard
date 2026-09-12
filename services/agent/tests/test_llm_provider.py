"""
Tests for LLM provider abstraction layer.

Tests both Anthropic and OpenAI provider implementations with mocked API clients.
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Import the LLM provider module
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_provider import (
    AnthropicProvider,
    OpenAIProvider,
    create_llm_provider,
    get_provider_info,
    validate_provider_config,
)


class TestAnthropicProvider:
    """Tests for Anthropic/Claude provider."""

    @pytest.mark.asyncio
    async def test_anthropic_generate_success(self):
        """Test successful response from Anthropic API."""
        with patch("llm_provider.anthropic") as mock_anthropic:
            # Mock the AsyncAnthropic client
            mock_client = AsyncMock()
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            # Mock the response
            mock_response = MagicMock()
            mock_content_block = MagicMock()
            mock_content_block.text = '{"analysis": "test", "confidence": 0.9}'
            mock_response.content = [mock_content_block]
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            # Create provider and call generate
            provider = AnthropicProvider(api_key="sk-ant-test", model="claude-3-5-sonnet")
            result = await provider.generate(
                system="You are a test assistant",
                user="What is 2+2?",
                max_tokens=100,
            )

            assert result == '{"analysis": "test", "confidence": 0.9}'
            assert provider.get_provider_name() == "anthropic"

            # Verify API was called with correct parameters
            mock_client.messages.create.assert_called_once()
            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == "claude-3-5-sonnet"
            assert call_kwargs["max_tokens"] == 100
            assert call_kwargs["system"] == "You are a test assistant"
            assert call_kwargs["messages"][0]["content"] == "What is 2+2?"

    @pytest.mark.asyncio
    async def test_anthropic_empty_response(self):
        """Test handling of empty response from Anthropic."""
        with patch("llm_provider.anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            # Mock empty response
            mock_response = MagicMock()
            mock_response.content = []
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            provider = AnthropicProvider(api_key="sk-ant-test", model="claude-test")

            with pytest.raises(ValueError, match="empty response"):
                await provider.generate(system="test", user="test")


class TestOpenAIProvider:
    """Tests for OpenAI/GPT provider."""

    @pytest.mark.asyncio
    async def test_openai_generate_success(self):
        """Test successful response from OpenAI API."""
        with patch("llm_provider.openai") as mock_openai:
            # Mock the AsyncOpenAI client
            mock_client = AsyncMock()
            mock_openai.AsyncOpenAI.return_value = mock_client

            # Mock the response
            mock_response = MagicMock()
            mock_choice = MagicMock()
            mock_message = MagicMock()
            mock_message.content = '{"analysis": "test", "confidence": 0.85}'
            mock_choice.message = mock_message
            mock_response.choices = [mock_choice]
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

            # Create provider and call generate
            provider = OpenAIProvider(api_key="sk-test", model="gpt-4")
            result = await provider.generate(
                system="You are a test assistant",
                user="What is 2+2?",
                max_tokens=100,
            )

            assert result == '{"analysis": "test", "confidence": 0.85}'
            assert provider.get_provider_name() == "openai"

            # Verify API was called with correct parameters
            mock_client.chat.completions.create.assert_called_once()
            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["model"] == "gpt-4"
            assert call_kwargs["max_tokens"] == 100
            assert len(call_kwargs["messages"]) == 2
            assert call_kwargs["messages"][0]["role"] == "system"
            assert call_kwargs["messages"][1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_openai_empty_response(self):
        """Test handling of empty response from OpenAI."""
        with patch("llm_provider.openai") as mock_openai:
            mock_client = AsyncMock()
            mock_openai.AsyncOpenAI.return_value = mock_client

            # Mock empty response
            mock_response = MagicMock()
            mock_response.choices = []
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

            provider = OpenAIProvider(api_key="sk-test", model="gpt-4")

            with pytest.raises(ValueError, match="empty response"):
                await provider.generate(system="test", user="test")


class TestFactoryFunction:
    """Tests for create_llm_provider factory function."""

    @patch.dict(os.environ, {
        "LLM_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "CLAUDE_MODEL": "claude-test-model"
    })
    def test_create_anthropic_provider(self):
        """Test creating Anthropic provider from env vars."""
        with patch("llm_provider.anthropic"):
            provider = create_llm_provider()
            assert provider.get_provider_name() == "anthropic"
            assert provider.model == "claude-test-model"

    @patch.dict(os.environ, {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test",
        "OPENAI_MODEL": "gpt-4-custom"
    })
    def test_create_openai_provider(self):
        """Test creating OpenAI provider from env vars."""
        with patch("llm_provider.openai"):
            provider = create_llm_provider()
            assert provider.get_provider_name() == "openai"
            assert provider.model == "gpt-4-custom"

    @patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}, clear=True)
    def test_create_provider_missing_api_key(self):
        """Test error when API key is missing."""
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY.*required"):
            create_llm_provider()

    @patch.dict(os.environ, {"LLM_PROVIDER": "invalid_provider"}, clear=True)
    def test_create_provider_invalid_name(self):
        """Test error with invalid provider name."""
        with pytest.raises(ValueError, match="Invalid LLM_PROVIDER"):
            create_llm_provider()

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True)
    def test_default_provider_anthropic(self):
        """Test that Anthropic is the default provider."""
        with patch("llm_provider.anthropic"):
            provider = create_llm_provider()
            assert provider.get_provider_name() == "anthropic"


class TestProviderInfo:
    """Tests for get_provider_info helper."""

    @patch.dict(os.environ, {
        "LLM_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "CLAUDE_MODEL": "claude-custom"
    })
    def test_anthropic_provider_info(self):
        """Test provider info for Anthropic."""
        info = get_provider_info()
        assert info["provider"] == "anthropic"
        assert info["model"] == "claude-custom"
        assert info["api_key_set"] is True

    @patch.dict(os.environ, {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test"
    }, clear=True)
    def test_openai_provider_info(self):
        """Test provider info for OpenAI with default model."""
        info = get_provider_info()
        assert info["provider"] == "openai"
        assert info["model"] == "gpt-4-turbo-2024-04-09"  # default
        assert info["api_key_set"] is True

    @patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}, clear=True)
    def test_provider_info_no_api_key(self):
        """Test provider info when API key is not set."""
        info = get_provider_info()
        assert info["provider"] == "anthropic"
        assert info["api_key_set"] is False


class TestValidateConfig:
    """Tests for validate_provider_config helper."""

    @patch.dict(os.environ, {
        "LLM_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-ant-test"
    })
    def test_valid_anthropic_config(self):
        """Test validation with valid Anthropic config."""
        is_valid, error_msg = validate_provider_config()
        assert is_valid is True
        assert error_msg == ""

    @patch.dict(os.environ, {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test"
    })
    def test_valid_openai_config(self):
        """Test validation with valid OpenAI config."""
        is_valid, error_msg = validate_provider_config()
        assert is_valid is True
        assert error_msg == ""

    @patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}, clear=True)
    def test_invalid_missing_key(self):
        """Test validation fails when API key is missing."""
        is_valid, error_msg = validate_provider_config()
        assert is_valid is False
        assert "ANTHROPIC_API_KEY" in error_msg

    @patch.dict(os.environ, {"LLM_PROVIDER": "invalid"}, clear=True)
    def test_invalid_provider_name(self):
        """Test validation fails with invalid provider name."""
        is_valid, error_msg = validate_provider_config()
        assert is_valid is False
        assert "Invalid LLM_PROVIDER" in error_msg
