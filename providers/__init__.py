"""
NEXUS Providers Package
~~~~~~~~~~~~~~~~~~~~~~~~
Multi-provider abstraction layer:
  - OllamaCloudProvider   (cloud.ollama.com/chat)
  - NvidiaNimProvider    (integrate.api.nvidia.com/v1)
  - LiteLLMProvider     (unified OpenAI-compatible wrapper)

Usage:
  from providers.registry import build_provider_map, get_provider
"""
from providers.registry import (
    BaseProvider,
    OllamaCloudProvider,
    NvidiaNimProvider,
    LiteLLMProvider,
    build_provider_map,
    get_provider,
)

__all__ = [
    "BaseProvider",
    "OllamaCloudProvider",
    "NvidiaNimProvider",
    "LiteLLMProvider",
    "build_provider_map",
    "get_provider",
]
