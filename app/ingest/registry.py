"""Adapter registry: name -> Adapter instance. Add a family here once."""
from __future__ import annotations

from app.ingest.base import Adapter
from app.ingest.clinch import ClinchAdapter
from app.ingest.html_generic import HtmlGenericAdapter
from app.ingest.nganet import NgaNetAdapter
from app.ingest.oracle import OracleAdapter
from app.ingest.pageup import PageUpAdapter
from app.ingest.smartrecruiters import SmartRecruitersAdapter
from app.ingest.workday import WorkdayAdapter

_REGISTRY: dict[str, Adapter] = {
    a.name: a
    for a in (
        PageUpAdapter(),
        WorkdayAdapter(),
        NgaNetAdapter(),
        SmartRecruitersAdapter(),
        OracleAdapter(),
        ClinchAdapter(),
        HtmlGenericAdapter(),
    )
}


def get_adapter(name: str) -> Adapter:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"No adapter registered for '{name}'. "
                         f"Known: {sorted(_REGISTRY)}") from None


def adapter_names() -> list[str]:
    return sorted(_REGISTRY)
