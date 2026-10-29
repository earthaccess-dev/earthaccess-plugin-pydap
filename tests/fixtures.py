"""Conformance fixtures for PyDAP: real UMM, no backend patch needed."""

from __future__ import annotations

import contextlib
from contextlib import AbstractContextManager
from typing import Any

from earthaccess_plugin_spec import Capability, Job
from earthaccess_plugin_spec.results import GranuleResultSet
from earthaccess_plugin_spec.testing import Fixtures

__all__ = ["PyDAPFixtures", "make_granule"]


def make_granule(
    *,
    collection_id: str = "C1234-EXAMPLE",
    concept_id: str = "G1234-EXAMPLE",
    ur: str = "G1234.EXAMPLE",
    urls: list[str] | None = None,
) -> dict[str, Any]:
    urls = urls if urls is not None else ["https://example.org/g1.dap"]
    return {
        "meta": {"concept-id": concept_id, "collection-concept-id": collection_id},
        "umm": {
            "GranuleUR": ur,
            "RelatedUrls": [{"Type": "OPeNDAP", "URL": url} for url in urls],
        },
    }


class PyDAPFixtures(Fixtures):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("result_set", GranuleResultSet([make_granule()]))
        kwargs.setdefault(
            "unsupported_result_set",
            GranuleResultSet([make_granule(collection_id="C-NO-OPENDAP", urls=[])]),
        )
        kwargs.setdefault(
            "capability_cases",
            {Capability.SUBSET_VARIABLE: {"variables": ["sst"]}},
        )
        super().__init__(**kwargs)
        self.capability_assertions.setdefault(
            Capability.SUBSET_VARIABLE, self._assert_variable_constraint
        )

    @staticmethod
    def _assert_variable_constraint(job: Job) -> None:
        hrefs = [asset.href for asset in job.results()]
        assert hrefs, "the variable case produced no assets"
        assert all("?dap4.ce=/sst" in href for href in hrefs), hrefs

    def backend(self) -> AbstractContextManager[Any]:
        # The xarray client only builds URLs; nothing to patch.
        return contextlib.nullcontext()
