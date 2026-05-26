"""Base class for OptiLLM-style reasoning approaches"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Tuple

class ApproachPlugin(ABC):
    """Base class for all reasoning approaches"""
    SLUG: str = ""
    DESCRIPTION: str = ""

    @abstractmethod
    async def run(self, system_prompt: str, initial_query: str,
                 client: Any, model: str, **kwargs) -> Tuple[str, int]:
        """
        Run the approach.
        Returns: (response_text, completion_tokens)
        """
        pass

    def validate_config(self, **kwargs) -> bool:
        return True


# ── Global approach registry ──────────────────────────────────────────
_APPROACHES: Dict[str, ApproachPlugin] = {}


def register_approach(approach: ApproachPlugin) -> None:
    """Register an approach plugin instance."""
    if not approach.SLUG:
        raise ValueError(f"Approach {approach.__class__.__name__} must have a SLUG")
    _APPROACHES[approach.SLUG] = approach


def get_approach(slug: str) -> ApproachPlugin:
    """Get a registered approach by slug."""
    return _APPROACHES.get(slug)


def list_approaches() -> Dict[str, str]:
    """List all registered approaches with slugs and descriptions."""
    return {slug: a.DESCRIPTION for slug, a in _APPROACHES.items()}


def clear_approaches() -> None:
    """Clear all registered approaches (for testing)."""
    _APPROACHES.clear()