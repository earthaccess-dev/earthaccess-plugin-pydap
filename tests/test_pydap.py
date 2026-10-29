import sys
import types

import pytest
from earthaccess_plugin_spec import Asset, Capability, UnsupportedCollectionError
from earthaccess_plugin_spec.results import GranuleResultSet
from earthaccess_plugin_spec.testing import fake_context

from earthaccess_pydap import NoOPeNDAPUrlsError, PyDAPPlugin
from earthaccess_pydap.plugin import with_constraint, with_scheme

from .fixtures import PyDAPFixtures, make_granule


def fake_pydap(monkeypatch):
    """Inject a fake ``pydap.client.open_url`` and return the call list."""

    calls: list[dict] = []

    def open_url(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return {"url": url, **kwargs}

    client = types.ModuleType("pydap.client")
    client.open_url = open_url
    package = types.ModuleType("pydap")
    package.client = client
    monkeypatch.setitem(sys.modules, "pydap", package)
    monkeypatch.setitem(sys.modules, "pydap.client", client)
    return calls


class TestUrlExtraction:
    def test_basic(self):
        from earthaccess_pydap.plugin import opendap_urls

        rs = GranuleResultSet(
            [make_granule(urls=["https://e.org/a.dap", "https://e.org/b.dap"])]
        )
        assert opendap_urls(rs) == ["https://e.org/a.dap", "https://e.org/b.dap"]

    def test_filter_and_dedupe(self):
        from earthaccess_pydap.plugin import opendap_urls

        rs = GranuleResultSet(
            [make_granule(urls=["https://e.org/a.dap", "https://e.org/b.dap"])]
        )
        assert opendap_urls(rs, needle="a.dap") == ["https://e.org/a.dap"]

    def test_subtype_match_and_ignores_non_opendap(self):
        from earthaccess_pydap.plugin import opendap_urls

        granule = make_granule()
        granule["umm"]["RelatedUrls"] = [
            {"Type": "GET DATA", "Subtype": "OPeNDAP", "URL": "https://e.org/x"},
            {"Type": "GET DATA", "URL": "https://e.org/y"},
        ]
        assert opendap_urls(GranuleResultSet([granule])) == ["https://e.org/x"]


class TestSchemeAndConstraint:
    @pytest.mark.parametrize(
        ("url", "protocol", "expected"),
        [
            ("https://e.org/a", "dap4", "dap4://e.org/a"),
            ("https://e.org/a", "dap2", "dap2://e.org/a"),
            ("dap4://e.org/a", "dap2", "dap4://e.org/a"),
        ],
    )
    def test_scheme(self, url, protocol, expected):
        assert with_scheme(url, protocol) == expected

    def test_constraint_empty(self):
        assert with_constraint("dap4://e/a", []) == "dap4://e/a"

    def test_constraint_selects_variables(self):
        url = with_constraint("dap4://e/a", ["lon", "lat", "time"])
        assert url == "dap4://e/a?dap4.ce=/lon;/lat;/time"


class TestOptions:
    def test_defaults(self):
        options = PyDAPPlugin.Options()
        assert options.client == "xarray"
        assert options.protocol == "dap4"
        assert options.variables == []

    def test_unknown_option_rejected(self):
        plugin = PyDAPPlugin(fake_context())
        with pytest.raises(TypeError):
            plugin.request(PyDAPFixtures().result_set, nope=1)


class TestRequest:
    def test_xarray_client_yields_asset_urls(self):
        plugin = PyDAPPlugin(fake_context())
        assets = plugin.request(PyDAPFixtures().result_set).results()
        assert assets == [
            Asset(href="dap4://example.org/g1.dap", access="opendap", auth="edl")
        ]

    def test_variables_become_a_constraint(self):
        plugin = PyDAPPlugin(fake_context())
        assets = plugin.request(
            PyDAPFixtures().result_set, variables=["lon", "analysed_sst"]
        ).results()
        assert assets[0].href == (
            "dap4://example.org/g1.dap?dap4.ce=/lon;/analysed_sst"
        )

    def test_capabilities_asset_kind(self):
        plugin = PyDAPPlugin(fake_context())
        job = plugin.request(PyDAPFixtures().result_set)
        assert job.output == "assets"

    def test_pydap_client_yields_native_datasets(self, monkeypatch):
        calls = fake_pydap(monkeypatch)
        plugin = PyDAPPlugin(fake_context())
        job = plugin.request(
            PyDAPFixtures().result_set, client="pydap", variables=["lat"]
        )
        assert job.output == "opendap"
        datasets = job.results()
        assert datasets[0]["url"] == "dap4://example.org/g1.dap?dap4.ce=/lat"
        assert datasets[0]["protocol"] == "dap4"
        assert calls[0]["url"] == "dap4://example.org/g1.dap?dap4.ce=/lat"

    def test_no_urls_raises(self):
        plugin = PyDAPPlugin(fake_context())
        granule = make_granule()
        granule["umm"]["RelatedUrls"] = [{"Type": "GET DATA", "URL": "https://e.org/y"}]
        # check=False bypasses the capability gate and reaches the request.
        with pytest.raises(NoOPeNDAPUrlsError):
            plugin.request(GranuleResultSet([granule]), check=False).results()

    def test_session_is_requested_up_front(self):
        ctx = fake_context()
        plugin = PyDAPPlugin(ctx)
        plugin.request(PyDAPFixtures().result_set)
        assert ctx.auth.session_calls == 1


def test_entry_point_registered():
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group="earthaccess.plugins")}
    assert "pydap" in names


class TestSupport:
    def test_supported_with_operations(self):
        plugin = PyDAPPlugin(fake_context())
        capabilities = plugin.capabilities(PyDAPFixtures().result_set)
        assert capabilities.ok is True
        assert capabilities.operations == {
            Capability.LAZY_READ,
            Capability.SUBSET_VARIABLE,
        }

    def test_short_name_samples_cmr_for_opendap_links(self, monkeypatch):
        import earthaccess_pydap.plugin as plugin_mod

        monkeypatch.setattr(plugin_mod, "_sample_granule", lambda name: make_granule())
        plugin = PyDAPPlugin(fake_context())
        capabilities = plugin.capabilities("MUR-JPL-L4-GLOB-v4.1")
        assert capabilities.ok is True
        assert capabilities.operations == {
            Capability.LAZY_READ,
            Capability.SUBSET_VARIABLE,
        }

    def test_short_name_without_links_is_unknown(self, monkeypatch):
        import earthaccess_pydap.plugin as plugin_mod

        monkeypatch.setattr(
            plugin_mod, "_sample_granule", lambda name: make_granule(urls=[])
        )
        capabilities = PyDAPPlugin(fake_context()).capabilities(
            "MUR-JPL-L4-GLOB-v4.1"
        )
        assert capabilities.ok is None
        assert "no OPeNDAP link" in capabilities.reason

    def test_short_name_when_cmr_is_unavailable_is_unknown(self, monkeypatch):
        import earthaccess_pydap.plugin as plugin_mod

        monkeypatch.setattr(plugin_mod, "_sample_granule", lambda name: None)
        capabilities = PyDAPPlugin(fake_context()).capabilities(
            "MUR-JPL-L4-GLOB-v4.1"
        )
        assert capabilities.ok is None
        assert "CMR" in capabilities.reason

    def test_unsupported_without_links(self):
        plugin = PyDAPPlugin(fake_context())
        support = plugin.capabilities(
            GranuleResultSet([make_granule(collection_id="C-X", urls=[])])
        )
        assert support.ok is False
        assert "OPeNDAP" in support.reason

    def test_request_raises_before_backend(self):
        from unittest import mock

        import earthaccess_pydap.plugin as plugin_mod

        plugin = PyDAPPlugin(fake_context())
        unsupported = GranuleResultSet([make_granule(collection_id="C-X", urls=[])])
        with (
            mock.patch.object(plugin_mod, "_native_datasets") as native_mock,
            pytest.raises(UnsupportedCollectionError),
        ):
            plugin.request(unsupported)
        native_mock.assert_not_called()
