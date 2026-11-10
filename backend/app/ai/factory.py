from __future__ import annotations
from typing import Dict, Any
from .base import BaseAIProvider
from .providers.deepseek import DeepSeekProvider
from .providers.gemini import GeminiProvider


class AIProviderFactory:
    """Factory to create AI provider instances based on configuration."""

    _providers = {
        "deepseek": DeepSeekProvider,
        "gemini": GeminiProvider,
        # Add more providers here as they're implemented:
        # "openai": OpenAIProvider,
    }

    @classmethod
    def create(cls, provider_name: str, api_key: str | None = None, **kwargs) -> BaseAIProvider:
        """
        Create an AI provider instance.
        
        Args:
            provider_name: Name of the provider (e.g., 'deepseek', 'openai')
            api_key: API key for the provider
            **kwargs: Additional provider-specific configuration
        
        Returns:
            Instance of the requested provider
        
        Raises:
            ValueError: If provider_name is not supported
        """
        provider_class = cls._providers.get(provider_name.lower())
        if not provider_class:
            raise ValueError(
                f"Unknown provider: {provider_name}. "
                f"Supported: {', '.join(cls._providers.keys())}"
            )
        return provider_class(api_key=api_key, **kwargs)

    @classmethod
    def get_supported_providers(cls) -> list[str]:
        """Return list of supported provider names."""
        return list(cls._providers.keys())
