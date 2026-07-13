# maintainer: starlight.ai
# author: starlight.ai
# version v0.0.4
# purpose: Fetch URLs with Chrome UA, optionally extract clean markdown via trafilatura
# changelog:
#  v0.0.1 ==> initial creation
#  v0.0.2 ==> .md output includes YAML frontmatter with source url and createdAt
#  v0.0.3 ==> properly embed yaml frontmatter in markdown branch; and clarified .md.html suffix
#  v0.0.4 ==> switch from trafilatura markdown output to XML + recursive walker to
#             preserve inline [text](url) links; adds _parse_xml_node_to_md_url
#
# Design rationale:
# - Chrome 143 UA matches webfetch's approach — modern browser agents face fewer blocks.
# - 5MB cap matches webfetch. Truncated HTML is unparseable (broken tags, garbage content),
#   so we skip via content-length header check rather than Range-fetching partial content.
# - trafilatura output_format="markdown" strips <a href> URLs. We use output_format="xml"
#   with include_links=True and a recursive XML walker to recover markdown [text](url) and
#   ![alt](src) inline syntax — same approach as rag_html_to_blocks.py.
# - .md extension signals markdown content; paired with .html for the raw fetch.

# To Parallelize:
#   BASH: echo "url1\nurl2\nurl3" | xargs -P 4 -I {} python tool_webfetch.py {} --and-clean --output {}
#   PYTHON:
#     from concurrent.futures import ThreadPoolExecutor
#     from tool_webfetch import tool_webfetch
#     with ThreadPoolExecutor(max_workers=4) as pool:
#         results = list(pool.map(tool_webfetch, urls, [True]*len(urls)))

import argparse
import hashlib
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
import trafilatura


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 30


def _parse_xml_node_to_md_url(elem: ET.Element, base_url: str) -> str:
    """Walk trafilatura XML, emit text with markdown links for <ref> and <graphic>.

    Trafilatura strips <a href> in markdown output. This recovers them by
    walking the XML tree and converting <ref> -> [text](url) and <graphic>
    -> ![](url). Fragment anchors produce valid markdown fragment links.

    Args:
        elem: XML element from trafilatura output_format="xml".
        base_url: Source page URL for resolving relative paths.

    Returns:
        Text with inline markdown links.
    """
    parts: list[str] = []

    if elem.text is not None:
        parts.append(elem.text)

    for child in elem:
        if child.tag == "ref":
            text = "".join(child.itertext()).strip()
            target = child.attrib.get("target")
            if target is not None:
                href = urllib.parse.urljoin(base_url, target)
                parts.append(f"[{text}]({href})")
            else:
                parts.append(text)

        elif child.tag == "graphic":
            alt = child.attrib.get("alt") or "(image)"
            src = child.attrib.get("url") or child.attrib.get("src")
            if src is not None:
                parts.append(f"![{alt}]({src})")
            elif alt:
                parts.append(alt)

        else:
            parts.append(_parse_xml_node_to_md_url(child, base_url))

        if child.tail is not None:
            parts.append(child.tail)

    return "".join(parts)


def tool_webfetch(url: str, and_clean: bool = False) -> str | dict:
    """Fetch a URL and optionally extract clean markdown via trafilatura.

    Args:
        url: HTTP or HTTPS URL.
        and_clean: If True, returns ``{"html": str, "md": str}``.
                   If False, returns raw HTML string.

    Returns:
        str or dict — see ``and_clean``.

    Raises:
        requests.HTTPError on non-2xx.
        ValueError if response exceeds 5MB.
    """
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")

    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()

    content_length = resp.headers.get("content-length")
    if content_length and int(content_length) > MAX_BYTES:
        raise ValueError("Response too large (exceeds 5MB limit)")

    html = resp.text

    if len(html.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Response too large (exceeds 5MB limit)")

    if not and_clean:
        return html

    # extract as XML and walk to preserve inline [text](url) links
    raw = trafilatura.extract(html, output_format="xml", with_metadata=True,
                              include_comments=False, include_images=True,
                              include_tables=True, include_links=True)
    if raw is None:
        raise ValueError("[trafilatura] failed to parse html")

    root = ET.fromstring(raw)
    main = root.find("main")
    md_parts: list[str] = []
    if main is not None:
        for child in main:
            md_parts.append(_parse_xml_node_to_md_url(child, url))

    frontmatter = ("---\n"
                   f"source:\n"
                   f"  url: {url}\n"
                   f"  createdAt: {datetime.now(timezone.utc).isoformat()}\n"
                   "---\n\n")
    md = frontmatter + "\n\n".join(md_parts)
    return {"html": html, "md": md}


def main():
    parser = argparse.ArgumentParser(description="Fetch a URL with browser-like headers.")
    parser.add_argument("url", help="HTTP or HTTPS URL")
    parser.add_argument("--and-clean", action="store_true",
                        help="Run trafilatura extraction alongside raw HTML")
    parser.add_argument("--output", help="Base filename (no extension). Auto-generates from URL hash if omitted.")
    args = parser.parse_args()

    result = tool_webfetch(args.url, and_clean=args.and_clean)

    if args.output: base = Path(args.output)
    else: base = Path(hashlib.md5(args.url.encode()).hexdigest()[:12])

    if isinstance(result, dict):
        base.with_suffix(".html").write_text(result["html"])
        base.with_suffix(".html.md").write_text(result["md"])
    else:
        base.with_suffix(".html").write_text(result)


if __name__ == "__main__":
    main()
