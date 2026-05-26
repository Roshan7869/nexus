#!/usr/bin/env python3
"""Nexus Core Configuration System"""
import os, json, yaml, logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class ProviderModel:
    def __init__(self, name: str, role: str):
        self.name = name
        self.role = role  # reasoning, coding, agentic, general

class ProviderConfig:
    def __init__(self, base_url: str, api_key: Optional[str] = None,
                 timeout: int = 120, models: Optional[List[ProviderModel]] = None):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.models = models or []

class RoutingConfig:
    def __init__(self, enabled: bool = False, learn_slowly: bool = True,
                 benchmarks: Optional[List[str]] = None,
                 min_samples_before_route: int = 50):
        self.enabled = enabled
        self.learn_slowly = learn_slowly
        self.benchmarks = benchmarks or ["mmlu", "humaneval", "math500"]
        self.min_samples_before_route = min_samples_before_route

class IDEConnection:
    def __init__(self, enabled: bool = False, mcp_port: Optional[int] = None,
                 api_base: Optional[str] = None):
        self.enabled = enabled
        self.mcp_port = mcp_port
        self.api_base = api_base

class ApproachConfig:
    def __init__(self, enabled: bool = False, default: str = "none",
                 available: Optional[List[str]] = None):
        self.enabled = enabled
        self.default = default
        self.available = available or ["none"]


class LearningConfig:
    """Centralized learning feature flags — single source of truth.
    
    All NEXUS modules (unified.py, learning.py, router.py) MUST import
    from this class instead of reading env vars directly.
    
    Resolution order: explicit value > config.learning.YAML > env var > default.
    """
    def __init__(
        self,
        thompson_routing: Optional[bool] = None,
        cluster_routing: Optional[bool] = None,
        reflection_enabled: Optional[bool] = None,
        mcp_sync: Optional[bool] = None,
        auto_invoke_enabled: Optional[bool] = None,
        min_samples_thompson: int = 10,
        degraded_threshold: float = 0.40,
    ):
        self.thompson_routing = thompson_routing
        self.cluster_routing = cluster_routing
        self.reflection_enabled = reflection_enabled
        self.mcp_sync = mcp_sync
        self.auto_invoke_enabled = auto_invoke_enabled
        self.min_samples_thompson = min_samples_thompson
        self.degraded_threshold = degraded_threshold

    def is_thompson_routing(self) -> bool:
        if self.thompson_routing is not None:
            return self.thompson_routing
        return os.environ.get("NEXUS_THOMPSON_ROUTING", "true").lower() == "true"

    def is_cluster_routing(self) -> bool:
        if self.cluster_routing is not None:
            return self.cluster_routing
        return os.environ.get("NEXUS_CLUSTER_ROUTING", "false").lower() == "true"

    def is_reflection_enabled(self) -> bool:
        if self.reflection_enabled is not None:
            return self.reflection_enabled
        return os.environ.get("NEXUS_REFLECTION", "false").lower() == "true"

    def is_mcp_sync(self) -> bool:
        if self.mcp_sync is not None:
            return self.mcp_sync
        return os.environ.get("NEXUS_MCP_SYNC", "false").lower() == "true"

    def is_auto_invoke_enabled(self) -> bool:
        if self.auto_invoke_enabled is not None:
            return self.auto_invoke_enabled
        return os.environ.get("NEXUS_AUTO_INVOKE",
            os.environ.get("NEXUS_REFLECTION", "false")).lower() == "true"

    def to_dict(self) -> dict:
        return {
            "thompson_routing": self.is_thompson_routing(),
            "cluster_routing": self.is_cluster_routing(),
            "reflection_enabled": self.is_reflection_enabled(),
            "mcp_sync": self.is_mcp_sync(),
            "auto_invoke_enabled": self.is_auto_invoke_enabled(),
            "min_samples_thompson": self.min_samples_thompson,
            "degraded_threshold": self.degraded_threshold,
        }

    @classmethod
    def from_yaml_data(cls, data: dict) -> "LearningConfig":
        if not data:
            return cls()
        return cls(
            thompson_routing=data.get("thompson_routing"),
            cluster_routing=data.get("cluster_routing"),
            reflection_enabled=data.get("reflection_enabled"),
            mcp_sync=data.get("mcp_sync"),
            auto_invoke_enabled=data.get("auto_invoke_enabled"),
            min_samples_thompson=data.get("min_samples_thompson", 10),
            degraded_threshold=data.get("degraded_threshold", 0.40),
        )


# Global singleton for modules that can't use dependency injection.
# Modules should import and use this directly.
_GLOBAL_LEARNING_CONFIG: Optional[LearningConfig] = None

def get_learning_config() -> LearningConfig:
    """Get the global learning config singleton."""
    global _GLOBAL_LEARNING_CONFIG
    if _GLOBAL_LEARNING_CONFIG is None:
        _GLOBAL_LEARNING_CONFIG = LearningConfig()
    return _GLOBAL_LEARNING_CONFIG

def set_learning_config(cfg: LearningConfig) -> None:
    """Set the global learning config (e.g. from YAML at startup)."""
    global _GLOBAL_LEARNING_CONFIG
    _GLOBAL_LEARNING_CONFIG = cfg


class NexusConfig:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        log_level: str = "info",
        providers: Optional[Dict[str, ProviderConfig]] = None,
        routing: Optional[RoutingConfig] = None,
        ide_connections: Optional[Dict[str, IDEConnection]] = None,
        approaches: Optional[ApproachConfig] = None,
        learning: Optional[LearningConfig] = None,
        optillm_api_key: str = "",
        ssl_verify: bool = True,
        api_key: str = ""
    ):
        self.host = host
        self.port = port
        self.log_level = log_level
        self.providers = providers or {}
        self.routing = routing or RoutingConfig()
        self.ide_connections = ide_connections or {}
        self.approaches = approaches or ApproachConfig()
        self.learning = learning or LearningConfig()
        self.optillm_api_key = optillm_api_key
        self.ssl_verify = ssl_verify
        self.api_key = api_key

    @staticmethod
    def _expand_env_vars(obj):
        if isinstance(obj, str):
            return os.path.expandvars(obj)
        elif isinstance(obj, dict):
            return {k: NexusConfig._expand_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [NexusConfig._expand_env_vars(i) for i in obj]
        return obj

    @classmethod
    def from_yaml(cls, path: str) -> "NexusConfig":
        p = Path(path).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"Config not found: {path}")
        with open(p) as f:
            data = yaml.safe_load(f)
        data = cls._expand_env_vars(data)

        providers = {}
        for name, pdata in data.get("providers", {}).items():
            models = [ProviderModel(m["name"], m["role"]) for m in pdata.get("models", [])]
            providers[name] = ProviderConfig(
                base_url=pdata["base_url"],
                api_key=pdata.get("api_key"),
                timeout=pdata.get("timeout", 120),
                models=models
            )

        routing_data = data.get("routing", {})
        routing = RoutingConfig(
            enabled=routing_data.get("enabled", False),
            learn_slowly=routing_data.get("learn_slowly", True),
            benchmarks=routing_data.get("benchmarks", ["mmlu", "humaneval", "math500"]),
            min_samples_before_route=routing_data.get("min_samples_before_route", 50)
        )

        ide_conns = {}
        for name, ide in data.get("ide_connections", {}).items():
            ide_conns[name] = IDEConnection(
                enabled=ide.get("enabled", False),
                mcp_port=ide.get("mcp_port"),
                api_base=ide.get("api_base")
            )

        approach_data = data.get("approaches", {})
        approaches = ApproachConfig(
            enabled=approach_data.get("enabled", False),
            default=approach_data.get("default", "none"),
            available=approach_data.get("available", ["none"])
        )

        learning_data = data.get("learning", {})
        learning = LearningConfig.from_yaml_data(learning_data)

        cfg = cls(
            host=data.get("host", "127.0.0.1"),
            port=data.get("port", 8000),
            log_level=data.get("log_level", "info"),
            providers=providers,
            routing=routing,
            ide_connections=ide_conns,
            approaches=approaches,
            learning=learning,
            optillm_api_key=data.get("optillm_api_key", ""),
            ssl_verify=data.get("ssl_verify", True),
            api_key=data.get("api_key", "")
        )

        # Apply learning config globally so modules that import the
        # singleton (get_learning_config()) see the YAML overrides.
        set_learning_config(cfg.learning)

        return cfg

    def to_yaml(self, path: str):
        def _serialize(obj):
            if hasattr(obj, "__dict__"):
                return {k: _serialize(v) for k, v in obj.__dict__.items()}
            elif isinstance(obj, list):
                return [_serialize(i) for i in obj]
            return obj
        with open(path, "w") as f:
            yaml.dump(_serialize(self), f, default_flow_style=False)

    def get_model_provider(self, model_name: str) -> Optional[tuple]:
        """Find provider and actual model name from logical model name."""
        for pname, pconfig in self.providers.items():
            for model in pconfig.models:
                if model.name == model_name or f"{pname}/{model.name}" == model_name:
                    return pname, model.name, model.role
        return None

    def get_all_models(self) -> List[tuple]:
        """Return list of (provider, model_name, role) tuples."""
        result = []
        for pname, pconfig in self.providers.items():
            for model in pconfig.models:
                result.append((pname, model.name, model.role))
        return result