import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import NEWS_CACHE_JSON
from app.gdelt import load_cache, make_article_id, save_cache
from app.models import Article

SYNTHETIC_DOMAIN = "synthetic-demo.local"

# Each entry is (title, body text or None, seen_date).
# C001–C006: public-record outcomes for documented high-profile cases.
# C008+: fully fictional scenarios for fictional private clients, designed to
# exercise every router branch (hit, name collision, victim, whistleblower,
# no wrongdoing, ambiguous → REVIEW).
SYNTHETIC_ARTICLES: dict[str, list[tuple[str, str | None, str | None]]] = {
    "C001": [
        ("Sam Bankman-Fried sentenced to 25 years in prison for FTX fraud", None, None),
        ("FTX founder Bankman-Fried ordered to forfeit billions after fraud conviction", None, None),
    ],
    "C002": [
        ("Elizabeth Holmes begins 11-year prison sentence over Theranos fraud", None, None),
        ("Theranos founder Holmes loses appeal in blood-testing fraud case", None, None),
    ],
    "C003": [
        ("Martin Shkreli released from prison after securities fraud sentence", None, None),
        ("Former pharma executive Shkreli barred from industry after fraud conviction", None, None),
    ],
    "C004": [
        ("Terraform Labs founder Do Kwon extradited to face US fraud charges", None, None),
        ("Do Kwon pleads guilty to fraud charges tied to Terra-Luna collapse", None, None),
    ],
    "C005": [
        ("Frank founder Charlie Javice convicted of defrauding JPMorgan Chase", None, None),
        ("Charlie Javice found guilty on all counts in fraud trial", None, None),
    ],
    "C006": [
        ("Carlos Ghosn charged with financial misconduct by Japanese prosecutors", None, None),
        ("Fugitive ex-Nissan chief Ghosn remains wanted over fraud allegations", None, None),
    ],
    # Famous-name collision + a genuine hit on the actual client.
    "C008": [
        (
            "Taylor Swift announces additional stadium dates for world tour",
            "Pop superstar Taylor Swift added six stadium shows to her world tour on Monday, "
            "her label said. The singer-songwriter will perform in Europe and South America next summer.",
            "20260920T140000Z",
        ),
        (
            "Tulsa accountant charged in tax-preparation fraud scheme",
            "Federal prosecutors in Oklahoma charged Taylor Swift, 51, an accountant at Prairie Tax "
            "Services in Tulsa, with filing hundreds of fraudulent tax returns that inflated client "
            "refunds by more than $2 million. Swift was indicted on 14 counts of aiding and assisting "
            "the preparation of false returns. She has pleaded not guilty.",
            "20260918T093000Z",
        ),
    ],
    # Famous-name collision with genuinely adverse news about someone else.
    "C009": [
        (
            "Former Trump lawyer Michael Cohen testifies again before congressional panel",
            "Michael Cohen, Donald Trump's former personal attorney, who served a prison sentence "
            "after pleading guilty to campaign finance violations and tax evasion, appeared before "
            "a House committee in Washington on Tuesday.",
            "20260915T160000Z",
        ),
        (
            "Michael Cohen's disbarment upheld by New York appeals court",
            "A New York appellate court declined to reinstate the law license of Michael Cohen, "
            "the former fixer for Donald Trump, citing his federal convictions.",
            "20260910T120000Z",
        ),
    ],
    # Common name, vague article with no identifying details → should land in REVIEW.
    "C012": [
        (
            "Denver police question man in cryptocurrency theft probe",
            "Denver police said Thursday they had questioned a man identified as John Smith in "
            "connection with the theft of roughly $400,000 in cryptocurrency from a local investment "
            "club. Police did not release his age or occupation and said no charges had been filed. "
            "The investigation is ongoing.",
            "20260922T190000Z",
        ),
    ],
    # Client is the victim, not the perpetrator → CLEAR (not the subject).
    "C014": [
        (
            "Atlanta freight manager testifies against cargo theft ring",
            "James Johnson, a logistics manager at Peach Freight in Atlanta, testified Wednesday that "
            "a criminal ring stole more than 40 trailers from his company's yard using forged "
            "pickup orders. Johnson, 57, told jurors he first noticed the discrepancies in company "
            "records and alerted police. Five defendants face federal cargo theft charges.",
            "20260919T150000Z",
        ),
    ],
    # Employer under investigation, client named but role unclear → REVIEW.
    "C015": [
        (
            "Seattle analytics firm Northwind under SEC investigation over client data sales",
            "The Securities and Exchange Commission has opened an investigation into Northwind "
            "Analytics, a Seattle data firm, over allegations that it sold non-public client data "
            "to hedge funds. People familiar with the matter said investigators have interviewed "
            "several employees, including data analyst David Lee. It was not clear whether any "
            "individual employee is a target of the probe.",
            "20260921T110000Z",
        ),
    ],
    # Clear perpetrator, bribery → FLAGGED.
    "C016": [
        (
            "Cleveland electrician indicted in city contract bribery scheme",
            "A federal grand jury indicted Michael Brown, 43, owner of Lakeshore Electric in "
            "Cleveland, on charges that he paid more than $180,000 in bribes to a city official in "
            "exchange for municipal wiring contracts. Brown faces counts of bribery and honest-services "
            "wire fraud.",
            "20260917T130000Z",
        ),
        (
            "City official pleads guilty, names Lakeshore Electric owner in bribery case",
            "A former Cleveland procurement officer pleaded guilty Monday and admitted accepting cash "
            "payments from Michael Brown of Lakeshore Electric in return for steering contracts.",
            "20260923T100000Z",
        ),
    ],
    # Same person, positive news → CLEAR (no wrongdoing).
    "C019": [
        (
            "Toronto engineer Wei Zhang wins provincial award for bridge retrofit design",
            "Wei Zhang, a civil engineer at Maple Bridgeworks in Toronto, received Ontario's "
            "infrastructure excellence award for a seismic retrofit of a 1960s highway overpass.",
            "20260914T120000Z",
        ),
    ],
    # Pharmacy under investigation; client mentioned only as cooperating staff → REVIEW.
    "C020": [
        (
            "Manchester pharmacy investigated over missing controlled drugs",
            "The General Pharmaceutical Council is investigating Northgate Pharmacy in Manchester "
            "after an audit found discrepancies in its records of controlled medicines. Pharmacist "
            "Ahmed Khan said staff were cooperating fully. No individual has been charged.",
            "20260916T090000Z",
        ),
    ],
    # Whistleblower suit naming the client → allegation-level, ambiguous.
    "C022": [
        (
            "Whistleblower alleges Bayline Robotics shipped restricted parts abroad",
            "A former employee of San Jose-based Bayline Robotics filed a whistleblower complaint "
            "alleging the company shipped export-controlled motion sensors to a sanctioned buyer "
            "through a third country. The complaint names mechanical engineer Daniel Nguyen as having "
            "signed off on the shipping specifications. Bayline denied wrongdoing; no charges have been filed.",
            "20260924T170000Z",
        ),
    ],
    # Client is an identity-theft victim → CLEAR.
    "C023": [
        (
            "Portland veterinarian's identity used to obtain fraudulent loans",
            "Emily Clark, a veterinarian at Cedar Animal Clinic in Portland, discovered that "
            "fraudsters had used her stolen identity to open three business loans. Police arrested "
            "two suspects on Friday. Clark said she was relieved and urged others to monitor their credit.",
            "20260913T200000Z",
        ),
    ],
    # Clear perpetrator, money laundering → FLAGGED.
    "C024": [
        (
            "Miami restaurant owner charged with laundering drug proceeds",
            "Carlos Rodriguez, 51, owner of Casa Rodriguez in Miami, was arrested and charged with "
            "laundering more than $3 million in narcotics proceeds through his restaurant's cash "
            "receipts, according to a federal complaint unsealed Thursday.",
            "20260925T140000Z",
        ),
    ],
    # Client is the whistleblower who exposed a partner's embezzlement → CLEAR.
    "C026": [
        (
            "Chicago law firm partner charged with embezzling client funds",
            "A founding partner of Lakeview Legal in Chicago was charged with embezzling $1.2 million "
            "from client trust accounts. Paralegal Anna Kowalski, who first reported the missing funds "
            "to the state bar, was praised by prosecutors for her cooperation.",
            "20260912T110000Z",
        ),
    ],
}


def main() -> None:
    articles_by_client, fetch_failures = load_cache(NEWS_CACHE_JSON)

    seeded_clients = []
    for client_id, entries in SYNTHETIC_ARTICLES.items():
        existing = articles_by_client.get(client_id) or []
        if any(a.domain != SYNTHETIC_DOMAIN for a in existing):
            continue  # real GDELT coverage wins

        synthetic_articles = []
        for i, (title, text, seen_date) in enumerate(entries):
            url = f"https://{SYNTHETIC_DOMAIN}/{client_id.lower()}-{i}"
            synthetic_articles.append(
                Article(
                    id=make_article_id(client_id, url),
                    client_id=client_id,
                    url=url,
                    title=title,
                    domain=SYNTHETIC_DOMAIN,
                    seen_date=seen_date,
                    language="English",
                    text=text,
                    title_only=text is None,
                )
            )

        articles_by_client[client_id] = synthetic_articles
        seeded_clients.append(client_id)

    fetch_failures = [cid for cid in fetch_failures if cid not in seeded_clients]

    save_cache(NEWS_CACHE_JSON, articles_by_client, fetch_failures)

    print(f"Seeded synthetic articles for: {','.join(seeded_clients) if seeded_clients else '(none)'}")
    print(f"Wrote data/news_cache.json (domain={SYNTHETIC_DOMAIN!r} marks synthetic entries)")


if __name__ == "__main__":
    main()
