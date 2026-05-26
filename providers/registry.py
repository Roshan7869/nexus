#!/usr/bin/env python3
"""Nexus Provider Registry - Multi-provider abstraction"""
import os, logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

class BaseProvider:
    def __init__(self, name: str, base_url: str, api_key: Optional[str] = None, timeout: int = 120):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict[str, Any]:
        raise NotImplementedError

    async def list_models(self) -> List[str]:
        raise NotImplementedError


class OllamaCloudProvider(BaseProvider):
    """Ollama Cloud - cloud.ollama.com API"""

    def __init__(self, base_url: str = "https://cloud.ollama.com", api_key: Optional[str] = None, timeout: int = 120):
        # Normalize: strip trailing /v1 so we can append it consistently
        base_url = base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        super().__init__("ollama_cloud", base_url, api_key, timeout)

    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict[str, Any]:
        import httpx
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(verify=True, timeout=self.timeout) as client:
            # Use OpenAI-compatible endpoint for consistent response format
            resp = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    **{k: v for k, v in kwargs.items()
                       if k in ["stream", "max_tokens", "temperature", "top_p", "stop"]}
                },
                headers=headers
            )
            resp.raise_for_status()
            data = resp.json()

            # If provider returns OpenAI format already, pass through
            if "choices" in data and isinstance(data.get("choices"), list):
                return data

            # Fallback: Ollama native format → OpenAI format
            return {
                "id": f"chatcmpl-{data.get('created', 0)}",
                "object": "chat.completion",
                "created": data.get("created", 0),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": data.get("message", {}).get("content", "")
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0),
                    "total_tokens": (data.get("prompt_eval_count", 0) + data.get("eval_count", 0))
                }
            }

    async def list_models(self) -> List[str]:
        import httpx
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(verify=True, timeout=self.timeout) as client:
            try:
                # Try OpenAI-compatible /v1/models first
                resp = await client.get(f"{self.base_url}/v1/models", headers=headers)
                resp.raise_for_status()
                data = resp.json()
                # OpenAI /v1/models returns {"data": [{"id": "...", ...}]}
                if "data" in data:
                    return [m["id"] for m in data["data"]]
                return [m["name"] if isinstance(m, dict) and "name" in m else str(m) for m in data.get("models", [])]
            except Exception:
                # Fallback: try Ollama native /api/tags
                try:
                    resp = await client.get(f"{self.base_url}/api/tags", headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    return [m["name"] for m in data.get("models", [])]
                except Exception:
                    return []


class NvidiaNimProvider(BaseProvider):
    """NVIDIA NIM - integrate.api.nvidia.com"""

    def __init__(self, base_url: str = "https://integrate.api.nvidia.com/v1",
                 api_key: Optional[str] = None, timeout: int = 120):
        super().__init__("nvidia_nim", base_url, api_key, timeout)

    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict[str, Any]:
        import httpx
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient(verify=True, timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    **{k: v for k, v in kwargs.items()
                       if k in ["stream", "max_tokens", "temperature", "top_p", "stop"]}
                },
                headers=headers
            )
            resp.raise_for_status()
            return resp.json()

    async def list_models(self) -> List[str]:
        # NIM has predefined model catalog
        return [
            "meta/llama-3.1-405b-instruct",
            "meta/llama-3.1-70b-instruct",
            "meta/llama-3.1-8b-instruct",
            "nvidia/llama-3.1-nemotron-70b-instruct",
            "nvidia/nemotron-4-340b-instruct",
            "mistralai/mixtral-8x7b-instruct-v0.1",
            "google/gemma-2-27b-it",
            "snowflake/arctic",
        ]


class LiteLLMProvider(BaseProvider):
    """LiteLLM proxy as provider - wraps LiteLLM SDK"""
    # Import litellm lazily to avoid hard dependency
    _litellm = None

    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: int = 120):
        super().__init__("litellm", base_url, api_key, timeout)

    @property
    def litellm(self):
        if self._litellm is None:
            import litellm
            self._litellm = litellm
        return self._litellm

    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict[str, Any]:
        from litellm import completion
        response = await completion(
            model=model,
            messages=messages,
            api_base=self.base_url,
            api_key=self.api_key or "dummy",
            **kwargs
        )
        return response.to_dict() if hasattr(response, "to_dict") else response

    async def list_models(self) -> List[str]:
        from litellm import get_valid_models
        try:
            return get_valid_models(api_base=self.base_url)
        except Exception:
            return []


PROVIDER_CLASSES = {
    "ollama_cloud": OllamaCloudProvider,
    "nvidia_nim": NvidiaNimProvider,
    "litellm": LiteLLMProvider,
}


def get_provider(name: str, config: Dict[str, Any]) -> BaseProvider:
    cls = PROVIDER_CLASSES.get(name)
    if not cls:
        raise ValueError(f"Unknown provider: {name}. Available: {list(PROVIDER_CLASSES.keys())}")

    api_key = os.path.expandvars(config.get("api_key", "") or "")

    if name == "ollama_cloud":
        return cls(base_url=config.get("base_url", "https://cloud.ollama.com"), api_key=api_key, timeout=config.get("timeout", 120))
    elif name == "nvidia_nim":
        return cls(base_url=config.get("base_url", "https://integrate.api.nvidia.com/v1"), api_key=api_key, timeout=config.get("timeout", 120))
    elif name == "litellm":
        return cls(base_url=config.get("base_url", ""), api_key=api_key, timeout=config.get("timeout", 120))


def build_provider_map(config) -> Dict[str, BaseProvider]:
    providers = {}
    for name, pconfig in config.providers.items():
        try:
            providers[name] = get_provider(name, pconfig.__dict__ if hasattr(pconfig, '__dict__') else vars(pconfig))
        except Exception as e:
            logger.error(f"Failed to init provider '{name}': {e}")
    return providers