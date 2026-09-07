"""Adapter tests against saved fixtures — so a broken selector is obvious without
re-running a live crawl (brief reliability requirement)."""
from conftest import load_fixture

from app.ingest.base import FetchResult
from app.ingest.html_generic import HtmlGenericAdapter
from app.ingest.nganet import NgaNetAdapter
from app.ingest.oracle import OracleAdapter
from app.ingest.pageup import PageUpAdapter
from app.ingest.smartrecruiters import SmartRecruitersAdapter
from app.ingest.workday import WorkdayAdapter
from app.normalise.core import normalise_record


class FakeClient:
    """Minimal PoliteClient stand-in: serves canned bytes per URL."""

    def __init__(self, pages: dict[str, bytes]):
        self.pages = pages
        self.requested: list[str] = []

    def fetch(self, url, method="GET", **kwargs):
        self.requested.append(url)
        if url not in self.pages:
            import httpx
            raise httpx.HTTPStatusError("404", request=None, response=None)
        return FetchResult(url, 200, self.pages[url], final_url=url)

UWA = {"slug": "uwa", "name": "University of Western Australia", "state": "WA",
       "params": {"listing_url": "https://jobs.uwa.edu.au/cw/en/listing/",
                  "feed_url": "https://jobs.uwa.edu.au/cw/en/listing/?rss=1"}}
SYDNEY = {"slug": "sydney", "name": "University of Sydney", "state": "NSW",
          "params": {"tenant": "usyd", "dc": "wd3", "site": "USYD_EXTERNAL_CAREER_SITE"}}
CSU = {"slug": "csu", "name": "Charles Sturt University", "state": "NSW",
       "params": {"listing_url": "https://csu.nga.net.au/cp/"}}
TORRENS = {"slug": "torrens", "name": "Torrens University", "state": "SA",
           "params": {"company_id": "TorrensUniversity"}}
DIVINITY = {"slug": "divinity", "name": "University of Divinity", "state": "VIC",
            "params": {"listing_url": "https://vox.divinity.edu.au/vacancies/",
                       "row_selector": "article, .post",
                       "title_selector": ".entry-title a, h2 a",
                       "link_selector": ".entry-title a, a",
                       "location_selector": ".location",
                       "date_selector": "time, .published",
                       "excerpt_selector": "p"}}


class TestPageUp:
    def test_html_listing(self):
        jobs = PageUpAdapter().parse(load_fixture("pageup_listing.html"), UWA,
                                     UWA["params"]["listing_url"])
        assert len(jobs) == 3
        first = jobs[0]
        assert first.source_job_id == "523401"
        assert first.title == "Lecturer in Computer Science"
        assert first.url.startswith("https://jobs.uwa.edu.au/cw/en/job/523401")
        assert "Camperdown" in first.location
        assert first.classification_raw == "Academic Level B"

    def test_html_normalises_correctly(self):
        jobs = PageUpAdapter().parse(load_fixture("pageup_listing.html"), UWA,
                                     UWA["params"]["listing_url"])
        recs = [normalise_record(j, UWA) for j in jobs]
        by_title = {r["title"]: r for r in recs}
        lecturer = by_title["Lecturer in Computer Science"]
        assert lecturer["level_band"] == "mid"
        assert lecturer["work_type"] == "continuing"
        assert lecturer["salary_min"] == 110596
        postdoc = by_title["Postdoctoral Research Fellow, Marine Biology"]
        assert postdoc["role_family"] == "research"
        assert postdoc["remote_flag"] is True
        assert postdoc["level_band"] == "early-career"

    def test_rss_feed(self):
        jobs = PageUpAdapter().parse(load_fixture("pageup_feed.xml"), UWA,
                                     UWA["params"]["feed_url"])
        assert len(jobs) == 2
        assert jobs[0].source_job_id == "880123"
        assert jobs[0].classification_raw == "Academic Level C"
        assert jobs[0].posted_at is not None

    def test_endpoint_order_prefers_feed(self):
        specs = PageUpAdapter().endpoints(UWA)
        assert specs[0]["url"].endswith("?rss=1")

    def test_xhr_json_wrapping_html_fragment(self):
        # PageUp XHR responses wrap the listing HTML in JSON under a varying key.
        import json
        fragment = ('<li><article><h3><a href="/cw/en/job/700100/lecturer">'
                    'Lecturer in Maths</a></h3><span class="location">Crawley</span>'
                    '<span class="categories">Academic Level B</span></article></li>')
        for key in ("results", "SearchResults", "content", "jobResultsHtml"):
            payload = json.dumps({key: fragment, "count": 1}).encode()
            jobs = PageUpAdapter().parse(payload, UWA, UWA["params"]["listing_url"])
            assert len(jobs) == 1, f"failed for key {key}"
            assert jobs[0].source_job_id == "700100"
            assert jobs[0].title == "Lecturer in Maths"

    def test_json_without_job_links_yields_nothing(self):
        import json
        payload = json.dumps({"count": 0, "results": "<p>No matching jobs</p>"}).encode()
        assert PageUpAdapter().parse(payload, UWA, UWA["params"]["listing_url"]) == []


class TestWorkday:
    def test_parse(self):
        adapter = WorkdayAdapter()
        jobs = adapter.parse(load_fixture("workday_jobs.json"), SYDNEY,
                             "https://usyd.wd3.myworkdayjobs.com/...")
        assert len(jobs) == 3
        assert adapter._last_total == 3
        assoc = jobs[0]
        assert assoc.source_job_id == "R0045123"
        assert assoc.title == "Associate Professor, Data Science"
        assert assoc.url == ("https://usyd.wd3.myworkdayjobs.com/en-US/"
                             "USYD_EXTERNAL_CAREER_SITE/job/Camperdown-Campus/"
                             "Associate-Professor--Data-Science_R0045123")
        assert assoc.posted_at is not None  # "Posted Today"

    def test_normalises(self):
        jobs = WorkdayAdapter().parse(load_fixture("workday_jobs.json"), SYDNEY, "x")
        recs = {r["title"]: r for r in (normalise_record(j, SYDNEY) for j in jobs)}
        assert recs["Associate Professor, Data Science"]["level_band"] == "leadership"
        assert recs["Research Assistant, Immunology"]["role_family"] == "research"
        assert recs["Manager, Financial Operations"]["level_band"] == "senior"  # HEW 9 in bullets


class TestNgaNet:
    def test_parse(self):
        jobs = NgaNetAdapter().parse(load_fixture("nganet_listing.html"), CSU,
                                     "https://csu.nga.net.au/cp/")
        assert len(jobs) == 2
        assert jobs[0].source_job_id == "71012"
        assert jobs[0].title == "Lecturer in Nursing"
        assert jobs[0].closes_at is not None
        assert "Bathurst" in jobs[0].location


class TestSmartRecruiters:
    def test_parse(self):
        jobs = SmartRecruitersAdapter().parse(load_fixture("smartrecruiters_postings.json"),
                                              TORRENS, "x")
        assert len(jobs) == 2
        assert jobs[0].title == "Marketing Coordinator"
        assert jobs[0].url == "https://jobs.smartrecruiters.com/TorrensUniversity/743999000123456"
        assert "Melbourne" in jobs[0].location
        assert jobs[1].remote is True


class TestHtmlGeneric:
    def test_parse(self):
        jobs = HtmlGenericAdapter().parse(load_fixture("html_generic_divinity.html"), DIVINITY,
                                          "https://vox.divinity.edu.au/vacancies/")
        assert len(jobs) == 2
        assert jobs[0].title.startswith("Lecturer in Biblical Studies")
        assert jobs[0].url == "https://vox.divinity.edu.au/vacancies/lecturer-in-biblical-studies/"
        assert "Parkville" in jobs[0].location
        assert jobs[0].posted_at is not None


class TestOracle:
    UOW = {"slug": "uow", "name": "University of Wollongong", "state": "NSW",
           "params": {"host": "uow.hcm.ap1.oraclecloud.com", "site": "UOW"}}

    def test_parse(self):
        jobs = OracleAdapter().parse(load_fixture("oracle_requisitions.json"), self.UOW, "x")
        assert len(jobs) == 2
        assert jobs[0].source_job_id == "497123"
        assert jobs[0].url == (
            "https://uow.hcm.ap1.oraclecloud.com/hcmUI/CandidateExperience/en/sites/UOW/job/497123"
        )
        assert "Wollongong" in jobs[0].location
        assert jobs[0].posted_at is not None


class TestPageUpPagination:
    """PageUp HTML listings paginate; the adapter must walk every page and dedupe."""

    def _page(self, ids):
        rows = "".join(
            f'<li><article><h3><a href="/cw/en/job/{i}/role-{i}">Role {i}</a></h3>'
            f'<span class="location">Perth</span></article></li>' for i in ids
        )
        return f"<html><body><ul>{rows}</ul></body></html>".encode()

    def test_walks_all_pages_until_empty(self):
        base = "https://jobs.uwa.edu.au/cw/en/listing/"
        pages = {
            base + "?page=1": self._page([1, 2, 3]),
            base + "?page=2": self._page([4, 5]),
            base + "?page=3": self._page([]),          # empty -> stop
        }
        client = FakeClient(pages)
        uni = {"slug": "uwa", "name": "UWA", "state": "WA", "params": {"listing_url": base}}
        jobs = PageUpAdapter().fetch(client, uni)
        assert sorted(j.source_job_id for j in jobs) == ["1", "2", "3", "4", "5"]

    def test_stops_when_page_repeats(self):
        # A site that ignores ?page= and always returns page 1 must not loop forever.
        base = "https://jobs.uwa.edu.au/cw/en/listing/"
        same = self._page([1, 2])
        client = FakeClient({base + f"?page={n}": same for n in range(1, 42)})
        uni = {"slug": "uwa", "name": "UWA", "state": "WA", "params": {"listing_url": base}}
        jobs = PageUpAdapter().fetch(client, uni)
        assert sorted(j.source_job_id for j in jobs) == ["1", "2"]
        assert len(client.requested) == 2  # page 1 (new), page 2 (no new ids) -> stop
