# Discovery report — platform coverage across the 42 Australian universities

**Compiled:** 2026-07 · **Method:** public web search + known Australian HE ATS URL
patterns · **Live-verified:** ❌ no (see caveat)

## ⚠️ Read this first

The build environment has **no outbound network access** to university / ATS
domains — the egress policy returns `403 policy denial` for every `*.edu.au`,
`*.myworkdayjobs.com`, `*.pageuppeople.com`, `*.nga.net.au` host. I verified this
directly (proxy status endpoint + `WebFetch` both blocked). `WebSearch` works, so
platform identification below is real research, but **the exact endpoint URLs and
CSS selectors are inferred, not confirmed against a live response.**

Consequences:
- Platform *family* identifications are reliable where marked `high`.
- Endpoint paths follow each family's standard pattern but may need a one-line
  `config/universities.yaml` edit on first deploy.
- Run `python -m app.ingest.runner --verify` in an environment with open egress
  to check every endpoint and print corrections. This is a 5-minute task per
  university and is exactly what the config-driven design optimises for.

## Platform distribution

| Platform | Count | Universities |
|---|---:|---|
| **PageUp** | 30 | anu, canberra, acu, csu, macquarie, scu, une, unsw, newcastle, uts, uow, cdu, bond, cqu, griffith, jcu, qut, unisq, unisc, adelaide, flinders, utas, deakin, federation, latrobe, monash, rmit, swinburne, vu, curtin, ecu, murdoch, notredame, uwa |
| **Workday** | 4 | sydney, uq, melbourne, torrens |
| **HTML (bespoke)** | 3 | aut, avondale, divinity |
| **NGA.NET (suspected)** | — | wsu, qut *(currently configured pageup pending verify)* |

> PageUp's dominance in Australian HE is the whole reason the "don't write 42
> scrapers" instinct is correct: **one PageUp adapter covers ~70% of the sector.**

## Coverage table

| University | State | Groups | Platform | Endpoint type | Confidence |
|---|---|---|---|---|---|
| Australian National University | ACT | Go8 | PageUp | RSS + HTML listing | 🟢 high |
| University of Canberra | ACT | IRU | PageUp | HTML listing | 🟡 medium |
| Australian Catholic University | NSW | — | PageUp | HTML listing | 🟡 medium |
| Australian University of Theology | NSW | — | HTML bespoke | HTML scrape | 🔴 low |
| Avondale University | NSW | — | HTML bespoke | HTML scrape | 🔴 low |
| Charles Sturt University | NSW | RUN | PageUp | HTML listing | 🟡 medium |
| Macquarie University | NSW | — | PageUp | HTML listing | 🟢 high |
| Southern Cross University | NSW | RUN | PageUp | HTML listing | 🟡 medium |
| University of New England | NSW | RUN | PageUp | HTML listing | 🟢 high |
| UNSW Sydney | NSW | Go8 | PageUp | RSS + HTML listing | 🟡 medium |
| University of Newcastle | NSW | ATN | PageUp | HTML listing | 🟡 medium |
| University of Sydney | NSW | Go8 | **Workday** | JSON API | 🟢 high |
| University of Technology Sydney | NSW | ATN | PageUp? | HTML listing | 🔴 low |
| University of Wollongong | NSW | — | PageUp | HTML listing | 🟡 medium |
| Western Sydney University | NSW | IRU | NGA.NET? | — | 🔴 low |
| Charles Darwin University | NT | IRU | PageUp | HTML listing | 🟡 medium |
| Bond University | QLD | — | PageUp? | HTML listing | 🔴 low |
| CQUniversity | QLD | RUN | PageUp | HTML listing | 🟢 high |
| Griffith University | QLD | IRU | PageUp | HTML listing | 🟡 medium |
| James Cook University | QLD | IRU | PageUp | HTML listing | 🔴 low |
| Queensland University of Technology | QLD | — | NGA.NET? | — | 🔴 low |
| University of Queensland | QLD | Go8 | **Workday** | JSON API | 🟢 high |
| University of Southern Queensland | QLD | RUN | PageUp | HTML listing | 🟡 medium |
| University of the Sunshine Coast | QLD | — | PageUp | HTML listing | 🟡 medium |
| Adelaide University | SA | Go8 | PageUp | RSS + HTML listing | 🟢 high |
| Flinders University | SA | — | PageUp | HTML listing | 🟢 high |
| Torrens University Australia | SA | — | **Workday** | JSON API | 🟢 high |
| University of Tasmania | TAS | — | PageUp | HTML listing | 🟡 medium |
| Deakin University | VIC | ATN | PageUp | HTML listing | 🟡 medium |
| Federation University Australia | VIC | RUN | PageUp | HTML listing | 🟡 medium |
| La Trobe University | VIC | IRU | PageUp | HTML listing | 🟡 medium |
| Monash University | VIC | Go8 | PageUp? | HTML listing | 🔴 low |
| RMIT University | VIC | ATN | PageUp | HTML listing | 🟡 medium |
| Swinburne University of Technology | VIC | — | PageUp | HTML listing | 🟡 medium |
| University of Divinity | VIC | — | HTML bespoke | HTML scrape | 🟡 medium |
| University of Melbourne | VIC | Go8 | **Workday** | JSON API | 🟢 high |
| Victoria University | VIC | — | PageUp | HTML listing | 🟡 medium |
| Curtin University | WA | ATN | PageUp | HTML listing | 🟡 medium |
| Edith Cowan University | WA | — | PageUp | HTML listing | 🟢 high |
| Murdoch University | WA | IRU | PageUp | HTML listing | 🟡 medium |
| University of Notre Dame Australia | WA | — | PageUp? | HTML listing | 🔴 low |
| University of Western Australia | WA | Go8 | PageUp | RSS + HTML listing | 🟢 high |

## Confidence summary

- 🟢 **high (11):** anu, macquarie, une, sydney, cqu, uq, adelaide, flinders, torrens, melbourne, ecu, uwa — platform confirmed, endpoint follows the exact known pattern.
- 🟡 **medium (22):** platform family near-certain (PageUp vanity/careers host), endpoint path standard but the subdomain needs a quick confirm.
- 🔴 **low (9):** aut, avondale, uts, wsu, bond, jcu, qut, monash, notredame — platform unconfirmed or known to be in flux.

## The ones I couldn't crack (yet)

These are the honest gaps against the "35/42" target and need live verification:

1. **Western Sydney (wsu)** & **QUT** — both have historically used **NGA.NET**
   (`uws.nga.net.au`, `qut.nga.net.au`). A `nganet` adapter exists; they're
   configured `pageup` as a placeholder and flagged. Flip the `adapter` field
   once the live board is confirmed.
2. **Monash** — was PageUp (client 513), disabled it after the 2018 breach and
   may have migrated to Workday. Needs a live check to pick the right adapter.
3. **UTS** — research didn't surface the platform. PageUp assumed.
4. **Bond, Notre Dame** — private universities, platform unconfirmed.
5. **Australian University of Theology, Avondale** — small institutions on
   bespoke pages; the `html_generic` selectors are guesses and will need tuning.
   These two are the most likely to remain documented gaps.

**Realistic live coverage:** the 11 high + 22 medium = **33 universities** should
ingest with zero or one-line config edits. Reaching 35+ means confirming 2 of the
9 low-confidence sites, which is a short verification session with open egress.
The 3 tiny bespoke sites (aut, avondale, divinity) are the expected long-tail
gaps and are documented as such per the brief's definition of done.
