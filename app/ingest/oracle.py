"""Oracle Recruiting Cloud (ORC) adapter — used by e.g. University of Wollongong.

ORC exposes a public JSON API behind the "CandidateExperience" career site:

    GET https://<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitions
        ?onlyData=true&expand=requisitionList.secondaryLocations
        &finder=findReqs;siteNumber=<site>,limit=<n>,offset=<n>,sortBy=POSTING_DATES_DESC

The job detail URL is the CandidateExperience page:
    https://<host>/hcmUI/CandidateExperience/en/sites/<site>/job/<id>
"""
from __future__ import annotations

import json

from app.ingest.base import Adapter, NotModified, PoliteClient, parse_date
from app.normalise.core import RawJob

_PAGE = 100
_MAX_PAGES = 30


class OracleAdapter(Adapter):
    name = "oracle"

    def _api(self, university: dict, offset: int) -> str:
        p = university["params"]
        host, site = p["host"], p["site"]
        return (
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?onlyData=true&expand=requisitionList.secondaryLocations"
            f"&finder=findReqs;siteNumber={site},limit={_PAGE},offset={offset},"
            f"sortBy=POSTING_DATES_DESC"
        )

    def endpoints(self, university: dict) -> list[dict]:
        return [{"url": self._api(university, 0), "kwargs": {"headers": {"Accept": "application/json"}}}]

    def fetch(self, client: PoliteClient, university: dict) -> list[RawJob]:
        jobs: list[RawJob] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            url = self._api(university, offset)
            result = client.fetch(url, headers={"Accept": "application/json"})
            if result.not_modified:
                raise NotModified(url)
            page = self.parse(result.content, university, url)
            if not page:
                break
            jobs.extend(page)
            if len(page) < _PAGE:
                break
            offset += _PAGE
        return jobs

    def parse(self, content: bytes, university: dict, source_url: str) -> list[RawJob]:
        try:
            data = json.loads(content.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return []
        # ORC wraps results in items[0].requisitionList
        items = data.get("items") or []
        reqs = items[0].get("requisitionList", []) if items else data.get("requisitionList", [])
        p = university["params"]
        host, site = p["host"], p["site"]
        jobs: list[RawJob] = []
        for r in reqs:
            rid = r.get("Id") or r.get("requisitionId")
            title = r.get("Title")
            if not rid or not title:
                continue
            loc = r.get("PrimaryLocation") or r.get("Location")
            jobs.append(
                RawJob(
                    source_job_id=str(rid),
                    title=title,
                    url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}",
                    location=loc,
                    posted_at=parse_date(r.get("PostedDate") or r.get("ExternalPostedStartDate")),
                    work_type_raw=r.get("WorkplaceTypeCode") or r.get("JobFunction"),
                )
            )
        return jobs
