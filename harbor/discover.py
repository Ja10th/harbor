"""Find companies from a public search. Not a revenue ranking and not an inbox finder."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, urlunparse

from harbor.research import fetch_page, pick_fact, search_web

DIRECTORY = (
    "jiji.", "hotfrog.", "yellowpages", "yelp.", "facebook.", "instagram.",
    "linkedin.", "twitter.", "x.com", "tiktok.", "youtube.", "youtu.be",
    "wikipedia.org", "wikidata.org", "crunchbase.", "zoominfo.", "apollo.",
    "rocketreach.", "indeed.", "glassdoor.", "nairaland.", "pinterest.",
    "google.", "duckduckgo.", "vconnect.", "finelib.", "businesslist.",
    "nigeriagalleria.", "hotnigerianjobs.", "bloomberg.", "reuters.",
    "premiumtimesng.", "thecable.", "guardian.ng", "bbc.", "cnn.",
    "punchng.", "vanguardngr.", "thisdaylive.", "scribd.com", "dnb.com",
    "aeroleads.", "ranked.ng", "nigerianqueries.", "elalan.com",
    "buildingpractice.", "revenuebase.", "hunter.io", "lusha.", "seamless.",
    "contactout.", "signalhire.", "clearbit.",
)
GENERIC = re.compile(
    r"\b(top \d+|\d+\s+best|best\b|list of|companies in|near me|directory|"
    r"official site|wikipedia|jobs in|emails?|contacts?|pdf|ranked|queries)\b",
    re.I,
)
DIRECTORY_PAGE = re.compile(
    r"\b(business directory|email finder|find the best businesses|list of companies|"
    r"top \d+ companies|b2b leads|verified contacts)\b",
    re.I,
)
TIER_WHY = {
    "top": "More of their public site was readable than the others in this search.",
    "middle": "A public site, with less to read than the top group.",
    "thin": "A thin public page. Not a judgment of the business.",
}


def discover(industry: str, location: str, limit: int = 6) -> list[dict]:
    industry = " ".join((industry or "").split())
    location = " ".join((location or "").split())
    if len(industry) < 3 or len(location) < 2:
        raise ValueError("Add an industry and a location.")
    hits = []
    seen_urls = set()
    token = (_industry_tokens(industry) or ["company"])[0]
    for query in (
        f"{industry} in {location}",
        f"{industry} {location} company",
        f"{token} limited {location}",
        f"{token} contractor {location}",
    ):
        for hit in search_web(query, 8):
            if hit["url"] in seen_urls or _directory(hit["url"]):
                continue
            seen_urls.add(hit["url"])
            hits.append(hit)
    by_domain: dict[str, dict] = {}
    for hit in hits:
        domain = _domain(hit["url"])
        if not domain or domain in by_domain:
            continue
        by_domain[domain] = {
            "company": company_name(hit.get("title") or "", industry) or domain,
            "website": _origin(hit["url"]),
            "title": hit.get("title") or "",
            "snippet": hit.get("snippet") or "",
        }
        if len(by_domain) >= 12:
            break
    candidates = list(by_domain.values())
    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(pool.map(lambda item: fetch_page(item["website"], timeout=6), candidates))
    scored = []
    tokens = _industry_tokens(industry)
    for item, page in zip(candidates, pages):
        if page.get("url") and _directory(page["url"]):
            continue
        text = page.get("text") or ""
        if not page.get("ok") or len(text.split()) < 40:
            continue
        if DIRECTORY_PAGE.search(text[:1800]):
            continue
        if re.search(r"\b(search guide|business directory|email finder|list of companies)\b", f"{page.get('title') or ''} {text[:500]}", re.I):
            continue
        name = choose_name(page, text[:700], industry, item["company"]) or name_from_domain(_domain(item["website"]))
        if not name:
            continue
        blob = f"{page.get('title') or ''} {page.get('site_name') or ''} {text[:8000]} {item['snippet']}"
        # Require industry token and at least part of location (city/state) in the page
        if not _mentions(blob, tokens):
            continue
        if location:
            parts=[p.strip().lower() for p in location.split(",") if p.strip()]
            # For US/UK addresses like "Houston, Texas" or "Houston, United States", check city/state, not full country string
            if parts and not any(part in blob.lower() for part in parts):
                # also accept TX abbreviation for Texas
                if not any(abbr in blob.lower() for abbr in ([" tx "] if "texas" in location.lower() else [])):
                    continue
        score = 1
        if page.get("ok"):
            score += 3
        if page.get("emails"):
            score += 2
        words = len(text.split())
        if words > 200:
            score += 2
        elif words > 80:
            score += 1
        fact = pick_fact(name, [dict(page, own=True)]) if text else ""
        named = choose_name(page, fact, industry, name)
        if named:
            name = named
        scored.append({
            **item,
            "company": name,
            "score": score,
            "fact": fact,
            "emails": page.get("emails") or [],
            "socials": page.get("socials") or [],
            "news_url": page.get("news_url") or "",
            "sources": [{"title": page.get("title") or item["website"], "url": page.get("url") or item["website"]}] if page.get("ok") else [],
        })
    scored.sort(key=lambda item: item["score"], reverse=True)
    chosen = assign_tiers(scored[:limit])
    news_targets = [item for item in chosen if item.get("news_url")]
    news_pages = {}
    if news_targets:
        with ThreadPoolExecutor(max_workers=4) as pool:
            fetched = list(pool.map(lambda item: fetch_page(item["news_url"], timeout=6), news_targets))
        news_pages = {item["news_url"]: page for item, page in zip(news_targets, fetched)}
    for item in chosen:
        page = news_pages.get(item.get("news_url") or "")
        recent_text = ""
        recent_url = ""
        if page and page.get("ok"):
            recent_text = pick_fact(item["company"], [dict(page, own=True)]) or ""
            recent_url = page.get("url") or item["news_url"]
        item["recent"] = (
            {"text": recent_text, "url": recent_url, "how": "page", "domain": _domain(recent_url)}
            if recent_text else {}
        )
        item["why"] = TIER_WHY[item["tier"]]
    return chosen


def _industry_tokens(industry: str) -> list[str]:
    stop = {"company", "companies", "firm", "firms", "business", "businesses", "the", "and", "for"}
    tokens = [token for token in re.findall(r"[a-z0-9]+", industry.lower()) if len(token) > 3 and token not in stop]
    return tokens or ["company"]


def _mentions(text: str, tokens: list[str]) -> bool:
    lowered = (text or "").lower()
    return any(token in lowered for token in tokens)


def name_from_domain(domain: str) -> str:
    stem = (domain or "").split(".")[0]
    stem = re.sub(r"(construction|engineering|builders?|group|company|limited|ltd|plc)", r" \1 ", stem, flags=re.I)
    words = [word.capitalize() for word in re.findall(r"[A-Za-z]{3,}", stem)]
    if not words or words[0].lower() in {"www", "the", "best", "top"}:
        return ""
    return " ".join(words[:4])


def choose_name(page: dict, fact: str, industry: str, fallback: str) -> str:
    fact_name = ""
    match = re.search(
        r"\b([A-Z][\w&.'\-]+(?: [\w&.'\-]+){0,5} (?:Limited|Ltd|Plc))\b",
        fact or "",
    )
    if match:
        fact_name = match.group(1)
    for candidate in (fact_name, page.get("site_name") or "", page.get("title") or "", fallback):
        cleaned = re.sub(r"\s+", " ", candidate or "").strip(" .|-")
        if not cleaned or len(cleaned) > 42:
            continue
        if GENERIC.search(cleaned) or re.search(r"\bin (lagos|nigeria)\b", cleaned, re.I):
            continue
        if "." in cleaned and " " not in cleaned:
            continue
        return cleaned
    return ""


def company_name(title: str, industry: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    parts = re.split(r"\s+[|\-–—:]\s+", title) or [title]
    industry_low = (industry or "").lower()
    for part in parts:
        cleaned = part.strip(" .")
        low = cleaned.lower()
        if not cleaned or len(cleaned) > 70 or len(cleaned) < 2:
            continue
        if GENERIC.search(cleaned):
            continue
        if industry_low and industry_low in low and len(cleaned.split()) <= 5:
            continue
        if low in {"home", "about", "contact", "blog", "news"}:
            continue
        return cleaned
    return ""


def assign_tiers(items: list[dict]) -> list[dict]:
    items = list(items[:6])
    count = len(items)
    if not count:
        return []
    base, extra = divmod(count, 3)
    sizes = [base + (1 if index < extra else 0) for index in range(3)]
    labels = ("top", "middle", "thin")
    grouped = []
    cursor = 0
    for size, label in zip(sizes, labels):
        for _ in range(size):
            item = dict(items[cursor])
            item["tier"] = label
            grouped.append(item)
            cursor += 1
    return grouped


def _directory(url: str) -> bool:
    host = _domain(url)
    return any(token in host or token in url.lower() for token in DIRECTORY)


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme or "https", parsed.netloc, "/", "", "", ""))
