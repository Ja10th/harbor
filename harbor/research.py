"""Read a public company page and keep one sentence that was actually there."""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, unquote, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from harbor.net import is_public_url, resolves_public
from harbor.socials import visit_public_posts

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except ImportError:
        DDGS = None  # type: ignore

UA = "Mozilla/5.0 (compatible; HarborDesk/0.1; personal outreach research)"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}
TIMEOUT = httpx.Timeout(8.0, connect=4.0)
SKIP_HOSTS = (
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "tiktok.com", "pinterest.", "youtube.com", "youtu.be", "reddit.com",
)
JUNK = re.compile(
    r"\b(cookie|subscribe|sign up|newsletter|all rights reserved|privacy policy|"
    r"terms of use|accept all|log in|sign in|javascript|enable javascript|"
    r"contact us|contact our team|use the form below|submit an email|find out more about our property|planning an investment strategy|finding a home to lease|"
    r"request a review|provider portal|award badges|make changes to all of your listings|find a professional near you|hire the best local professionals)\b",
    re.I,
)


def _industry_location_from_reason(reason: str) -> tuple[str, str]:
    # \"Public search for {industry} in {location}.\" is how Harbor records a discover.
    match = re.search(r"Public search for (.+?) in ([^.]+)\.", reason or "", flags=re.I)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", ""


def research_company(company: str, website: str, reason: str = "", industry: str = "", location: str = "") -> dict:
    """Deep company research — like Folio: read homepage + services/projects/team/about + search fallback.

    Fetches up to 8 own-site pages (homepage + promising internal links + static fallbacks),
    scores every sentence (Folio-style), keeps top 3-5 claims as evidence.
    Emails only from own site. Socials are links, not scraped posts.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    company = " ".join((company or "").split())
    website = (website or "").strip() or find_website(company)
    if (not industry or not location) and reason:
        guessed_industry, guessed_location = _industry_location_from_reason(reason)
        industry = industry or guessed_industry
        location = location or guessed_location
    t0 = time.time()
    pages: list[dict] = []
    sources: list[dict] = []
    errors: list[str] = []
    own_pages: list[dict] = []

    # Build URL list: start with static fallbacks, then discover promising links from homepage
    static_urls = _own_urls(website)
    # Try to discover promising internal URLs from homepage quickly
    discovered: list[str] = []
    homepage_html = ""
    try:
        # fetch homepage raw to extract links (fast, 4s timeout)
        import httpx
        with httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=True, headers=HEADERS) as client:
            resp = client.get(website, headers=HEADERS)
            resp.raise_for_status()
            homepage_html = resp.text[:120000]
            discovered = _discover_promising_urls(website, homepage_html, limit=6)
    except Exception:
        discovered = []
    # Merge: homepage + discovered + static, deduped, keep up to 8
    all_urls: list[str] = []
    seen = set()
    for u in [website] + discovered + static_urls:
        if not u:
            continue
        key = u.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        all_urls.append(u)
        if len(all_urls) >= 8:
            break

    # Fetch in parallel (like Folio's _fetch_hits)
    def _fetch_one(u: str) -> dict:
        pg = fetch_page(u)
        pg["own"] = True
        return pg
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {pool.submit(_fetch_one, u): u for u in all_urls}
        for fut in as_completed(futs):
            try:
                pg = fut.result()
            except Exception as e:
                pg = {"url": futs[fut], "ok": False, "error": str(e), "own": True, "text": "", "title": ""}
            own_pages.append(pg)
            _keep(pg, pages, sources, errors, own=True)
    # Sort own_pages by ok/word count so best pages first for scoring
    own_pages.sort(key=lambda p: (p.get("ok"), len((p.get("text") or "").split())), reverse=True)

    emails = _emails_from_own(website, own_pages)
    socials = _socials_from_own(own_pages)
    for net in ["instagram", "facebook", "youtube", "linkedin"]:
        if not any(item["network"] == net for item in socials):
            found = find_social(company, net)
            if found:
                socials.append(found)
    if not any(item["network"] == "x" for item in socials):
        found = find_social(company, "x")
        if found and found.get("network") == "x":
            socials.append(found)

    # If still thin (no good fact), search fallback like Folio: 2 queries, diversify, fetch 3 pages
    if not _fact_ready(company, pages):
        queries = [f'"{company}"', f'"{company}" {industry or "services"}']
        seen_search = set(s["url"] for s in sources)
        search_pages: list[dict] = []
        for q in queries:
            hits = _rank_hits(company, search_web(q, 6), website)
            for hit in hits:
                if hit["url"] in seen_search or _skip_host(hit["url"]) or (website and _same_site(website, hit["url"])):
                    continue
                pg = fetch_page(hit["url"])
                if not pg.get("title"):
                    pg["title"] = hit.get("title") or ""
                if not _title_matches(company, pg.get("title") or hit.get("title") or ""):
                    continue
                pg["own"] = False
                _keep(pg, pages, sources, errors, own=False)
                search_pages.append(pg)
                seen_search.add(hit["url"])
                if _fact_ready(company, pages) and len(search_pages) >= 2:
                    break
            if _fact_ready(company, pages):
                break

    post = visit_public_posts(company, socials, location, industry)
    recent = post or find_recent(company, own_pages)

    # Deep fact extraction: keep top 3-5 claims (Folio-style) instead of single pick_fact
    top_facts = _pick_top_facts(company, pages, limit=5)
    fact = top_facts[0]["text"] if top_facts else pick_fact(company, pages)
    # Build evidence claims for orchestrator (source URL + claim + confidence)
    evidence_claims = []
    for tf in top_facts:
        evidence_claims.append({
            "claim": tf["text"],
            "url": tf.get("url") or (sources[0]["url"] if sources else website),
            "title": tf.get("title") or "",
            "confidence": "high" if tf.get("score", 0) > 2.5 else "medium",
            "score": tf.get("score", 0),
        })
    if fact:
        error = ""
    elif errors:
        error = "Could not read a public page from here. Type a detail you know."
    else:
        error = "No public detail found. Type one you can stand behind."
    # Return richer payload but keep backward compat keys
    return {
        "fact": fact,
        "facts": [c["claim"] for c in evidence_claims],
        "evidence_claims": evidence_claims,
        "sources": sources[:6],
        "error": error,
        "emails": emails[:5],
        "socials": socials[:6],
        "recent": recent,
        "website": website,
        "pages_fetched": len(own_pages),
        "elapsed_ms": int((time.time() - t0) * 1000),
    }

def find_recent(company: str, pages: list[dict]) -> dict:
    """One note from a page Harbor opened. Not an Instagram login bypass.
    Tries the /blog or /news listing, then follows the first article link for a real sentence.
    """
    for page in pages:
        news = page.get("news_url") or ""
        if not news or not is_public_url(news):
            continue
        listing = fetch_page(news)
        # Try to find a real article inside the listing — first /blog/xxx that is not the listing itself
        try:
            import httpx
            # quick fetch raw for link extraction
            with httpx.Client(timeout=httpx.Timeout(6.0, connect=3.0), follow_redirects=True, headers=HEADERS) as client:
                resp = client.get(news)
                resp.raise_for_status()
                html = resp.text[:120000]
                base_host = _domain(news)
                for href in re.findall(r'''href=["']([^"'#]+)["']''', html, flags=re.I)[:80]:
                    abs_url = urljoin(news, href.strip())
                    if _domain(abs_url) != base_host or not is_public_url(abs_url):
                        continue
                    path = urlparse(abs_url).path.lower()
                    # article-like: /blog/xxxx with extra segment, not just /blog or /blog/
                    if path.startswith("/blog/") and len(path) > len("/blog/") and not path.endswith((".jpg",".png",".pdf")):
                        # avoid listing itself
                        if abs_url.rstrip("/") == news.rstrip("/"):
                            continue
                        candidates.append(abs_url)
                    elif path.startswith("/news/") and len(path) > len("/news/"):
                        candidates.append(abs_url)
                # Prefer most recent (first) candidate
                if candidates:
                    article = fetch_page(candidates[0])
                    if article.get("text"):
                        text = pick_fact(company, [dict(article, own=True)])
                        if text and not TOC_START.search(text) and "administrator" not in text.lower():
                            low = text.lower()
                            tokens = [t for t in re.findall(r"[a-z0-9]+", company.lower()) if len(t) > 3]
                            has_token = any(t in low for t in tokens)
                            has_project = bool(re.search(r"\b(project|construction|building|engineering|contractor|estate|residential|commercial|tenant|property|maintenance)\b", low))
                            if has_token or has_project or len(text.split()) >= 12:
                                return {
                                    "text": text[:260],
                                    "url": article.get("url") or candidates[0],
                                    "how": "page",
                                    "domain": _domain(article.get("url") or candidates[0]),
                                }
        except Exception:
            pass
        # Fallback: use listing itself if article fetch failed
        extra = listing
        text = pick_fact(company, [dict(extra, own=True)]) if extra.get("text") else ""
        if not text:
            continue
        if TOC_START.search(text):
            continue
        if re.match(r"^\s*\d{1,2}\s+\w+\s+\d{4}", text):
            continue
        if "administrator" in text.lower():
            continue
        low = text.lower()
        tokens = [t for t in re.findall(r"[a-z0-9]+", company.lower()) if len(t) > 3]
        has_token = any(t in low for t in tokens)
        has_project = bool(re.search(r"\b(project|construction|building|engineering|contractor|estate|residential|commercial)\b", low))
        if not (has_token or has_project):
            continue
        return {
            "text": text[:260],
            "url": extra.get("url") or news,
            "how": "page",
            "domain": _domain(extra.get("url") or news),
        }
    return {}


def news_url_from(raw: str, page_url: str) -> str:
    host = _domain(page_url)
    if not host:
        return ""
    for href in re.findall(r"""href=["']([^"'#]+)""", raw or "", flags=re.I)[:80]:
        absolute = urljoin(page_url, href.strip())
        if _domain(absolute) != host or not is_public_url(absolute):
            continue
        path = urlparse(absolute).path.lower()
        if any(token in path for token in ("/news", "/blog", "/projects", "/project", "/insights", "/press")):
            return absolute
    return ""


def site_name_from(raw: str, title: str) -> str:
    for pattern in (
        r'property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)',
        r'content=["\']([^"\']+)["\'][^>]*property=["\']og:site_name["\']',
    ):
        match = re.search(pattern, raw or "", flags=re.I)
        if match:
            return _one(match.group(1))[:80]
    return ""


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


BOAST_PICK = re.compile(
    r"\b(leading|best|world-class|unparalleled|dream|excellence|committed to|"
    r"reputation for|your vision|skilled team delivers|among the|top construction)\b",
    re.I,
)
# TOC / heading list that is not a real sentence — common on blog listings
TOC_START = re.compile(r"^\s*(in this article|in this post|table of contents|contents:|what you can)\b", re.I)



def pick_fact(company: str, pages: list[dict]) -> str:
    tokens = [token for token in re.findall(r"[a-z0-9]+", (company or "").lower()) if len(token) > 2]
    best = ""
    best_score = 0
    for page in pages:
        own = bool(page.get("own"))
        for sentence in _sentences(page.get("text") or "")[:80]:
            if len(sentence) < 50 or len(sentence) > 260 or JUNK.search(sentence):
                continue
            if BOAST_PICK.search(sentence):
                continue
            if TOC_START.search(sentence):
                continue
            # Skip contact-form / navigation / directory boilerplate
            low_s = sentence.lower()
            if any(p in low_s for p in ["contact our team", "use the form below", "submit an email", "contact us find out more", "if you would like to contact",
                                         "request a review", "provider portal", "award badges", "make changes to all of your listings",
                                         "find a professional near you", "hire the best local professionals"]):
                continue
            if low_s.startswith("contact ") and len(sentence.split()) < 15:
                continue
            # Skip truncated founder headers like "mo Hashem Founder — Built Flat Fee"
            if "founder" in low_s and "built" in low_s and len(sentence.split()) < 22:
                continue
            if sentence.lower().count("—") >= 2 and len(sentence.split()) < 25:
                continue
            # Skip heading-list fragments that are just concatenated titles
            if sentence.count(":") >= 2 and len(sentence.split()) < 25:
                continue
            if "What you can" in sentence and "itemization" in sentence.lower():
                continue
            lowered = sentence.lower()
            score = sum(3 for token in tokens if token in lowered)
            # Prioritize relatable operational detail over generic tagline
            # Sentences with concrete operations are more human to quote
            operational_bonus = 0
            if any(k in lowered for k in ["tenant screening", "maintenance", "vendors", "repairs", "emergencies", "lease", "marketing", "advertising", "financial documentation", "move-in"]):
                operational_bonus = 8
            elif any(k in lowered for k in ["tenant", "maintenance", "screening", "repair", "lease", "vendor", "dispatch", "booking", "scheduling"]):
                operational_bonus = 5
            # Penalize overly generic tagline concatenations
            if "trusted partner" in lowered and "full-service" in lowered:
                operational_bonus -= 4
            score += operational_bonus
            if tokens and score < 3 and operational_bonus == 0:
                continue
            if not tokens and not own and operational_bonus == 0:
                continue
            if own:
                score += 2
            if score > best_score:
                best = sentence
                best_score = score
    if best:
        return _tidy(company, best)
    # Own pages may have a useful sentence that does not repeat the name.
    for page in pages:
        if not page.get("own"):
            continue
        for sentence in _sentences(page.get("text") or "")[:80]:
            low_s2 = sentence.lower()
            if len(sentence) < 50 or len(sentence) > 260 or JUNK.search(sentence) or BOAST_PICK.search(sentence):
                continue
            if TOC_START.search(sentence):
                continue
            if any(p in low_s2 for p in ["request a review", "provider portal", "award badges", "find a professional", "hire the best"]):
                continue
            if "founder" in low_s2 and "built" in low_s2 and len(sentence.split()) < 22:
                continue
            return _tidy(company, sentence)
    for page in pages:
        description = (page.get("description") or "").strip()
        if description and not JUNK.search(description) and not BOAST_PICK.search(description) and len(description) >= 40:
            return _tidy(company, description[:260])
    return ""

# --- Harbor deep scoring (Folio-style) — keep multiple claims ---
STOPWORDS_HARBOR = frozenset(
    "a an the and or but if then else when while of to for from in on at by with without into over after before about as is are was were be been being it its this that these those they them their we you your i me my our not no nor so than too very can could should would will just also more most other such only own same do does did doing done have has had having what which who whom whose why how where when research tell explain describe please page home using used use make makes made get gets".split()
)

def _harbor_content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9.+-]{1,}", (text or "").lower()) if w not in STOPWORDS_HARBOR and len(w) >= 3}

def _score_harbor_sentence(sentence: str, query_terms: set[str], company: str, index: int, weight: float, about: bool) -> float | None:
    if not (50 <= len(sentence) <= 340):
        return None
    if JUNK.search(sentence):
        return None
    if BOAST_PICK.search(sentence):
        return None
    if sentence.count("/") >= 2 and sentence.count(" ") < 10:
        return None
    if sentence.endswith("?"):
        return None
    if re.match(r"^(in this|this (?:article|post|guide)|today we|here we)\b", sentence, re.I):
        return None
    if sentence.lower().startswith(("design", "discover", "explore", "learn", "see how", "get started", "build with")):
        return None
    if re.search(r"\(\+234\)|Copyright ©|WhatsApp", sentence):
        return None
    if re.match(r"^\d+\s+[A-Z]", sentence):
        return None
    if len(re.findall(r"[A-Z][a-z]+", sentence)) > 8 and not any(k in sentence.lower() for k in ["is", "are", "provides", "offers", "cater"]):
        return None
    words = _harbor_content_words(sentence)
    if len(words) < 6:
        return None
    # Operational bonus — what Harbor cares about for cold email personalization (heavily weighted)
    low = sentence.lower()
    operational_bonus = 0
    if any(k in low for k in ["tenant screening", "maintenance", "vendors", "repairs", "emergencies", "lease", "marketing", "advertising", "financial documentation", "move-in", "property management", "tenant", "landlord", "owner", "resident"]):
        operational_bonus = 12 if any(k in low for k in ["tenant screening", "maintenance", "vendors", "repairs"]) else 7
    elif any(k in low for k in ["construction", "residential", "commercial", "industrial", "contracting", "projects", "heritage", "retail", "education", "design", "material sourcing", "building"]):
        operational_bonus = 9
    elif any(k in low for k in ["services", "specializing", "cater", "expertise", "craftsmanship", "premises", "locations", "range"]):
        operational_bonus = 4
    # Company name bonus
    score = 0.0
    overlap = len(words & query_terms)
    coverage = overlap / max(len(query_terms), 1) if query_terms else 0
    score += min(2.0, coverage * 2.2)
    if company and company.lower() in sentence.lower():
        score += 0.9
    elif about:
        score += 0.6
    if index < 2:
        score += 0.5
    elif index < 5:
        score += 0.25
    if 80 <= len(sentence) <= 230:
        score += 0.45
    if CONCRETE.search(sentence) if 'CONCRETE' in globals() else False:
        score += 0.5
    if any(k in low for k in ["provides", "offers", "specializes", "delivers", "operates", "manages", "handles", "covers", "includes", "based"]):
        score += 0.4
    score += operational_bonus * 0.32
    if operational_bonus == 0 and not any(k in low for k in ["provides", "offers", "specializes", "delivers", "operates", "manages", "handles", "covers"]):
        score -= 0.6
    score *= weight
    if score < 0.85 and overlap == 0 and operational_bonus == 0:
        return None
    return score

def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

def _pick_top_facts(company: str, pages: list[dict], limit: int = 5) -> list[dict]:
    """Folio-style: score every sentence across all pages, keep top diverse claims."""
    query_terms = _harbor_content_words(company)
    candidates: list[dict] = []
    for doc in pages:
        text = (doc.get("text") or "").strip()
        if not text:
            continue
        weight = 1.0 if doc.get("own") else 0.72
        # official / services pages get slight boost
        url = (doc.get("url") or "").lower()
        if any(k in url for k in ["/services", "/projects", "/about"]):
            weight += 0.18
        about = bool(company and company.lower() in ((doc.get("title") or "") + (doc.get("url") or "")).lower())
        for idx, sentence in enumerate(_sentences(text)):
            # use harbor scorer
            s = _score_harbor_sentence(sentence, query_terms, company, idx, weight, about)
            if s is None:
                continue
            # extra junk checks already in scorer, but also founder etc.
            low_s = sentence.lower()
            if any(p in low_s for p in ["contact our team", "use the form below", "submit an email", "request a review", "provider portal"]):
                continue
            if "founder" in low_s and "built" in low_s and len(sentence.split()) < 22:
                continue
            candidates.append({
                "text": _tidy(company, sentence),
                "score": s,
                "words": _harbor_content_words(sentence),
                "url": doc.get("url") or "",
                "title": doc.get("title") or "",
                "domain": _domain(doc.get("url") or ""),
            })
    # de-duplicate / cluster by jaccard
    candidates.sort(key=lambda c: c["score"], reverse=True)
    clusters: list[dict] = []
    for cand in candidates:
        placed = False
        for cl in clusters:
            if _jaccard(cand["words"], cl["words"]) >= 0.52:
                # merge sources
                if cand["url"] not in cl.get("urls", []):
                    cl.setdefault("urls", []).append(cand["url"])
                placed = True
                break
        if not placed:
            cand["urls"] = [cand["url"]]
            clusters.append(cand)
            if len(clusters) >= limit * 2:
                break
    # MMR-ish diversity: penalize redundancy, reward domain diversity
    selected: list[dict] = []
    pool = clusters[:]
    seen_domains: set[str] = set()
    while pool and len(selected) < limit:
        best = None
        best_val = -1e9
        for cand in pool:
            redundancy = max((_jaccard(cand["words"], s["words"]) for s in selected), default=0)
            diversity = 0.28 if cand.get("domain") and cand["domain"] not in seen_domains else 0
            val = cand["score"] - 0.72 * redundancy + diversity
            if val > best_val:
                best_val = val
                best = cand
        if best is None:
            break
        if best["score"] < 0.75 and len(selected) >= 2:
            break
        selected.append(best)
        seen_domains.add(best.get("domain") or "")
        pool.remove(best)
    return selected


def search_web(query: str, limit: int = 5) -> list[dict]:
    hits = _search_ddgs(query, limit)
    if not hits:
        hits = _search_html(query, limit)
    clean = []
    seen = set()
    for hit in hits:
        url = (hit.get("url") or "").strip()
        if not url or not is_public_url(url) or _skip_host(url) or url in seen:
            continue
        seen.add(url)
        clean.append({
            "title": _one(hit.get("title") or url)[:180],
            "url": url,
            "snippet": _one(hit.get("snippet") or "")[:240],
        })
        if len(clean) >= limit:
            break
    return clean


def fetch_page(url: str, timeout: httpx.Timeout | float | None = None) -> dict:
    empty = {
        "url": url, "title": "", "text": "", "description": "",
        "ok": False, "error": "", "emails": [], "socials": [], "news_url": "",
    }
    if not is_public_url(url):
        empty["error"] = "That address is not a public web page."
        return empty
    host = urlparse(url).hostname or ""
    if not resolves_public(host):
        empty["error"] = "That address does not resolve to a public site."
        return empty
    try:
        client_timeout = timeout if isinstance(timeout, httpx.Timeout) else (
            httpx.Timeout(float(timeout), connect=4.0) if timeout else TIMEOUT
        )
        with httpx.Client(timeout=client_timeout, follow_redirects=True, headers=HEADERS) as client:
            response = client.get(url)
            response.raise_for_status()
            final = str(response.url)
            final_host = urlparse(final).hostname or ""
            if not is_public_url(final) or not resolves_public(final_host):
                empty["error"] = "The page redirected away from a public site."
                return empty
            ctype = (response.headers.get("content-type") or "").lower()
            if "html" not in ctype and "text/" not in ctype:
                empty["error"] = "That page is not HTML."
                return empty
            raw = response.text[:800_000]
    except Exception as exc:
        empty["error"] = f"{type(exc).__name__}"
        return empty
    title, description, text = _readable(raw, final)
    contacts = extract_contacts(raw, final)
    empty.update({
        "url": final,
        "title": title[:180],
        "site_name": site_name_from(raw, title),
        "description": description[:300],
        "text": text[:12_000],
        "emails": contacts["emails"],
        "socials": contacts["socials"],
        "news_url": news_url_from(raw, final),
        "ok": len(text.split()) >= 25,
        "error": "" if len(text.split()) >= 25 else "Page had almost no readable text.",
    })
    return empty


def _keep(page: dict, pages: list[dict], sources: list[dict], errors: list[str], own: bool) -> None:
    if page.get("ok"):
        page["own"] = own
        pages.append(page)
        sources.append({"title": page.get("title") or page["url"], "url": page["url"]})
    elif page.get("error"):
        errors.append(page["error"])


def _fact_ready(company: str, pages: list[dict]) -> bool:
    return bool(pick_fact(company, pages))


EMAIL_IN_PAGE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
SKIP_LOCAL = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "privacy", "abuse", "webmaster", "root", "postmaster", "bounce",
}
SKIP_DOMAIN = {
    "example.com", "example.org", "sentry.io", "wixpress.com", "godaddy.com",
    "schema.org", "email.com", "domain.com", "yoursite.com", "cloudflare.com",
}
SKIP_HANDLES = {
    "p", "reel", "reels", "explore", "accounts", "stories", "share", "sharer",
    "intent", "home", "about", "privacy", "legal", "watch", "playlist",
    "hashtag", "search", "plugins", "dialog", "photo.php", "pages",
}


def extract_contacts(raw: str, page_url: str) -> dict:
    """Emails and business social links published on one page. No guessing."""
    raw = raw or ""
    loosened = re.sub(r"\s*(?:\[at\]|\(at\))\s*", "@", raw, flags=re.I)
    loosened = re.sub(r"\s*(?:\[dot\]|\(dot\))\s*", ".", loosened, flags=re.I)
    emails = []
    seen = set()
    for match in list(re.findall(r"mailto:([^?\"'\s>]+)", raw, flags=re.I)) + EMAIL_IN_PAGE.findall(loosened):
        address = unquote(match).strip().strip(".").lower()
        if not _keep_email(address) or address in seen:
            continue
        seen.add(address)
        emails.append({"email": address, "page": page_url})
        if len(emails) >= 5:
            break
    socials = []
    seen_urls = set()
    for href in re.findall(r"""href=["']([^"'#]+)""", raw, flags=re.I):
        absolute = urljoin(page_url, href.strip())
        social = social_from_url(absolute)
        if not social or social["url"] in seen_urls:
            continue
        seen_urls.add(social["url"])
        socials.append(social)
        if len(socials) >= 6:
            break
    return {"emails": emails, "socials": socials}


def social_from_url(url: str) -> dict | None:
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    parts = [part for part in parsed.path.split("/") if part]
    if host == "instagram.com" and parts and parts[0].lower() not in SKIP_HANDLES and len(parts) == 1:
        handle = parts[0]
        return {"network": "instagram", "handle": handle, "url": f"https://instagram.com/{handle}", "source": "site"}
    if host in {"twitter.com", "x.com"} and parts and parts[0].lower() not in SKIP_HANDLES and len(parts) == 1:
        handle = parts[0]
        return {"network": "x", "handle": handle, "url": f"https://x.com/{handle}", "source": "site"}
    if host == "facebook.com" and parts and parts[0].lower() not in SKIP_HANDLES and "php" not in parts[0].lower():
        handle = parts[0]
        return {"network": "facebook", "handle": handle, "url": f"https://facebook.com/{handle}", "source": "site"}
    if host == "tiktok.com" and parts and parts[0].startswith("@") and parts[0].lower() not in SKIP_HANDLES:
        handle = parts[0].lstrip("@")
        return {"network": "tiktok", "handle": handle, "url": f"https://www.tiktok.com/@{handle}", "source": "site"}
    if host == "youtube.com" and parts and parts[0] in {"@", "channel", "c"} and len(parts) >= 2:
        handle = parts[1].lstrip("@")
        return {"network": "youtube", "handle": handle, "url": url.split("?")[0], "source": "site"}
    if host == "youtube.com" and parts and parts[0].startswith("@"):
        handle = parts[0].lstrip("@")
        return {"network": "youtube", "handle": handle, "url": f"https://www.youtube.com/@{handle}", "source": "site"}
    if host == "linkedin.com" and len(parts) >= 2 and parts[0] == "company":
        handle = parts[1]
        return {"network": "linkedin", "handle": handle, "url": f"https://www.linkedin.com/company/{handle}", "source": "site"}
    return None


def find_website(company: str) -> str:
    for hit in search_web(f"{company} official website", 5):
        if not _title_matches(company, hit["title"]) and not _title_matches(company, hit["url"]):
            continue
        parsed = urlparse(hit["url"])
        if not parsed.netloc or "wikipedia.org" in parsed.netloc:
            continue
        return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))
    return ""


def find_social(company: str, network: str) -> dict | None:
    """A matching public profile link. The profile page is not fetched."""
    try:
        raw = _search_ddgs(f"{company} {network}", 6)
    except Exception:
        raw = []
    tokens = [token for token in re.findall(r"[a-z0-9]+", company.lower()) if len(token) > 2]
    for hit in raw:
        social = social_from_url(hit.get("url") or "")
        if not social or social["network"] != network:
            continue
        blob = f"{social['handle']} {hit.get('title') or ''}".lower()
        if tokens and not any(token in blob for token in tokens):
            continue
        social["source"] = "search"
        return social
    return None


def _keep_email(address: str) -> bool:
    if not EMAIL_IN_PAGE.fullmatch(address or ""):
        return False
    local, _, domain = address.partition("@")
    if local in SKIP_LOCAL or domain in SKIP_DOMAIN or domain.endswith((".png", ".jpg", ".svg", ".webp")):
        return False
    if len(local) > 40 or ".." in address:
        return False
    return True


PROMISING_PATH_RE = re.compile(r"/(services?|our-services|what-we-do|capabilities|solutions|expertise|projects?|portfolio|works?|case[-_]?studies?|team|our-team|people|about|about-us|who-we-are|company)(/|$)", re.I)

def _own_urls(website: str) -> list[str]:
    if not website:
        return []
    parsed = urlparse(website)
    if not parsed.netloc:
        return []
    base = f"{parsed.scheme}://{parsed.netloc}"
    urls = [website]
    for path in ("/about", "/about-us", "/services", "/services/", "/projects", "/projects/", "/team", "/our-team", "/contact", "/contact-us", "/blog", "/news"):
        urls.append(urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")))
    # dedupe, keep up to 8
    clean = []
    seen = set()
    for url in urls:
        key = url.rstrip("/").lower() or url
        if key in seen:
            continue
        seen.add(key)
        clean.append(url)
        if len(clean) >= 8:
            break
    return clean

def _discover_promising_urls(website: str, homepage_html: str, limit: int = 6) -> list[str]:
    if not homepage_html or not website:
        return []
    base_host = _domain(website)
    if not base_host:
        return []
    found = []
    seen = set()
    for href in re.findall(r'''href=["']([^"'#]+)["']''', homepage_html or "", flags=re.I)[:120]:
        abs_url = urljoin(website, href.strip())
        if _domain(abs_url) != base_host or not is_public_url(abs_url):
            continue
        path = urlparse(abs_url).path.lower()
        if not PROMISING_PATH_RE.search(path):
            continue
        if any(x in path for x in [".jpg", ".png", ".pdf", ".zip"]):
            continue
        key = abs_url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(abs_url)
        if len(found) >= limit:
            break
    return found


def _emails_from_own(website: str, pages: list[dict]) -> list[dict]:
    found = []
    seen = set()
    for page in pages:
        if website and page.get("url") and not _same_site(website, page["url"]):
            continue
        for item in page.get("emails") or []:
            address = item.get("email") or ""
            if address in seen:
                continue
            seen.add(address)
            found.append(item)
    return found


def _socials_from_own(pages: list[dict]) -> list[dict]:
    found = []
    seen = set()
    for page in pages:
        for item in page.get("socials") or []:
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            found.append(item)
    return found


def _about_url(website: str) -> str:
    parsed = urlparse(website)
    if not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, "/about", "", "", ""))


def _rank_hits(company: str, hits: list[dict], website: str) -> list[dict]:
    tokens = [token for token in re.findall(r"[a-z0-9]+", (company or "").lower()) if len(token) > 2]
    ranked = []
    for hit in hits:
        title = (hit.get("title") or "").lower()
        url = (hit.get("url") or "").lower()
        if tokens and not any(token in f"{title} {url}" for token in tokens):
            continue
        if not _title_matches(company, hit.get("title") or ""):
            continue
        score = sum(3 for token in tokens if token in title)
        score += sum(2 for token in tokens if token in url)
        if "wikipedia.org" in url:
            score += 1
        ranked.append((score, hit))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [hit for _score, hit in ranked]


def _title_matches(company: str, title: str) -> bool:
    tokens = [token for token in re.findall(r"[a-z0-9]+", (company or "").lower()) if len(token) > 3]
    if not tokens:
        return True
    blob = (title or "").lower()
    return any(token in blob for token in tokens)


def _tidy(company: str, sentence: str) -> str:
    sentence = re.sub(r"\[\d+\]", "", sentence or "")
    sentence = re.sub(r"\[[a-z]{2,10}\]", "", sentence, flags=re.I)
    sentence = _one(sentence).strip(" \"")
    words = sentence.split()
    if len(words) >= 2 and words[0].lower() == words[1].lower():
        sentence = " ".join(words[1:])
    token = (company or "").split()
    if token and sentence.lower().startswith(token[0].lower() + " " + token[0].lower()):
        sentence = sentence[len(token[0]):].strip()
    return sentence[:260]


def _same_site(website: str, url: str) -> bool:
    if not website:
        return False
    left = (urlparse(website).hostname or "").lower().removeprefix("www.")
    right = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return bool(left) and left == right


def _skip_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(("duckduckgo.com", "google.com")):
        return True
    return any(token in host for token in SKIP_HOSTS)


def _search_ddgs(query: str, limit: int) -> list[dict]:
    if DDGS is None:
        return []
    try:
        with DDGS(timeout=12) as ddgs:
            raw = list(ddgs.text(query, max_results=limit))
    except Exception:
        return []
    hits = []
    for item in raw:
        hits.append({
            "title": item.get("title") or "",
            "url": item.get("href") or item.get("url") or "",
            "snippet": item.get("body") or item.get("snippet") or "",
        })
    return hits


def _search_html(query: str, limit: int) -> list[dict]:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=HEADERS) as client:
            response = client.post("https://html.duckduckgo.com/html/", data={"q": query})
            response.raise_for_status()
    except Exception:
        return []
    soup = BeautifulSoup(response.text, "lxml")
    hits = []
    for anchor in soup.select("a.result__a"):
        href = _unwrap_ddg(anchor.get("href") or "")
        parent = anchor.find_parent("div", class_="result") or anchor.find_parent("tr")
        snippet = ""
        if parent:
            node = parent.select_one(".result__snippet")
            if node:
                snippet = node.get_text(" ", strip=True)
        if href:
            hits.append({"title": anchor.get_text(" ", strip=True), "url": href, "snippet": snippet})
        if len(hits) >= limit:
            break
    return hits


def _unwrap_ddg(href: str) -> str:
    parsed = urlparse(href)
    query = dict(parse_qsl(parsed.query))
    if "uddg" in query:
        return unquote(query["uddg"])
    return href


def _readable(raw: str, url: str) -> tuple[str, str, str]:
    soup = BeautifulSoup(raw, "lxml")
    title = ""
    if soup.title and soup.title.string:
        title = _one(soup.title.string)
    description = ""
    for attrs in ({"name": "description"}, {"property": "og:description"}):
        node = soup.find("meta", attrs=attrs)
        if node and node.get("content"):
            description = _one(node["content"])
            break
    text = ""
    try:
        import trafilatura
        text = trafilatura.extract(raw, url=url, include_comments=False, include_tables=False) or ""
    except Exception:
        text = ""
    if len(text.split()) < 25:
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"\[edit\]", "", text, flags=re.I)
    return title, description, _one(text)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", _one(text))
    return [part.strip() for part in parts if part.strip()]


def _one(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

# --- Streaming comprehensive research (like Folio) ---
import time
from typing import Generator

def _extract_business_age(text: str) -> str:
    if not text:
        return ""
    # Prioritized patterns: history cues score 3, RC/years score 2, generic years 1
    patterns_scored = [
        (r"since\s+20\d{2}", 3),
        (r"established\s+(?:in\s+)?20\d{2}", 3),
        (r"founded\s+(?:in\s+)?20\d{2}", 3),
        (r"incorporated\s+(?:in\s+)?20\d{2}", 3),
        (r"RC[:\s]*\d{6,}", 3),
        (r"\d+\+?\s*years?\s+(?:of\s+)?(?:experience|excellence)", 2),
        (r"over\s+\d+\+?\s+years", 2),
        (r"\d+\s+years?\s+old", 1),
    ]
    best = ""
    best_score = -1
    for pat, score in patterns_scored:
        for m in re.finditer(pat, text, re.I):
            # window 120 chars before/after to avoid huge concatenated sentences
            start = max(0, m.start() - 120)
            end = min(len(text), m.end() + 140)
            window = text[start:end]
            # Try to find sentence in window containing the match
            window_sents = _sentences(window)
            chosen = ""
            for sent in window_sents:
                if m.group(0).lower() in sent.lower():
                    chosen = sent
                    break
            if not chosen:
                chosen = window
            tidied = _tidy("", chosen).strip()
            # Fix missing space after period from trafilatura ("projects.We")
            tidied = re.sub(r"\.(?=[A-Za-z])", ". ", tidied)
            tidied = re.sub(r"\s+", " ", tidied).strip()
            # If still huge (missing punctuation concatenated 2 sentences), slice around match
            if len(tidied) > 260:
                idx = tidied.lower().find(m.group(0).lower())
                if idx != -1:
                    s = max(0, idx - 90)
                    e = min(len(tidied), idx + len(m.group(0)) + 150)
                    tidied = tidied[s:e].strip(" •|-—")
                tidied = tidied[:260]
            if len(tidied) < 25:
                continue
            # RC snippet should be cleaned to start at RC or company-ish
            if "rc:" in tidied.lower():
                rc_idx = tidied.lower().find("rc:")
                if rc_idx > 40:
                    tidied = tidied[rc_idx-10:].strip() if rc_idx < 120 else tidied[rc_idx:].strip()
                    # prepend a bit of context if trimmed too much
                    if len(tidied) < 60 and rc_idx > 0:
                        tidied = _tidy("", text[max(0, m.start()-60): m.end()+80])[:240]
            # Prefer higher score, then shorter/more specific
            if score > best_score or (score == best_score and 40 < len(tidied) < len(best)):
                best = tidied[:240]
                best_score = score
                if score >= 3 and len(best) >= 40:
                    # immediate return for strong history cue
                    # but keep RC as strong too; return first strong
                    return best[:240]
    if best:
        return best[:240]
    return ""

def _extract_services(text: str, limit: int = 6) -> list[str]:
    services = []
    for sent in _sentences(text)[:100]:
        low = sent.lower()
        if any(k in low for k in ["we provide", "we offer", "we specialize", "our services", "our expertise", "we deliver"]):
            if len(sent) >= 40 and len(sent) <= 300 and not JUNK.search(sent):
                services.append(_tidy("", sent))
        elif any(k in low for k in ["commercial", "residential", "industrial", "project management", "general contracting"]):
            if len(sent) >= 40 and not BOAST_PICK.search(sent):
                services.append(_tidy("", sent))
        if len(services) >= limit:
            break
    return services[:limit]

def research_company_stream(company: str, website: str, reason: str = "", industry: str = "", location: str = "") -> Generator[dict, None, dict]:
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    start_all = time.time()
    company = " ".join((company or "").split())
    website = (website or "").strip() or find_website(company)
    if (not industry or not location) and reason:
        gi, gl = _industry_location_from_reason(reason)
        industry = industry or gi
        location = location or gl
    yield {"type": "step", "node": "agent", "title": "Agent", "message": f"Planning deep research for {company} — will check site, services, history, socials and recent posts.", "queries": [f'"{company}" {industry}'.strip(), f'"{company}" official site'], "ms": 5}
    time.sleep(0.05)
    t0 = time.time()
    static_urls = _own_urls(website)
    discovered = []
    homepage_html = ""
    try:
        import httpx
        with httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=True, headers=HEADERS) as client:
            resp = client.get(website, headers=HEADERS)
            resp.raise_for_status()
            homepage_html = resp.text[:120000]
            discovered = _discover_promising_urls(website, homepage_html, limit=6)
    except Exception:
        pass
    all_urls = []
    seen = set()
    for u in [website] + discovered + static_urls:
        if not u: continue
        key = u.rstrip("/").lower()
        if key in seen: continue
        seen.add(key)
        all_urls.append(u)
        if len(all_urls) >= 8: break
    yield {"type": "step", "node": "discover", "title": "Discover", "message": f"Found {len(all_urls)} pages to read ({len(discovered)} discovered from homepage).", "urls": all_urls, "ms": int((time.time()-t0)*1000)}
    t0 = time.time()
    pages: list[dict] = []
    sources: list[dict] = []
    errors: list[str] = []
    own_pages: list[dict] = []
    def _fetch_one(u: str) -> dict:
        pg = fetch_page(u)
        pg["own"] = True
        return pg
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {pool.submit(_fetch_one, u): u for u in all_urls}
        for fut in as_completed(futs):
            try:
                pg = fut.result()
            except Exception as e:
                pg = {"url": futs[fut], "ok": False, "error": str(e), "own": True, "text": "", "title": ""}
            own_pages.append(pg)
            _keep(pg, pages, sources, errors, own=True)
            yield {"type": "progress", "node": "read", "message": f"Read {pg.get('title') or pg.get('url')} — {'ok' if pg.get('ok') else 'thin'}", "url": pg.get("url"), "ok": pg.get("ok")}
    own_pages.sort(key=lambda p: (p.get("ok"), len((p.get("text") or "").split())), reverse=True)
    ok_count = sum(1 for p in own_pages if p.get("ok"))
    yield {"type": "step", "node": "read", "title": "Read", "message": f"Read {ok_count} of {len(own_pages)} pages ({sum(len((p.get('text') or '').split()) for p in own_pages):,} words).", "pages": [{"url": p.get("url"), "title": p.get("title"), "ok": p.get("ok"), "words": len((p.get("text") or "").split())} for p in own_pages], "ms": int((time.time()-t0)*1000)}
    t0 = time.time()
    emails = _emails_from_own(website, own_pages)
    socials = _socials_from_own(own_pages)
    for net in ["instagram", "facebook", "youtube", "linkedin"]:
        if not any(item["network"] == net for item in socials):
            found = find_social(company, net)
            if found:
                socials.append(found)
                yield {"type": "progress", "node": "socials", "message": f"Found {net}: {found.get('handle')}", "social": found}
    if not any(item["network"] == "x" for item in socials):
        found = find_social(company, "x")
        if found and found.get("network") == "x":
            socials.append(found)
            yield {"type": "progress", "node": "socials", "message": f"Found x: {found.get('handle')}", "social": found}
    yield {"type": "step", "node": "socials", "title": "Socials", "message": f"Checked socials — found {len(socials)} profiles ({', '.join(s.get('network') for s in socials) or 'none'}).", "socials": socials, "ms": int((time.time()-t0)*1000)}
    t0 = time.time()
    all_text = " ".join((p.get("text") or "")[:4000] for p in own_pages)
    business_age = _extract_business_age(all_text)
    services = _extract_services(all_text)
    if business_age:
        yield {"type": "progress", "node": "history", "message": f"Found history: {business_age[:120]}", "business_age": business_age}
    if services:
        yield {"type": "progress", "node": "services", "message": f"Found {len(services)} service lines", "services": services[:3]}
    post = visit_public_posts(company, socials, location, industry)
    recent = post or find_recent(company, own_pages)
    if recent and recent.get("text"):
        yield {"type": "step", "node": "recent", "title": "Recent", "message": f"Recent: {recent.get('text')[:120]}", "recent": recent, "ms": int((time.time()-t0)*1000)}
    else:
        try:
            hits = search_web(f'"{company}" news OR recent', 3)
            if hits:
                yield {"type": "progress", "node": "recent", "message": f"Searched recent news — {len(hits)} hits", "hits": hits[:2]}
        except Exception:
            pass
        yield {"type": "step", "node": "recent", "title": "Recent", "message": "No recent public post found — will use site facts.", "recent": recent, "ms": int((time.time()-t0)*1000)}
    t0 = time.time()
    top_facts = _pick_top_facts(company, pages, limit=5)
    fact = top_facts[0]["text"] if top_facts else pick_fact(company, pages)
    evidence_claims = []
    for tf in top_facts:
        evidence_claims.append({
            "claim": tf["text"],
            "url": tf.get("url") or (sources[0]["url"] if sources else website),
            "title": tf.get("title") or "",
            "confidence": "high" if tf.get("score", 0) > 2.5 else "medium",
            "score": tf.get("score", 0),
        })
    if not _fact_ready(company, pages):
        queries = [f'"{company}"', f'"{company}" {industry or "services"}']
        seen_search = set(s["url"] for s in sources)
        for q in queries:
            hits = _rank_hits(company, search_web(q, 6), website)
            for hit in hits:
                if hit["url"] in seen_search or _skip_host(hit["url"]) or (website and _same_site(website, hit["url"])):
                    continue
                pg = fetch_page(hit["url"])
                if not pg.get("title"):
                    pg["title"] = hit.get("title") or ""
                if not _title_matches(company, pg.get("title") or hit.get("title") or ""):
                    continue
                pg["own"] = False
                _keep(pg, pages, sources, errors, own=False)
                if _fact_ready(company, pages):
                    break
            if _fact_ready(company, pages):
                break
        if top_facts == []:
            top_facts = _pick_top_facts(company, pages, limit=5)
            fact = top_facts[0]["text"] if top_facts else fact
    yield {"type": "step", "node": "summarize", "title": "Summarize", "message": f"Kept {len(top_facts)} claims from {sum(len((p.get('text') or '').split()) for p in pages):,} words.", "claims": len(top_facts), "top_facts": [{"text": tf["text"][:200], "score": round(tf.get("score",0),2), "url": tf.get("url")} for tf in top_facts], "ms": int((time.time()-t0)*1000)}
    result = {
        "fact": fact,
        "facts": [c["claim"] for c in evidence_claims],
        "evidence_claims": evidence_claims,
        "sources": sources[:6],
        "emails": emails[:5],
        "socials": socials[:6],
        "recent": recent,
        "website": website,
        "pages_fetched": len(own_pages),
        "business_age": business_age,
        "services": services,
        "elapsed_ms": int((time.time() - start_all)*1000),
        "error": "" if fact else ("Could not read a public page." if errors else "No public detail found."),
    }
    yield {"type": "answer", "node": "answer", "title": "Answer", "message": "Research ready — comprehensive brief with sources.", "result": result, "ms": int((time.time()-start_all)*1000)}
    return result
