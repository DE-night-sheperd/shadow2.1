"""
Minimal web-access tool for the agent.

Deliberately narrow: GET-only, no auth headers, no cookies persisted,
size-capped, optional domain allowlist. This is not a general browser --
it is meant for "read this doc / API response" style lookups the agent
needs while executing an autonomous plan.
"""
import re
import urllib.request
import urllib.error
from urllib.parse import urlparse

from config import load_config


def _domain_allowed(url, allowlist):
    if not allowlist:
        return True
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in allowlist)


def fetch_url(url):
    """
    Fetch a URL with GET and return a truncated, tag-stripped text version.
    Returns a string starting with '[web_fetch error]' on failure -- callers
    should treat that prefix as a soft-fail signal, not raise.
    """
    cfg = load_config()
    if not cfg.get("allow_web_fetch", False):
        return "[web_fetch error] web fetch is disabled in config.json"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "[web_fetch error] only http/https URLs are allowed"

    if not _domain_allowed(url, cfg.get("web_fetch_allowlist", [])):
        return f"[web_fetch error] domain not in allowlist: {parsed.netloc}"

    max_bytes = cfg.get("web_fetch_max_bytes", 200_000)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ShadowCore-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(max_bytes)
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.URLError as e:
        return f"[web_fetch error] {e}"
    except Exception as e:
        return f"[web_fetch error] {e}"

    text = raw.decode("utf-8", errors="replace")

    if "html" in content_type or "<html" in text.lower()[:1000]:
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

    return text[:8000]
