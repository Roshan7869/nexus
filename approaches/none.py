"""None approach - direct passthrough to model"""
from .base import ApproachPlugin

class NoneApproach(ApproachPlugin):
    SLUG = "none"
    DESCRIPTION = "Direct pass-through without optimization"

    async def run(self, system_prompt: str, initial_query: str,
                 client, model: str, **kwargs) -> tuple[str, int]:
        messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
        messages.append({"role": "user", "content": initial_query})

        response = await client.chat_completions(
            model=model,
            messages=messages,
            **{k: v for k, v in kwargs.items() if k in ["max_tokens", "temperature", "top_p"]}
        )

        content = ""
        if response.get("choices"):
            content = response["choices"][0]["message"]["content"]
        tokens = response.get("usage", {}).get("completion_tokens", 0)

        return content, tokens