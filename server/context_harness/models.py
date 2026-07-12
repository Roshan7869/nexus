from pydantic import BaseModel, Field
from typing import Optional


class HarnessQuery(BaseModel):
    query: str = Field(description="Natural language query or context to harness")
    sources: str = Field(default="all", description="Backend sources: 'nexus', 'internet', 'memory', or 'all'")
    max_results: int = Field(default=10, ge=1, le=50)


class HarnessResult(BaseModel):
    source: str
    content: str
    score: float
    metadata: dict


class HarnessResponse(BaseModel):
    query: str
    results: list[HarnessResult]
    source_used: list[str]
    total_count: int


class SearchQuery(BaseModel):
    query: str
    source: str = "all"
    max_results: int = 10


class StoreQuery(BaseModel):
    key: str
    value: str
    source: str = "memory"
    tags: list[str] = []


class ProjectInitRequest(BaseModel):
    project_path: str = Field(default="", description="Path to project root (defaults to CWD)")
    project_type: str = Field(default="auto", description="Force project type: 'auto', 'ai-ml', 'web-dev', 'general-dev', 'research', 'devops', 'trading'")
    layers: list[str] = Field(default=["core", "project"], description="Layers to pre-cache: 'core', 'project', 'domain', 'task'")


class AssetCacheEntry(BaseModel):
    resource_id: int
    resource_name: str
    resource_type: str
    layer: str
    bundle: str
    priority: int
    body_text_snippet: str
    section_count: int


class LayerServeRequest(BaseModel):
    project_type: str
    layer: str = Field(default="core", description="Layer to serve: 'core', 'project', 'domain', 'task'")
    task_context: str = Field(default="", description="Task description for task-layer filtering (only used when layer='task')")
    top_k: int = Field(default=20, ge=1, le=100)
