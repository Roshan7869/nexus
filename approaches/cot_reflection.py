"""Chain-of-Thought with Reflection - from optillm"""
from .base import ApproachPlugin

class CoTReflectionApproach(ApproachPlugin):
    SLUG = "cot_reflection"
    DESCRIPTION = "Chain-of-thought with self-reflection for improved reasoning"

    async def run(self, system_prompt: str, initial_query: str,
                 client, model: str, **kwargs) -> tuple[str, int]:
        # Step 1: Initial reasoning
        messages = [{"role": "system", "content": system_prompt + "\n\nThink through this step by step."}] if system_prompt else []
        messages.append({"role": "user", "content": initial_query})

        response1 = await client.chat_completions(
            model=model,
            messages=messages,
            **{k: v for k, v in kwargs.items() if k in ["max_tokens", "temperature", "top_p"]}
        )
        reasoning = response1["choices"][0]["message"]["content"]

        # Step 2: Self-reflection
        reflection_messages = messages + [
            {"role": "assistant", "content": reasoning},
            {"role": "user", "content": "Review your reasoning above. Are there any flaws? If so, correct them and provide the final answer."}
        ]

        response2 = await client.chat_completions(
            model=model,
            messages=reflection_messages,
            **{k: v for k, v in kwargs.items() if k in ["max_tokens", "temperature", "top_p"]}
        )
        final = response2["choices"][0]["message"]["content"]
        tokens = response2.get("usage", {}).get("completion_tokens", 0)

        return final, tokens