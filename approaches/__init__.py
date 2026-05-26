#!/usr/bin/env python3
"""
Nexus OptiLLM-style Approach Plugins
Copied/adapted from optillm for reasoning optimization.
Enable via: approaches.enabled: true in nexus.yaml

Each approach: async def run(system_prompt, initial_query, client, model, **kwargs)
"""
from .base import ApproachPlugin, register_approach

# Re-export all available approaches
from .none import NoneApproach
from .cot_reflection import CoTReflectionApproach
from .moa import MoAApproach
from .mcts import MCTSApproach
from .bon import BoNApproach

__all__ = [
    "NoneApproach",
    "CoTReflectionApproach",
    "MoAApproach",
    "MCTSApproach",
    "BoNApproach",
]