from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable


PROHIBITED_WEB_FACTS = (
    "本公司人员", "公司人员", "项目团队", "本公司业绩", "公司业绩", "本公司资质", "公司资质",
    "服务承诺", "实际品牌", "实际型号", "实际配置", "技术偏离", "投标人资质",
)


def validate_public_url(url: str, *, resolver: Callable[..., Any] = socket.getaddrinfo) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("only public http/https URLs without credentials are allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("local and internal hosts are forbidden")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(item[4][0]) for item in resolver(host, parsed.port or 443)]
        except OSError as exc:
            raise ValueError("URL host cannot be resolved safely") from exc
    for address in addresses:
        if (
            address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast or address.is_unspecified
        ):
            raise ValueError("local, private and reserved network targets are forbidden")
    return parsed


def search_web(
    provider: Any,
    *,
    query: str,
    purpose: str,
    allowed_source_types: list[str],
    allowed_domains: list[str],
    recency_days: int | None,
    top_k: int,
) -> dict[str, Any]:
    ensure_web_purpose_allowed(query, purpose)
    result = provider.search(
        query,
        {
            "purpose": purpose,
            "allowed_source_types": allowed_source_types,
            "allowed_domains": allowed_domains,
            "recency_days": recency_days,
            "top_k": top_k,
        },
    )
    domains = {value.lower().lstrip(".") for value in allowed_domains if value}
    if result.get("status") in {"success", "no_results"}:
        normalized = []
        allowed_types = set(allowed_source_types)
        for item in result.get("results") or []:
            host = (urllib.parse.urlparse(str(item.get("url") or "")).hostname or "").lower()
            if domains and not any(host == domain or host.endswith(f".{domain}") for domain in domains):
                continue
            source_type = classify_source_type(host, domains)
            if allowed_types and source_type not in allowed_types:
                continue
            normalized.append({**item, "source_type": source_type})
        result["results"] = normalized
        result["status"] = "success" if result["results"] else "no_results"
    return result


def open_web_page(
    url: str,
    *,
    purpose: str,
    max_chars: int = 12000,
    opener: Any = None,
    resolver: Callable[..., Any] = socket.getaddrinfo,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    ensure_web_purpose_allowed(url, purpose)
    validate_public_url(url, resolver=resolver)
    opener = opener or urllib.request.build_opener(_SafeRedirectHandler(resolver)).open
    request = urllib.request.Request(url, headers={"User-Agent": "comlan-technical-agent/0"})
    last_error: Exception | None = None
    for _ in range(2):
        try:
            with opener(request, timeout=timeout_seconds) as response:
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if "text/html" not in content_type and "text/plain" not in content_type:
                    return {"status": "unsupported_content_type", "url": url, "content_type": content_type}
                raw = response.read(min(max(max_chars * 4, 4096), 2_000_000))
                text = raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
                content = extract_html_text(text) if "html" in content_type else text
                content = content[: max(1, min(max_chars, 50000))]
                title = extract_html_title(text) if "html" in content_type else ""
                now = datetime.now(timezone.utc).isoformat()
                return {
                    "status": "success",
                    "url": url,
                    "title": title,
                    "publisher": urllib.parse.urlparse(url).hostname,
                    "published_at": None,
                    "retrieved_at": now,
                    "content": content,
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
    return {"status": "error", "url": url, "error": str(last_error)[:300], "content": ""}


def ensure_web_purpose_allowed(query: str, purpose: str) -> None:
    value = f"{query}\n{purpose}".lower()
    if any(term.lower() in value for term in PROHIBITED_WEB_FACTS):
        raise PermissionError("web search cannot supply enterprise/project facts or commitments")


def classify_source_type(host: str, allowed_domains: set[str]) -> str:
    if host.endswith(".gov.cn") or host.endswith(".gov") or host == "gov.cn":
        if any(term in host for term in ("samr", "sac", "standard")):
            return "standards_authority_official"
        return "government_official"
    if allowed_domains and any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
        return "manufacturer_official"
    return "general_public_web"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, resolver: Callable[..., Any]) -> None:
        super().__init__()
        self._resolver = resolver

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl, resolver=self._resolver)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def extract_html_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return "\n".join(line for line in (re.sub(r"\s+", " ", html.unescape(item)).strip() for item in parser.parts) if line)


def extract_html_title(value: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", value, flags=re.I | re.S)
    return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip() if match else ""
