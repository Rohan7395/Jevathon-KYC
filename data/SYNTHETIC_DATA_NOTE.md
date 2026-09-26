# Synthetic data disclosure

GDELT's public API rate-limits per source IP ("Please limit requests to one every
5 seconds..."), and on the hackathon network / tethered connection used during
development, the 6 documented high-profile portfolio clients kept hitting that
limit even on isolated, correctly-throttled requests — likely because the shared
network IP's quota was already consumed by other traffic.

To keep the demo runnable, `scripts/seed_synthetic_articles.py` fills in
placeholder articles for these clients only, reflecting real, publicly documented
outcomes (convictions/charges that are a matter of public record), but as
fabricated article records, not real fetched news.

**Client IDs with synthetic articles:** C001, C002, C003, C004, C005, C006
(Sam Bankman-Fried, Elizabeth Holmes, Martin Shkreli, Do Kwon, Charlie Javice,
Carlos Ghosn)

**How to identify them in `data/news_cache.json`:** every synthetic article has
`"domain": "synthetic-demo.local"` and a URL under `https://synthetic-demo.local/...`.
Real GDELT articles never use that domain.

**To disclose in the demo:** these 6 clients' results come from placeholder
articles, not a live GDELT fetch, because of API rate limiting on our network.
Everything downstream of the cache (Jev calls, routing, audit, receipts) runs
identically on synthetic and real articles — only the news-fetch step is
substituted.

Re-run `python scripts/fetch_missing.py` from a clean network at any time; it
will only retry clients still in `fetch_failures` and will leave already-cached
clients (including these synthetic ones, once you delete their entries) alone.

## Fictional scenario clients (added for demo coverage)

**Client IDs:** C008, C009, C012, C014, C015, C016, C019, C020, C022, C023, C024, C026

These clients and their employers are fictional. Their synthetic articles (same
`synthetic-demo.local` domain, with short body text) are designed to exercise every
router branch: famous-name collisions (C008, C009), victims/whistleblowers (C014,
C023, C026), ambiguous common-name mentions (C012, C015, C020, C022), clear
perpetrators (C016, C024), and positive news (C019). Articles about the real public
figures in C009 reflect public-record facts. C017, C018, C021, C025 are
intentionally left with no coverage.
