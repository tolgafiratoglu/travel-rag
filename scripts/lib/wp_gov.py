"""Extract government (*.gov.*) links from Wikipedia HTML."""

from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup

_GOV_HOST = re.compile(r"\.gov\.", re.IGNORECASE)


def is_gov_host(host: str) -> bool:
    """True when hostname matches *.gov.* (e.g. kultur.gov.tr, www.gov.uk)."""
    return bool(host and _GOV_HOST.search(host))


def extract_gov_links(html: str, *, page_url: str = "") -> list[dict[str, str]]:
    """Return unique {link_url, link_text, domain} for hrefs whose host has .gov."""
    if not html or not html.strip():
        return []

    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue
        absolute = urllib.parse.urljoin(page_url or "https://en.wikipedia.org/", href)
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        host = (parsed.hostname or "").lower()
        if not is_gov_host(host):
            continue
        # Normalize: drop fragment
        link_url = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, "")
        )
        if link_url in seen:
            continue
        seen.add(link_url)
        text = a.get_text(" ", strip=True)[:512]
        out.append(
            {
                "link_url": link_url,
                "link_text": text,
                "domain": host,
            }
        )
    return out
