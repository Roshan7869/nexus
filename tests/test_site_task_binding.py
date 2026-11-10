"""P1.1 — per-task site binding loader (server.site_registry.get_task_sites)."""
from server.site_registry import get_task_sites

REG = {
    "projects": {
        "kaari": {
            "sites": [{"url": "https://project.example/a"}],
            "tasks": {
                "coding": {"sites": [{"url": "https://docs.example/upload"}]},
                "design": {"sites": []},          # empty → fall back to project sites
                "broken": "not-a-dict",           # malformed → fall back
            },
        },
        "plain": {"sites": [{"url": "https://plain.example/x"}]},
        "bad": "not-a-dict",
    }
}


def test_task_hit_returns_task_sites_only():
    assert get_task_sites("kaari", "coding", registry=REG) == ["https://docs.example/upload"]


def test_task_miss_falls_back_to_project_sites():
    assert get_task_sites("kaari", "research", registry=REG) == ["https://project.example/a"]


def test_empty_task_list_falls_back_to_project_sites():
    assert get_task_sites("kaari", "design", registry=REG) == ["https://project.example/a"]


def test_malformed_task_entry_falls_back():
    assert get_task_sites("kaari", "broken", registry=REG) == ["https://project.example/a"]


def test_project_without_tasks_uses_project_sites():
    assert get_task_sites("plain", "coding", registry=REG) == ["https://plain.example/x"]


def test_unknown_project_returns_empty():
    assert get_task_sites("nope", "coding", registry=REG) == []


def test_malformed_project_returns_empty():
    assert get_task_sites("bad", "coding", registry=REG) == []


def test_empty_or_none_inputs_do_not_raise():
    assert get_task_sites("", "", registry=REG) == []
    assert get_task_sites("kaari", None, registry=REG) == ["https://project.example/a"]  # type: ignore[arg-type]
    assert get_task_sites("kaari", "coding", registry={}) == []


def test_case_insensitive_lookup():
    assert get_task_sites("KAARI", "CODING", registry=REG) == ["https://docs.example/upload"]
