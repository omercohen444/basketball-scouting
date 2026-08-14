#!/usr/bin/env python
"""HTTP diagnostic probe for a play-by-play source page.

Purpose: make the *next* investigation fast. It answers "what does this URL
actually return, and where might the PBP data really come from?" — it does not
attempt to extract play-by-play, and it does not reverse-engineer anything.

Named for SegevSport (the current candidate PBP source) but it hard-codes no
site: pass any URL.

Usage
-----
    python scripts/spikes/probe_segevsport.py --url https://example.com/game/123
    python scripts/spikes/probe_segevsport.py                # uses SEGEVSPORT_PROBE_URL
    python scripts/spikes/probe_segevsport.py --url ... --filter pbp --no-save

Output
------
Prints a summary and, unless --no-save, writes to artifacts/probe/<slug>/:
    response.html   raw body
    probe.json      status, headers, timing, extracted clues

`artifacts/` is git-ignored.

Exit codes: 0 ok, 1 request failed, 2 bad usage.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

# Make `src/` importable without requiring an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from basketball_scout.config import load_settings  # noqa: E402
from basketball_scout.net import enable_system_trust_store  # noqa: E402

# A plain, honest desktop UA. We are reading a public page at human pace; this
# is not evasion, it just avoids being served a degraded no-JS fallback.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Words that hint a link/script/endpoint is worth following for PBP data.
INTERESTING = (
    "pbp",
    "play-by-play",
    "playbyplay",
    "play_by_play",
    "boxscore",
    "box-score",
    "game",
    "match",
    "stats",
    "events",
    "api",
    "json",
    "feed",
    "data",
)

# Endpoint-ish strings inside inline scripts: absolute URLs, root-relative
# paths, and .json references.
_URLISH_RE = re.compile(
    r"""["'`](
        https?://[^"'`\s]{6,200}
        | /[A-Za-z0-9_\-./]{4,200}\.(?:json|aspx|ashx|php|js)(?:\?[^"'`\s]{0,120})?
        | /(?:api|services?|data|feed)/[^"'`\s]{2,160}
    )["'`]""",
    re.VERBOSE | re.IGNORECASE,
)


def slugify(url: str) -> str:
    parsed = urlparse(url)
    raw = f"{parsed.netloc}{parsed.path}".strip("/") or "root"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("_")
    return slug[:80] or "probe"


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} MB"


def is_interesting(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in INTERESTING)


def extract_clues(html: str, base_url: str) -> dict[str, object]:
    """Pull links, scripts, embedded JSON blobs, iframes and endpoint-ish strings.

    Uses BeautifulSoup when available and degrades to regex if not, so the probe
    still returns something useful on a bare environment.
    """
    clues: dict[str, object] = {}
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        clues["parser"] = "regex-fallback (beautifulsoup4 not installed)"
        clues["urlish_strings"] = sorted(set(_URLISH_RE.findall(html)))[:80]
        return clues

    soup = BeautifulSoup(html, "html.parser")
    clues["parser"] = "beautifulsoup4"
    clues["title"] = soup.title.get_text(strip=True) if soup.title else None

    links: list[dict[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        text = anchor.get_text(strip=True)[:80]
        if is_interesting(href) or is_interesting(text):
            links.append({"href": urljoin(base_url, href), "text": text})
    clues["interesting_links"] = _dedupe(links, key="href")[:60]

    scripts_src: list[str] = []
    inline_hits: list[str] = []
    inline_json_ids: list[dict[str, object]] = []
    for script in soup.find_all("script"):
        src = script.get("src")
        if src:
            scripts_src.append(urljoin(base_url, src.strip()))
            continue
        body = script.string or script.get_text() or ""
        if not body.strip():
            continue
        script_type = (script.get("type") or "").lower()
        script_id = script.get("id") or ""
        # Frameworks commonly park the whole page payload in a typed JSON tag
        # (__NEXT_DATA__, application/json, application/ld+json). That is by far
        # the cheapest place to find structured PBP if it exists.
        if "json" in script_type or script_id:
            inline_json_ids.append(
                {"id": script_id, "type": script_type or None, "length": len(body)}
            )
        inline_hits += _URLISH_RE.findall(body)

    clues["script_srcs"] = sorted(set(scripts_src))[:60]
    clues["interesting_script_srcs"] = sorted({s for s in scripts_src if is_interesting(s)})[:40]
    clues["inline_json_blocks"] = inline_json_ids[:20]
    clues["urlish_strings"] = sorted(set(inline_hits))[:80]
    clues["interesting_urlish_strings"] = sorted({s for s in inline_hits if is_interesting(s)})[:40]

    clues["iframes"] = sorted(
        {urljoin(base_url, f["src"].strip()) for f in soup.find_all("iframe", src=True)}
    )[:20]
    clues["tables"] = len(soup.find_all("table"))
    clues["forms"] = len(soup.find_all("form"))
    return clues


def _dedupe(items: list[dict[str, str]], *, key: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in items:
        if item[key] in seen:
            continue
        seen.add(item[key])
        out.append(item)
    return out


def probe(url: str, timeout: float) -> dict[str, object]:
    """Perform one GET and describe the response. Raises on network failure."""
    import requests

    started = time.monotonic()
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "he,en;q=0.8"},
        allow_redirects=True,
    )
    elapsed = time.monotonic() - started

    content_type = response.headers.get("Content-Type", "")
    body = response.content

    # basket.co.il serves UTF-8 Hebrew with no charset in Content-Type, and
    # requests then falls back to ISO-8859-1 per RFC 2616 — which mojibakes
    # every team name. Sniff the real encoding when the server didn't say.
    declared_charset = "charset=" in content_type.lower()
    detected_encoding = None
    if not declared_charset:
        detected_encoding = response.apparent_encoding or "utf-8"
        response.encoding = detected_encoding

    text = ""
    if "text" in content_type or "html" in content_type or "json" in content_type:
        text = response.text

    result: dict[str, object] = {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "requested_url": url,
        "final_url": response.url,
        "redirected": response.url != url,
        "redirect_chain": [r.url for r in response.history],
        "status_code": response.status_code,
        "reason": response.reason,
        "content_type": content_type,
        "content_length_bytes": len(body),
        "content_length_human": human_size(len(body)),
        "encoding": response.encoding,
        "charset_declared_by_server": declared_charset,
        "encoding_sniffed": detected_encoding,
        "elapsed_seconds": round(elapsed, 3),
        "response_headers": dict(response.headers),
        "server_set_cookies": sorted(response.cookies.keys()),
    }

    if "json" in content_type.lower():
        try:
            parsed = response.json()
            result["json_top_level"] = (
                sorted(parsed)[:40] if isinstance(parsed, dict) else f"array[{len(parsed)}]"
            )
        except ValueError as exc:
            result["json_parse_error"] = str(exc)
    elif text:
        result["clues"] = extract_clues(text, response.url)

    result["_text"] = text
    return result


def save_artifacts(result: dict[str, object], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    text = result.pop("_text", "") or ""
    if text:
        suffix = "json" if "json" in str(result.get("content_type", "")).lower() else "html"
        body_path = out_dir / f"response.{suffix}"
        body_path.write_text(text, encoding="utf-8")
        written["body"] = str(body_path)

    meta_path = out_dir / "probe.json"
    meta_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    written["metadata"] = str(meta_path)
    return written


def print_summary(result: dict[str, object], keyword: str | None) -> None:
    print(f"URL          : {result['requested_url']}")
    if result.get("redirected"):
        print(f"Redirected to: {result['final_url']}")
    print(f"Status       : {result['status_code']} {result.get('reason', '')}")
    print(f"Content-Type : {result.get('content_type') or '(none)'}")
    print(f"Size         : {result['content_length_human']} ({result['content_length_bytes']} bytes)")
    print(f"Elapsed      : {result['elapsed_seconds']}s")

    if "json_top_level" in result:
        print(f"JSON keys    : {result['json_top_level']}")

    clues = result.get("clues")
    if not isinstance(clues, dict):
        return

    print(f"\nTitle        : {clues.get('title')}")
    print(f"Tables/forms : {clues.get('tables')} tables, {clues.get('forms')} forms")

    def show(label: str, values: object, formatter=lambda v: str(v)) -> None:
        items = list(values) if isinstance(values, list) else []
        if keyword:
            items = [i for i in items if keyword.lower() in json.dumps(i, ensure_ascii=False).lower()]
        print(f"\n{label} ({len(items)}):")
        if not items:
            print("  (none)")
            return
        for item in items[:25]:
            print(f"  - {formatter(item)}")
        if len(items) > 25:
            print(f"  … {len(items) - 25} more (see probe.json)")

    show(
        "Interesting links",
        clues.get("interesting_links"),
        lambda i: f"{i['href']}  [{i['text']}]" if i.get("text") else i["href"],
    )
    show("Interesting script srcs", clues.get("interesting_script_srcs"))
    show(
        "Inline JSON blocks",
        clues.get("inline_json_blocks"),
        lambda i: f"id={i.get('id') or '-'} type={i.get('type') or '-'} len={i.get('length')}",
    )
    show("Endpoint-ish strings", clues.get("interesting_urlish_strings"))
    show("Iframes", clues.get("iframes"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnose a play-by-play source URL (status, size, endpoint clues).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", help="URL to probe. Falls back to SEGEVSPORT_PROBE_URL.")
    parser.add_argument("--timeout", type=float, help="Request timeout in seconds.")
    parser.add_argument("--filter", dest="keyword", help="Only show clues containing this keyword.")
    parser.add_argument("--out", help="Artifact directory (default: artifacts/probe/<slug>).")
    parser.add_argument("--no-save", action="store_true", help="Print only; write nothing.")
    parser.add_argument("--json", action="store_true", help="Print the full result as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # The source is Hebrew; a cp125x Windows console would otherwise raise
    # UnicodeEncodeError mid-report. The saved artifacts are always UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    enable_system_trust_store()  # see basketball_scout.net — required on this machine
    settings = load_settings()

    url = args.url or settings.segevsport_probe_url
    if not url:
        print(
            "No URL. Pass --url <url>, or set SEGEVSPORT_PROBE_URL in .env "
            "(see .env.example).",
            file=sys.stderr,
        )
        return 2
    if not urlparse(url).scheme:
        url = "https://" + url

    timeout = args.timeout or settings.http_timeout_seconds
    try:
        result = probe(url, timeout)
    except Exception as exc:
        print(f"PROBE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        printable = {k: v for k, v in result.items() if k != "_text"}
        print(json.dumps(printable, indent=2, ensure_ascii=False))
    else:
        print_summary(result, args.keyword)

    if not args.no_save:
        out_dir = Path(args.out) if args.out else settings.artifacts_dir / "probe" / slugify(url)
        written = save_artifacts(result, out_dir)
        print("\nSaved:")
        for label, path in written.items():
            print(f"  {label}: {path}")

    status = int(result["status_code"])  # type: ignore[arg-type]
    if status >= 400:
        print(f"\nNOTE: server returned {status} — treat the clues above with suspicion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
