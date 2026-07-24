"""SmartRecruiters adapter — clean public Posting API.

No AU university in the current config uses SmartRecruiters, but the adapter is
included to demonstrate the "add a platform family = add one small class" story
and because the API is genuinely clean:

    GET https://api.smartrecruiters.com/v1/companies/{company_id}/postings?limit=100&offset=0

Wire a university to it by setting ``adapter: smartrecruiters`` and
``params: {company_id: <id>}``.
"""
from __future__ import annotations

import json

from app.ingest.base import Adapter, NotModified, PoliteClient, parse_date
from app.normalise.core import RawJob

_PAGE = 100


class SmartRecruitersAdapter(Adapter):
    name = "smartrecruiters"

    def _url(self, university: dict, offset: int) -> str:
        cid = university["params"]["company_id"]
        return (
            f"https://api.smartrecruiters.com/v1/companies/{cid}/postings"
            f"?limit={_PAGE}&offset={offset}"
        )

    def endpoints(self, university: dict) -> list[dict]:
        return [{"url": self._url(university, 0)}]

    def fetch(self, client: PoliteClient, university: dict) -> list[RawJob]:
        jobs: list[RawJob] = []
        offset = 0
        while True:
            result = client.fetch(self._url(university, offset),
                                  headers={"Accept": "application/json"})
            if result.not_modified:
                raise NotModified(self._url(university, offset))
            page = self.parse(result.content, university, self._url(university, offset))
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
        cid = university["params"]["company_id"]
        jobs: list[RawJob] = []
        for post in data.get("content", []):
            pid = post.get("id")
            title = post.get("name")
            if not pid or not title:
                continue
            loc = post.get("location") or {}
            location = ", ".join(filter(None, [loc.get("city"), loc.get("region")])) or None
            jobs.append(
                RawJob(
                    source_job_id=str(pid),
                    title=title,
                    url=f"https://jobs.smartrecruiters.com/{cid}/{pid}",
                    location=location,
                    remote=bool(loc.get("remote")),
                    posted_at=parse_date(post.get("releasedDate") or post.get("createdOn")),
                    work_type_raw=(post.get("typeOfEmployment") or {}).get("label"),
                )
            )
        return jobs
