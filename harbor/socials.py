"""Open a public social page the way a browser would. No login, no bypass."""

from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from harbor.net import is_public_url, resolves_public

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en"}
SKIP_TITLE = {
    "keyboard shortcuts", "playback", "search filters", "subtitles and closed captions",
    "spherical videos", "playlists", "description",
}
WELCOME = re.compile(r"^\s*welcome to\b", re.I)
BIO = re.compile(
    r"\b(followers|talking about this|watch awesome short|log in to|sign up|"
    r"make your day)\b",
    re.I,
)


def visit_public_posts(
    company: str,
    socials: list[dict] | None = None,
    location: str = "",
    industry: str = "",
) -> dict:
    """Return one real post if a public page actually contained it."""
    company = " ".join((company or "").split())
    if not company:
        return {}
    for item in socials or []:
        found = _from_profile(company, item, location, industry)
        if found:
            return found
    return _from_youtube_search(company, location, industry) or {}


def _from_profile(company: str, item: dict, location: str, industry: str) -> dict:
    url = item.get("url") or ""
    network = item.get("network") or ""
    if not url or not is_public_url(url):
        return {}
    if network == "youtube":
        url = url.rstrip("/") + "/videos"
    page = _open(url)
    if not page:
        return {}
    if network == "youtube":
        video = _first_owned_video(page["html"], company, location, industry)
        if video:
            return _video_post(video, company)
    caption = _caption_from_html(page["html"], page["url"])
    if caption and _owned(caption, company, item.get("handle") or ""):
        return {
            "text": caption[:240],
            "url": page["url"],
            "how": "page",
            "domain": _host(page["url"]),
            "network": network or _host(page["url"]),
        }
    return {}


def _from_youtube_search(company: str, location: str, industry: str) -> dict:
    url = "https://www.youtube.com/results?search_query=" + quote(company)
    page = _open(url)
    if not page:
        return {}
    video = _first_owned_video(page["html"], company, location, industry, strict=True)
    if not video:
        return {}
    return _video_post(video, company)


def _video_post(video: dict, company: str) -> dict:
    watch = f"https://www.youtube.com/watch?v={video['id']}" if video.get("id") else video.get("url") or ""
    text = video["title"]
    if watch:
        page = _open(watch)
        description = _short_description(page["html"]) if page else ""
        if description and not BIO.search(description):
            text = description
    return {
        "text": text[:240],
        "url": watch or "https://www.youtube.com/results?search_query=" + quote(company),
        "how": "page",
        "domain": "youtube.com",
        "network": "youtube",
    }


def _open(url: str) -> dict | None:
    host = ""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
    except Exception:
        return None
    if not resolves_public(host):
        return None
    try:
        with httpx.Client(timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=True, headers=HEADERS) as client:
            response = client.get(url)
            final = str(response.url)
            if response.status_code >= 400 or _login_wall(final, response.text[:2000]):
                return None
            if "html" not in (response.headers.get("content-type") or "").lower() and "text/" not in (response.headers.get("content-type") or "").lower():
                return None
            return {"url": final, "html": response.text[:1_500_000]}
    except Exception:
        return None


def _first_owned_video(html: str, company: str, location: str = "", industry: str = "", strict: bool = False) -> dict:
    titles = re.findall(r'"title":\{"runs":\[\{"text":"([^"\\]{8,160})"\}\]', html or "")
    bylines = re.findall(r'"longBylineText":\{"runs":\[\{"text":"([^"]{2,80})"', html or "")
    ids = []
    for video_id in re.findall(r'"videoId":"([\w-]{11})"', html or ""):
        if video_id not in ids:
            ids.append(video_id)
    pairs = list(zip(titles, bylines)) if bylines else [(title, company) for title in titles]
    for index, (title, channel) in enumerate(pairs):
        title = _unescape(title)
        channel = _unescape(channel)
        if title.lower() in SKIP_TITLE or len(title) < 8:
            continue
        if _keep_video(title, channel, company, location, industry, strict):
            return {"title": title, "channel": channel, "id": ids[index] if index < len(ids) else ""}
    for label in re.findall(r'"label":"((?:[^"\\]|\\.){12,180})"', html or ""):
        label = _unescape(label)
        if " by " not in label:
            continue
        title, _, rest = label.partition(" by ")
        channel = rest.split("  ")[0].strip()
        if title.lower() in SKIP_TITLE:
            continue
        if _keep_video(title, channel, company, location, industry, strict):
            return {"title": title.strip(), "channel": channel, "id": ""}
    return {}


def _caption_from_html(html: str, url: str) -> str:
    if _login_wall(url, html[:2000]):
        return ""
    for pattern in (
        r'"full_text":"((?:[^"\\]|\\.){20,220})"',
        r'"message":\{"text":"((?:[^"\\]|\\.){20,220})"',
        r'"desc":"((?:[^"\\]|\\.){20,220})"',
    ):
        for match in re.findall(pattern, html or ""):
            text = _unescape(match)
            if BIO.search(text) or "watch awesome" in text.lower():
                continue
            return text
    return ""


def _short_description(html: str) -> str:
    match = re.search(r'"shortDescription":"((?:[^"\\]|\\.){20,280})"', html or "")
    return _unescape(match.group(1)) if match else ""


def _login_wall(url: str, sample: str) -> bool:
    lowered = (url or "").lower()
    if any(token in lowered for token in ("/login", "/accounts/login", "checkpoint")):
        return True
    head = (sample or "").lower()
    return "log in to continue" in head or "sign up to continue" in head


def _keep_video(title: str, channel: str, company: str, location: str, industry: str, strict: bool = False) -> bool:
    """The channel has to be theirs. A title that merely contains the name is not enough."""
    if not _matches(channel, company):
        return False
    if WELCOME.search(title) or "here you will learn" in title.lower():
        return False
    blob = f"{title} {channel}"
    if _other_place(blob, location):
        return False
    if industry and not _mentions_topic(blob, industry) and location and location.lower() not in blob.lower():
        return False
    if strict and location:
        # For a YouTube search fallback, demand a tighter owner signal unless the place is in the text.
        here = location.lower()
        nigeria = "nigeria" in blob.lower()
        if here not in blob.lower() and not nigeria:
            tokens = [t for t in __import__('re').findall(r"[a-z0-9]+", company.lower()) if len(t) > 3]
            matched = sum(1 for t in tokens if t in channel.lower())
            if tokens and matched < min(2, len(tokens)):
                return False
    return True


OTHER_PLACES = (
    "los angeles", "new york", "london", "dubai", "manchester", "houston",
    "chicago", "toronto", "nairobi", "johannesburg", "accra", "abuja",
)


def _other_place(text: str, location: str) -> bool:
    lowered = text.lower()
    here = location.lower()
    return any(place in lowered and place not in here for place in OTHER_PLACES)


def _mentions_topic(text: str, industry: str) -> bool:
    tokens = [token for token in re.findall(r"[a-z0-9]+", industry.lower()) if len(token) > 4]
    lowered = text.lower()
    return any(token in lowered for token in tokens)


def _matches(text: str, company: str) -> bool:
    tokens = [token for token in re.findall(r"[a-z0-9]+", (company or "").lower()) if len(token) > 3]
    blob = (text or "").lower()
    if not tokens:
        return company.lower() in blob
    return any(token in blob for token in tokens)


def _owned(text: str, company: str, handle: str) -> bool:
    return _matches(text, company) or _matches(handle, company) or _matches(company, handle)


def _unescape(value: str) -> str:
    try:
        return bytes(value, "utf-8").decode("unicode_escape")
    except Exception:
        return value.replace("\\n", " ").replace('\\"', '"')


def _host(url: str) -> str:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host
