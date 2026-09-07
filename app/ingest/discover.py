"""Platform auto-discovery.

Instead of hard-coding each university's ATS endpoint (fragile — the data-centre
subdomain, the /cw/ vs /caw/ path, the PageUp client id, and the Workday site name
are all guessable-but-wrong), we detect the real endpoint from the careers page
itself. The careers landing page almost always links to the underlying ATS, so a
single fetch + URL-pattern match resolves the correct adapter and parameters.

`detect()` is pure (no I/O) and unit-tested against fixtures; the fetching lives in
the runner, which runs where network egress is open (e.g. Render).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- URL signatures for each platform family --------------------------------
# Workday:  https://<tenant>.<dc>.myworkdayjobs.com[/<lang>]/<SITE>
_WORKDAY = re.compile(
    r"https?://(?P<tenant>[a-z0-9-]+)\.(?P<dc>wd\d+)\.myworkdayjobs\.com"
    r"(?:/(?:wday/cxs/[^/]+/)?)?(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[A-Za-z0-9_]+)",
    re.I,
)
# SmartRecruiters: careers.smartrecruiters.com/<Company> or jobs.smartrecruiters.com/<Company>
_SMARTRECRUITERS = re.compile(
    r"https?://(?:careers|jobs)\.smartrecruiters\.com/(?P<company>[A-Za-z0-9._-]+)", re.I
)
# PageUp (hosted):  careers.pageuppeople.com/<clientId>/<cw|caw>/...
_PAGEUP_HOSTED = re.compile(
    r"https?://(?P<host>[a-z0-9.-]*pageuppeople\.com)/(?P<client>\d+)/(?P<path>c[aw]{1,2})/", re.I
)
# PageUp (vanity):  https://<host>/<cw|caw>/en/listing  (e.g. jobs.uwa.edu.au/cw/en/listing)
_PAGEUP_VANITY = re.compile(
    r"https?://(?P<host>[a-z0-9.-]+)/(?P<path>c[aw]{1,2})/en/(?:listing|job)", re.I
)
# NGA.NET:  <tenant>.nga.net.au
_NGANET = re.compile(r"https?://(?P<host>[a-z0-9-]+\.nga\.net\.au)", re.I)
# Oracle Recruiting Cloud:  <host>/hcmUI/CandidateExperience/.../sites/<site>
_ORACLE = re.compile(
    r"https?://(?P<host>[a-z0-9.-]+)/hcmUI/CandidateExperience/(?:[a-z]{2}/)?sites/(?P<site>[A-Za-z0-9_-]+)",
    re.I,
)


@dataclass
class Detection:
    adapter: str
    params: dict
    matched_url: str
    confidence: str = "discovered"


def detect(text: str, *extra_urls: str) -> Detection | None:
    """Detect the ATS platform from page text (HTML) and/or candidate URLs.

    `text` is typically the fetched careers-page HTML; `extra_urls` can include the
    final URL after redirects. Returns the highest-priority match, or None.
    Priority: Workday > SmartRecruiters > NGA.NET > Oracle > PageUp(hosted) >
    PageUp(vanity) — most specific / cleanest data source first.
    """
    haystack = "\n".join([*extra_urls, text or ""])

    m = _WORKDAY.search(haystack)
    if m and m.group("site").lower() not in {"wday", "cxs"}:
        return Detection(
            "workday",
            {"tenant": m.group("tenant").lower(), "dc": m.group("dc").lower(),
             "site": m.group("site")},
            m.group(0),
        )

    m = _SMARTRECRUITERS.search(haystack)
    if m:
        return Detection("smartrecruiters", {"company_id": m.group("company")}, m.group(0))

    m = _NGANET.search(haystack)
    if m:
        host = m.group("host").lower()
        return Detection("nganet", {"listing_url": f"https://{host}/cp/"}, m.group(0))

    m = _ORACLE.search(haystack)
    if m:
        return Detection(
            "oracle",
            {"host": m.group("host").lower(), "site": m.group("site")},
            m.group(0),
        )

    m = _PAGEUP_HOSTED.search(haystack)
    if m:
        host, client, path = m.group("host").lower(), m.group("client"), m.group("path").lower()
        base = f"https://{host}/{client}/{path}/en/listing/"
        return Detection("pageup", {"listing_url": base}, m.group(0))

    m = _PAGEUP_VANITY.search(haystack)
    if m:
        host, path = m.group("host").lower(), m.group("path").lower()
        # skip obvious non-ATS hosts
        if "pageuppeople" not in host:
            base = f"https://{host}/{path}/en/listing/"
            return Detection("pageup", {"listing_url": base}, m.group(0))

    return None
