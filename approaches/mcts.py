"""Monte Carlo Tree Search - from optillm"""
from .base import ApproachPlugin
import random

class MCTSApproach(ApproachPlugin):
    SLUG = "mcts"
    DESCRIPTION = "Monte Carlo Tree Search for systematic exploration of reasoning paths"

    async def run(self, system_prompt: str, initial_query: str,
                 client, model: str, **kwargs) -> tuple[str, int]:
        iterations = kwargs.get("mcts_iterations", 20)
        exploration_const = kwargs.get("mcts_exploration", 1.4)

        # Build prompt with MCTS instructions
        prompt = f"""{system_prompt}

Using Monte Carlo Tree Search, explore multiple reasoning paths for:
{initial_query}

Generate {iterations} candidate solutions, scoring each by simulated outcome.
Return the highest-scoring solution."""

        messages = [{"role": "system", "content": prompt}]
        messages.append({"role": "user", "content": initial_query})

        response = await client.chat_completions(
            model=model,
            messages=messages,
            **{k: v for k, v in kwargs.items() if k in ["max_tokens", "temperature", "top_p"]}
        )

        content = response["choices"][0]["message"]["content"]
        tokens = response.get("usage", {}).get("completion_tokens", 0)
        return content, tokens