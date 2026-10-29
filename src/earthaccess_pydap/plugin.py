"""The PyDAP plugin: OPeNDAP access expressed as a request.

``pydap.request(results, client="xarray")`` returns a completed job whose
``results()`` is a list of :class:`Asset` objects: the OPeNDAP DAP4 URLs with a
constraint expression for the requested ``variables``. Open them with xarray
(``engine="pydap"``) and whatever kwargs your data needs -- the plugin does not
open OPeNDAP itself, because that often needs tuning.

``pydap.request(results, client="pydap")`` returns the native pydap datasets
from ``pydap.open_url``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from earthaccess_plugin_spec import Asset, Capabilities, Capability, PluginBase
from earthaccess_plugin_spec.granules import peek, related_urls

from .errors import NoOPeNDAPUrlsError

__all__ = ["PyDAPPlugin"]

# OPeNDAP's intrinsic capabilities (any DAP endpoint advertises these).
_OPENDAP_OPERATIONS = frozenset(
    {
        Capability.LAZY_READ,
        Capability.SUBSET_VARIABLE,
    }
)


class PyDAPPlugin(PluginBase):
    name = "pydap"
    needs = frozenset({"umm"})
    auth = "edl"
    output = "assets"

    @dataclass
    class Options:
        # "xarray": assets (OPeNDAP URLs with a variable constraint) to open with
        # xarray. "pydap": the native pydap datasets from open_url.
        client: Literal["xarray", "pydap"] = "xarray"
        protocol: Literal["dap4", "dap2"] = "dap4"
        variables: list[str] = field(default_factory=list)  # DAP4 constraint
        url_filter: str | None = None  # substring match on OPeNDAP hrefs

    def _request(self, results: Any, options: Options) -> Any:
        urls = [
            with_scheme(url, options.protocol)
            for url in opendap_urls(results, options.url_filter)
        ]
        if not urls:
            raise NoOPeNDAPUrlsError(
                "no OPeNDAP links in the UMM for "
                f"{getattr(results, 'short_name', '') or 'these granules'}"
            )
        constrained = [with_constraint(url, options.variables) for url in urls]
        # Login happens here, on the caller's thread, not in the worker.
        session = self._ctx.auth.session()

        if options.client == "pydap":
            return self.job(
                lambda: _native_datasets(constrained, session, options.protocol),
                output="opendap",
            )
        return self.job(
            lambda: [
                Asset(href=url, access="opendap", auth="edl") for url in constrained
            ]
        )

    def _supports(self, results: Any) -> Capabilities:
        # Granule links are better evidence than a collection association,
        # because the data opened is the granule URL.
        if peek(results) is not None:
            if opendap_urls(results):
                return Capabilities(True, operations=_OPENDAP_OPERATIONS)
            return Capabilities(
                False, reason="none of these granules has an OPeNDAP link"
            )

        short_name = getattr(results, "short_name", "") or ""
        if not short_name:
            return Capabilities(
                None, reason="supply search results or a collection short name"
            )
        sample = _sample_granule(short_name)
        if sample is None:
            return Capabilities(
                None,
                reason=f"could not sample a granule for {short_name} in CMR",
            )
        if opendap_urls([sample]):
            return Capabilities(True, operations=_OPENDAP_OPERATIONS)
        # One sampled granule is weak evidence: never hard-block on it.
        return Capabilities(
            None,
            reason=f"the sampled {short_name} granule has no OPeNDAP link",
        )


def _sample_granule(short_name: str) -> Any:
    """Fetch one granule's UMM from CMR to check its links.

    Only used when ``capabilities()`` is given a short name instead of search
    results. Returns ``None`` when CMR cannot be queried, which the caller maps
    to "unknown" rather than blocking a request.
    """

    try:
        import earthaccess

        found = earthaccess.search_data(short_name=short_name, count=1)
    except Exception:  # noqa: BLE001 - a capability check must never raise
        return None
    return peek(found)


def _native_datasets(urls: list[str], session: Any, protocol: str) -> list[Any]:
    import pydap.client

    return [
        pydap.client.open_url(url, protocol=protocol, session=session) for url in urls
    ]


def opendap_urls(results: Any, needle: str | None = None) -> list[str]:
    """Collect OPeNDAP hrefs from the granules' UMM RelatedUrls.

    Streams the resultset, so only the links are retained, not every UMM record.
    """

    urls: list[str] = []
    for granule in results:
        for related in related_urls(granule):
            kind = f"{related.get('Type', '')} {related.get('Subtype', '')}".upper()
            url = related.get("URL", "")
            if (
                "OPENDAP" in kind
                and url
                and (needle is None or needle in url)
                and url not in urls
            ):
                urls.append(url)
    return urls


def with_scheme(url: str, protocol: str) -> str:
    if url.startswith(("dap2://", "dap4://")):
        return url
    return protocol + "://" + url.split("://", 1)[-1]


def with_constraint(url: str, variables: list[str]) -> str:
    """Append a DAP4 constraint expression selecting ``variables`` (if any)."""

    if not variables:
        return url
    return url + "?dap4.ce=" + ";".join("/" + name for name in variables)
