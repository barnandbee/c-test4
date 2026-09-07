"""Platform auto-detection tests. These are the safety net for the discovery that
runs on the deployed host — a wrong detection here is a wrong endpoint in prod."""
from app.ingest.discover import detect


def test_workday_from_careers_link():
    html = '<a href="https://rmit.wd3.myworkdayjobs.com/RMIT_Up_Careers">Current opportunities</a>'
    d = detect(html)
    assert d.adapter == "workday"
    assert d.params == {"tenant": "rmit", "dc": "wd3", "site": "RMIT_Up_Careers"}


def test_workday_with_language_segment():
    html = '<a href="https://unimelb.wd105.myworkdayjobs.com/en-US/UoM_External_Career">jobs</a>'
    d = detect(html)
    assert d.adapter == "workday"
    assert d.params["tenant"] == "unimelb"
    assert d.params["dc"] == "wd105"
    assert d.params["site"] == "UoM_External_Career"


def test_workday_from_final_url_only():
    d = detect("", "https://uq.wd3.myworkdayjobs.com/uqcareers")
    assert d.adapter == "workday"
    assert d.params["site"] == "uqcareers"


def test_smartrecruiters():
    html = '<a href="https://careers.smartrecruiters.com/GriffithUniversity">Search jobs</a>'
    d = detect(html)
    assert d.adapter == "smartrecruiters"
    assert d.params == {"company_id": "GriffithUniversity"}


def test_pageup_hosted_with_caw_path():
    html = '<a href="https://careers.pageuppeople.com/533/caw/en/listing/">Vacancies</a>'
    d = detect(html)
    assert d.adapter == "pageup"
    assert d.params["listing_url"] == "https://careers.pageuppeople.com/533/caw/en/listing/"


def test_pageup_vanity_domain():
    html = '<a href="https://careers.deakin.edu.au/en/listing/">Current vacancies</a>'
    # /en/listing without cw/caw still shouldn't crash; vanity regex needs cw/caw,
    # so this falls through to None (Deakin is configured explicitly). Guard the shape:
    d = detect(html)
    assert d is None or d.adapter == "pageup"


def test_pageup_vanity_with_cw():
    html = '<a href="https://jobs.uwa.edu.au/cw/en/listing/">Current opportunities</a>'
    d = detect(html)
    assert d.adapter == "pageup"
    assert d.params["listing_url"] == "https://jobs.uwa.edu.au/cw/en/listing/"


def test_nganet():
    html = '<a href="https://qut.nga.net.au/cp/index.cfm?event=jobs.home">Jobs</a>'
    d = detect(html)
    assert d.adapter == "nganet"
    assert d.params["listing_url"] == "https://qut.nga.net.au/cp/"


def test_oracle():
    html = '<a href="https://uow.hcm.ap1.oraclecloud.com/hcmUI/CandidateExperience/en/sites/UOW/jobs">x</a>'
    d = detect(html)
    assert d.adapter == "oracle"
    assert d.params["site"] == "UOW"
    assert "oraclecloud.com" in d.params["host"]


def test_workday_wins_over_pageup_when_both_present():
    html = ('<a href="https://x.pageuppeople.com/100/cw/en/listing/">old</a>'
            '<a href="https://mq.wd3.myworkdayjobs.com/Careers">new</a>')
    assert detect(html).adapter == "workday"


def test_no_match():
    assert detect("<p>Work with us! Email hr@example.edu.au</p>") is None
