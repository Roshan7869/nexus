"""Mixture of Agents - multiple models vote on best response (from optillm)"""
from .base import ApproachPlugin
import json

class MoAApproach(ApproachPlugin):
    SLUG = "moa"
    DESCRIPTION = "Mixture of Agents - multiple models generate responses, aggregator picks best"

    async def run(self, system_prompt: str, initial_query: str,
                 client, model: str, **kwargs) -> tuple[str, int]:
        # Get list of available models from the same provider
        # In practice, MoA calls multiple distinct models and aggregates
        models_to_try = kwargs.get("moa_models", [model])
        max_models = kwargs.get("max_moa_models", 3)

        responses = []
        for m in models_to_try[:max_models]:
            messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
            messages.append({"role": "user", "content": initial_query})

            try:
                resp = await client.chat_completions(
                    model=m,
                    messages=messages,
                    **{k: v for k, v in kwargs.items() if k in ["max_tokens", "temperature", "top_p"]}
                )
                content = resp["choices"][0]["message"]["content"]
                responses.append((m, content))
            except Exception:
                pass

        if not responses:
            return "", 0

        # If only one model, return it
        if len(responses) == 1:
            return responses[0][1], 0

        # Aggregator: pick longest response as "best" (simple heuristic)
        # In production: use a learned aggregator model
        best = max(responses, key=lambda x: len(x[1]))
        return best[1], 0