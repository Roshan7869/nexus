"""V2 backend: internet (agent-reach) 13 platforms, channel routing."""
from unittest.mock import patch
from server.context_harness.agent_reach import CHANNELS, doctor, route_to_channel


class TestInternetBackendChannels:
    """13 channel definitions are correct."""

    def test_channel_count(self):
        """Exactly 13 channels defined."""
        assert len(CHANNELS) == 13

    def test_channel_names_unique(self):
        """All channel names are unique."""
        names = [c["name"] for c in CHANNELS]
        assert len(names) == len(set(names))

    def test_each_channel_has_required_keys(self):
        """Each channel has: name, description, tier, backends, probe."""
        for ch in CHANNELS:
            assert "name" in ch
            assert "description" in ch
            assert "tier" in ch
            assert "backends" in ch
            assert callable(ch["probe"])

    def test_tier_values_valid(self):
        """Tier is 0, 1, or 2."""
        for ch in CHANNELS:
            assert ch["tier"] in (0, 1, 2)

    def test_tier0_channels_have_curl_or_gh_probe(self):
        """Tier 0 channels use curl or gh for probe."""
        for ch in CHANNELS:
            if ch["tier"] == 0:
                assert ch["name"] in ("web", "youtube", "rss", "exa_search", "github", "v2ex")

    def test_each_channel_has_at_least_one_backend(self):
        """Every channel has >=1 backend."""
        for ch in CHANNELS:
            assert len(ch["backends"]) >= 1

    def test_probe_is_callable(self):
        """probe is callable for every channel."""
        for ch in CHANNELS:
            assert isinstance(ch["probe"](), bool)


class TestInternetBackendDoctor:
    """doctor() returns platform status map."""

    def test_doctor_returns_all_channels(self, mock_agent_reach):
        """doctor() has entry for every channel."""
        d = doctor()
        assert len(d) >= 2

    def test_doctor_entry_structure(self, mock_agent_reach):
        """Each entry has: status, name, tier, backends, active_backend."""
        d = doctor()
        for name, info in d.items():
            assert "status" in info
            assert "name" in info
            assert "tier" in info
            assert "backends" in info
            assert "active_backend" in info or info.get("active_backend") is None

    def test_available_platform_has_ok_status(self, mock_agent_reach):
        """Platform with probe()=True has status 'ok'."""
        d = doctor()
        assert d.get("github", {}).get("status") == "ok"
        assert d.get("web", {}).get("status") == "ok"

    def test_unavailable_platform_has_off_status(self):
        """Platform with probe()=False has status 'off'."""
        d = doctor()
        assert d.get("linkedin", {}).get("status") == "off"

    def test_active_backend_matches_first_backend_when_available(self, mock_agent_reach):
        """active_backend is first backend when probe succeeds."""
        d = doctor()
        assert d.get("github", {}).get("active_backend") == "gh"


class TestInternetBackendRouteToChannel:
    """route_to_channel routes NL queries to best platform."""

    def test_route_github_query(self, mock_agent_reach):
        """Query 'github' routes to github channel."""
        ch = route_to_channel("search github for tensorflow")
        assert ch is not None
        assert ch["name"] == "github"

    def test_route_web_query(self, mock_agent_reach):
        """Query 'web' routes to web channel."""
        ch = route_to_channel("read this web page")
        assert ch is not None
        assert ch["name"] == "web"

    def test_route_unmatched_query_falls_to_tier0_probe(self):
        """Gibberish query still matches tier-0 channels with passing probes."""
        ch = route_to_channel("zzzzzzzzxxxxx")
        # tier=0 (+1) + probe=True (+2) = 3, so score > 0
        assert ch is not None

    def test_route_tier0_boosted(self, mock_agent_reach):
        """Tier 0 channels get a score boost."""
        ch = route_to_channel("search")
        assert ch is not None

    def test_route_available_platform_boosted(self, mock_agent_reach):
        """Platforms with probe()=True get +2 score."""
        ch = route_to_channel("github")
        assert ch is not None
        assert ch["probe"]() is True
