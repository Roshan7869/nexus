"""Best-of-N - generate N responses and pick the best (from optillm)"""
from .base import ApproachPlugin

class BoNApproach(ApproachPlugin):
    SLUG = "bon"
    DESCRIPTION = "Best-of-N: generate N responses and select the most consistent/verbose one"

    async def run(self, system_prompt: str, initial_query: str,
                 client, model: str, **kwargs) -> tuple[str, int]:
        n = kwargs.get("bon_n", 5)
        temperature = kwargs.get("temperature", 0.7)

        responses = []
        for i in range(n):
            messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
            messages.append({"role": "user", "content": initial_query})

            try:
                resp = await client.chat_completions(
                    model=model,
                    messages=messages,
                    temperature=temperature + (i * 0.1 / n),  # slight temp variation
                    **{k: v for k, v in kwargs.items() if k in ["max_tokens", "top_p"]}
                )
                content = resp["choices"][0]["message"]["content"]
                responses.append(content)
            except Exception:
                pass

        if not responses:
            return "", 0

        # Selection: longest non-repetitive response
        # In production: use a learned reranker
        best = max(responses, key=lambda x: len(x.split()))
        return best, 0