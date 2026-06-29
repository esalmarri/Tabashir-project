"""
Job Feed Fetcher - Collects job application URLs from free APIs and RSS feeds.

Fetches jobs from multiple sources, deduplicates, filters for ATS URLs,
and writes them to jobs.txt for the job_applier to process.

Usage:
    python tools/job_feed_fetcher.py --dry-run
    python tools/job_feed_fetcher.py -k "software engineer" --ats-only
    python tools/job_feed_fetcher.py --sources remoteok,indeed -l "remote"
"""

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

# Fix Windows console encoding for Unicode job titles
if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
from urllib.parse import quote_plus, urlparse, urlencode, parse_qs, urlunparse

import requests

# ─── Configuration ───────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "keywords": ["software engineer", "data engineer", "developer"],
    "locations": ["remote"],
    "ats_only": False,
    "max_results_per_source": 50,
    "request_timeout": 15,
    "user_agent": "JobFeedFetcher/1.0 (personal job search tool)",
    "output_file": None,  # Auto-resolved to ../jobs.txt
    "append_mode": True,
}

# ─── ATS URL Detection ──────────────────────────────────────────────────────

ATS_PATTERNS = [
    re.compile(r'boards\.greenhouse\.io/', re.IGNORECASE),
    re.compile(r'job-boards\.greenhouse\.io/', re.IGNORECASE),
    re.compile(r'jobs\.greenhouse\.io/', re.IGNORECASE),
    re.compile(r'jobs\.lever\.co/', re.IGNORECASE),
    re.compile(r'\.myworkdayjobs\.com/', re.IGNORECASE),
    re.compile(r'\.wd\d+\.myworkdayjobs\.com/', re.IGNORECASE),
    re.compile(r'\.icims\.com/', re.IGNORECASE),
    re.compile(r'smartrecruiters\.com/', re.IGNORECASE),
    re.compile(r'jobs\.ashbyhq\.com/', re.IGNORECASE),
    re.compile(r'\.applytojob\.com/', re.IGNORECASE),
    re.compile(r'\.jobvite\.com/', re.IGNORECASE),
    re.compile(r'\.breezy\.hr/', re.IGNORECASE),
    re.compile(r'\.recruitee\.com/', re.IGNORECASE),
    re.compile(r'\.bamboohr\.com/', re.IGNORECASE),
    re.compile(r'\.jazz\.co/', re.IGNORECASE),
    re.compile(r'apply\.workable\.com/', re.IGNORECASE),
    re.compile(r'\.taleo\.net/', re.IGNORECASE),
    re.compile(r'\.successfactors\.', re.IGNORECASE),
    re.compile(r'\.rippling\.com/', re.IGNORECASE),
    re.compile(r'\.teamtailor\.com/', re.IGNORECASE),
]


def is_ats_url(url: str) -> bool:
    """Check if URL matches a known ATS domain pattern."""
    return any(p.search(url) for p in ATS_PATTERNS)


# ─── URL Resolver ────────────────────────────────────────────────────────────

# Regex to find ATS URLs in HTML
ATS_LINK_RE = re.compile(
    r'https?://(?:'
    r'(?:boards|job-boards|jobs)\.greenhouse\.io/[^\s"\'<>]+|'
    r'jobs\.lever\.co/[^\s"\'<>]+|'
    r'[\w.-]+\.myworkdayjobs\.com/[^\s"\'<>]+|'
    r'[\w.-]+\.icims\.com/[^\s"\'<>]+|'
    r'[\w.-]+\.smartrecruiters\.com/[^\s"\'<>]+|'
    r'jobs\.ashbyhq\.com/[^\s"\'<>]+|'
    r'apply\.workable\.com/[^\s"\'<>]+|'
    r'[\w.-]+\.jobvite\.com/[^\s"\'<>]+|'
    r'[\w.-]+\.breezy\.hr/[^\s"\'<>]+|'
    r'[\w.-]+\.bamboohr\.com/[^\s"\'<>]+|'
    r'[\w.-]+\.recruitee\.com/[^\s"\'<>]+'
    r')',
    re.IGNORECASE,
)


def resolve_ats_url(listing_url: str, user_agent: str, timeout: int = 10) -> Optional[str]:
    """Try to find the actual ATS application URL from a job board listing page.

    Fetches the listing page HTML and searches for links to known ATS domains.
    Returns the ATS URL if found, None otherwise.
    """
    if is_ats_url(listing_url):
        return listing_url  # Already an ATS URL

    try:
        resp = requests.get(
            listing_url,
            headers={"User-Agent": user_agent},
            timeout=timeout,
            allow_redirects=True,
        )
        resp.raise_for_status()

        # Check if we were redirected to an ATS URL
        if is_ats_url(resp.url) and resp.url != listing_url:
            return resp.url

        # Search HTML for ATS URLs
        matches = ATS_LINK_RE.findall(resp.text)
        if matches:
            # Deduplicate and prefer apply/application URLs
            unique = list(dict.fromkeys(matches))  # Preserves order, removes dupes
            # Prefer URLs with /apply or /application in the path
            for url in unique:
                if '/apply' in url.lower() or '/application' in url.lower():
                    return url
            return unique[0]

    except Exception:
        pass

    return None


def resolve_jobs(jobs: List[dict], config: dict, verbose: bool = False) -> List[dict]:
    """Attempt to resolve listing URLs to direct ATS application URLs."""
    resolved = []
    resolved_count = 0

    for job in jobs:
        if is_ats_url(job["url"]):
            resolved.append(job)
            continue

        ats_url = resolve_ats_url(job["url"], config["user_agent"], config["request_timeout"])
        if ats_url:
            if verbose:
                print(f"    Resolved: {job['url'][:50]}... -> {ats_url[:60]}...")
            job = {**job, "url": ats_url, "original_url": job["url"]}
            resolved_count += 1

        resolved.append(job)
        time.sleep(0.3)  # Be polite between requests

    if resolved_count > 0:
        print(f"  Resolved {resolved_count} listing URLs to direct ATS URLs")

    return resolved


def extract_ats_url_from_html(html: str) -> Optional[str]:
    """Extract ATS application URL from HTML content (e.g., job descriptions)."""
    matches = ATS_LINK_RE.findall(html)
    if matches:
        unique = list(dict.fromkeys(matches))
        for url in unique:
            if '/apply' in url.lower() or '/application' in url.lower():
                return url
        return unique[0]
    return None


# ─── Utility Functions ───────────────────────────────────────────────────────

TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'ref', 'source', 'gh_jid', 'gh_src', 'fbclid', 'gclid', 'mc_cid',
    'mc_eid', 'mkt_tok',
}


def normalize_url(url: str) -> str:
    """Normalize URL for deduplication."""
    try:
        parsed = urlparse(url)
        # Lowercase domain
        netloc = parsed.netloc.lower()
        # Remove tracking params
        qs = parse_qs(parsed.query, keep_blank_values=False)
        filtered_qs = {k: v for k, v in qs.items() if k.lower() not in TRACKING_PARAMS}
        new_query = urlencode(filtered_qs, doseq=True) if filtered_qs else ""
        # Strip fragment and trailing slash
        path = parsed.path.rstrip('/')
        return urlunparse((parsed.scheme or 'https', netloc, path, '', new_query, ''))
    except Exception:
        return url.rstrip('/').lower()


def matches_keywords(text: str, keywords: List[str]) -> bool:
    """Case-insensitive check if any keyword appears in text."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def parse_rss_items(xml_text: str) -> List[dict]:
    """Parse RSS XML and extract items."""
    items = []
    try:
        root = ET.fromstring(xml_text)
        # Handle RSS 2.0 format
        for item in root.iter('item'):
            title = ""
            link = ""
            description = ""
            for child in item:
                tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                if tag == 'title':
                    title = (child.text or "").strip()
                elif tag == 'link':
                    link = (child.text or "").strip()
                elif tag == 'guid' and not link:
                    link = (child.text or "").strip()
                elif tag == 'description':
                    description = (child.text or "").strip()
            if link:
                items.append({"title": title, "link": link, "description": description})
    except ET.ParseError:
        pass
    return items


# ─── Source Fetchers ─────────────────────────────────────────────────────────

def fetch_remoteok(keywords: List[str], config: dict) -> List[dict]:
    """Fetch jobs from RemoteOK API."""
    jobs = []
    try:
        resp = requests.get(
            "https://remoteok.com/api",
            headers={"User-Agent": config["user_agent"]},
            timeout=config["request_timeout"],
        )
        resp.raise_for_status()
        data = resp.json()

        # First element is metadata, skip it
        for item in data[1:]:
            url = item.get("url", "")
            title = item.get("position", "")
            company = item.get("company", "")
            tags = item.get("tags", [])
            search_text = f"{title} {company} {' '.join(tags)}"

            if not url:
                continue
            if keywords and not matches_keywords(search_text, keywords):
                continue

            jobs.append({
                "url": url,
                "title": f"{title} at {company}",
                "source": "RemoteOK",
            })

            if len(jobs) >= config["max_results_per_source"]:
                break

    except Exception as e:
        print(f"  [WARN] RemoteOK fetch failed: {e}")

    return jobs


def fetch_remotive(keywords: List[str], config: dict) -> List[dict]:
    """Fetch jobs from Remotive API."""
    jobs = []
    try:
        params = {"limit": config["max_results_per_source"]}
        # Map keywords to Remotive categories if possible
        category_map = {
            "software": "software-dev",
            "developer": "software-dev",
            "engineer": "software-dev",
            "data": "data",
            "devops": "devops-sysadmin",
            "design": "design",
            "product": "product",
            "marketing": "marketing",
            "qa": "qa",
        }
        # Try to find a matching category from keywords
        for kw in keywords:
            for cat_key, cat_val in category_map.items():
                if cat_key in kw.lower():
                    params["category"] = cat_val
                    break
            if "category" in params:
                break

        resp = requests.get(
            "https://remotive.com/api/remote-jobs",
            params=params,
            headers={"User-Agent": config["user_agent"]},
            timeout=config["request_timeout"],
        )
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("jobs", []):
            url = item.get("url", "")
            title = item.get("title", "")
            company = item.get("company_name", "")
            description = item.get("description", "")
            search_text = f"{title} {company} {' '.join(item.get('tags', []))}"

            if not url:
                continue
            if keywords and not matches_keywords(search_text, keywords):
                continue

            # Try to extract direct ATS URL from description
            ats_url = extract_ats_url_from_html(description)
            job_url = ats_url if ats_url else url

            jobs.append({
                "url": job_url,
                "title": f"{title} at {company}",
                "source": "Remotive",
            })

            if len(jobs) >= config["max_results_per_source"]:
                break

    except Exception as e:
        print(f"  [WARN] Remotive fetch failed: {e}")

    return jobs


def fetch_arbeitnow(keywords: List[str], config: dict) -> List[dict]:
    """Fetch jobs from Arbeitnow API."""
    jobs = []
    try:
        for page in range(1, 3):  # Fetch first 2 pages
            resp = requests.get(
                "https://www.arbeitnow.com/api/job-board-api",
                params={"page": page},
                headers={"User-Agent": config["user_agent"]},
                timeout=config["request_timeout"],
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("data", []):
                url = item.get("url", "")
                title = item.get("title", "")
                company = item.get("company_name", "")
                description = item.get("description", "")
                tags = item.get("tags", [])
                search_text = f"{title} {company} {' '.join(tags)}"

                if not url:
                    continue
                if keywords and not matches_keywords(search_text, keywords):
                    continue

                # Try to extract direct ATS URL from description
                ats_url = extract_ats_url_from_html(description)
                job_url = ats_url if ats_url else url

                jobs.append({
                    "url": job_url,
                    "title": f"{title} at {company}",
                    "source": "Arbeitnow",
                })

                if len(jobs) >= config["max_results_per_source"]:
                    break

            if len(jobs) >= config["max_results_per_source"]:
                break

            # Small delay between pages
            time.sleep(1)

    except Exception as e:
        print(f"  [WARN] Arbeitnow fetch failed: {e}")

    return jobs


def fetch_indeed_rss(keywords: List[str], locations: List[str], config: dict) -> List[dict]:
    """Fetch jobs from Indeed RSS feeds."""
    jobs = []
    seen_urls = set()

    # Generate keyword+location combinations
    combos = []
    for kw in (keywords or [""]):
        for loc in (locations or [""]):
            combos.append((kw, loc))

    for keyword, location in combos:
        try:
            params = {}
            if keyword:
                params["q"] = keyword
            if location:
                params["l"] = location

            url = f"https://rss.indeed.com/rss?{urlencode(params)}"
            resp = requests.get(
                url,
                headers={"User-Agent": config["user_agent"]},
                timeout=config["request_timeout"],
            )
            resp.raise_for_status()

            items = parse_rss_items(resp.text)
            for item in items:
                link = item.get("link", "")
                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)

                jobs.append({
                    "url": link,
                    "title": item.get("title", ""),
                    "source": "Indeed RSS",
                })

                if len(jobs) >= config["max_results_per_source"]:
                    break

            if len(jobs) >= config["max_results_per_source"]:
                break

            # Small delay between requests
            time.sleep(0.5)

        except Exception as e:
            print(f"  [WARN] Indeed RSS fetch failed for '{keyword}' in '{location}': {e}")

    return jobs


def fetch_weworkremotely_rss(keywords: List[str], config: dict) -> List[dict]:
    """Fetch jobs from We Work Remotely RSS."""
    jobs = []
    try:
        resp = requests.get(
            "https://weworkremotely.com/remote-jobs.rss",
            headers={"User-Agent": config["user_agent"]},
            timeout=config["request_timeout"],
        )
        resp.raise_for_status()

        items = parse_rss_items(resp.text)
        for item in items:
            link = item.get("link", "")
            title = item.get("title", "")

            if not link:
                continue
            if keywords and not matches_keywords(title, keywords):
                continue

            jobs.append({
                "url": link,
                "title": title,
                "source": "WeWorkRemotely",
            })

            if len(jobs) >= config["max_results_per_source"]:
                break

    except Exception as e:
        print(f"  [WARN] We Work Remotely RSS fetch failed: {e}")

    return jobs


def fetch_remotive_rss(keywords: List[str], config: dict) -> List[dict]:
    """Fetch jobs from Remotive RSS feed."""
    jobs = []
    try:
        resp = requests.get(
            "https://remotive.com/remote-jobs/rss-feed",
            headers={"User-Agent": config["user_agent"]},
            timeout=config["request_timeout"],
        )
        resp.raise_for_status()

        items = parse_rss_items(resp.text)
        for item in items:
            link = item.get("link", "")
            title = item.get("title", "")

            if not link:
                continue
            if keywords and not matches_keywords(title, keywords):
                continue

            jobs.append({
                "url": link,
                "title": title,
                "source": "Remotive RSS",
            })

            if len(jobs) >= config["max_results_per_source"]:
                break

    except Exception as e:
        print(f"  [WARN] Remotive RSS fetch failed: {e}")

    return jobs


# ─── Source Registry ─────────────────────────────────────────────────────────

AVAILABLE_SOURCES = {
    "remoteok": ("RemoteOK API", fetch_remoteok),
    "remotive": ("Remotive API", fetch_remotive),
    "arbeitnow": ("Arbeitnow API", fetch_arbeitnow),
    "indeed": ("Indeed RSS", None),  # Special handler (needs locations)
    "wwr": ("We Work Remotely RSS", fetch_weworkremotely_rss),
    "remotive-rss": ("Remotive RSS", fetch_remotive_rss),
}


# ─── Deduplication & File I/O ────────────────────────────────────────────────

def deduplicate_jobs(jobs: List[dict]) -> List[dict]:
    """Remove duplicate URLs after normalization. Keeps first occurrence."""
    seen = set()
    unique = []
    for job in jobs:
        norm = normalize_url(job["url"])
        if norm not in seen:
            seen.add(norm)
            unique.append(job)
    return unique


def load_existing_urls(filepath: str) -> Set[str]:
    """Read existing jobs.txt and return set of normalized URLs."""
    urls = set()
    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    urls.add(normalize_url(line))
    except FileNotFoundError:
        pass
    return urls


def load_applied_urls(tracker_path: str) -> Set[str]:
    """Read application_tracker.json and return set of normalized applied URLs."""
    urls = set()
    try:
        with open(tracker_path, 'r') as f:
            data = json.load(f)
        for entry in data.values():
            url = entry.get("job_url", "")
            if url:
                urls.add(normalize_url(url))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return urls


def write_jobs_file(jobs: List[dict], filepath: str, append: bool):
    """Write job URLs to jobs.txt."""
    mode = 'a' if append else 'w'
    with open(filepath, mode) as f:
        if append:
            f.write(f"\n# --- Fetched {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ({len(jobs)} new jobs) ---\n")
        for job in jobs:
            f.write(f"{job['url']}\n")


# ─── Main Aggregation ────────────────────────────────────────────────────────

def fetch_all_jobs(config: dict, sources: Optional[List[str]] = None, verbose: bool = False) -> List[dict]:
    """Fetch from all sources, aggregate, and deduplicate."""
    all_jobs = []
    source_counts = {}

    active_sources = sources or list(AVAILABLE_SOURCES.keys())

    for source_key in active_sources:
        if source_key not in AVAILABLE_SOURCES:
            print(f"  [WARN] Unknown source: {source_key}")
            continue

        source_name, fetcher = AVAILABLE_SOURCES[source_key]
        print(f"  Fetching from {source_name}...", end=" ", flush=True)

        if source_key == "indeed":
            # Indeed needs special handling for locations
            jobs = fetch_indeed_rss(config["keywords"], config["locations"], config)
        elif fetcher:
            jobs = fetcher(config["keywords"], config)
        else:
            jobs = []

        source_counts[source_name] = len(jobs)
        all_jobs.extend(jobs)
        status = f"[OK] {len(jobs)} jobs" if jobs else "[EMPTY]"
        print(status)

        if verbose:
            for j in jobs[:3]:
                ats_tag = " [ATS]" if is_ats_url(j["url"]) else ""
                print(f"    - {j['title'][:60]}{ats_tag}")
            if len(jobs) > 3:
                print(f"    ... and {len(jobs) - 3} more")

        # Small delay between sources to be polite
        time.sleep(0.5)

    return all_jobs


# ─── CLI ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch job application URLs from free APIs and RSS feeds.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --dry-run
  %(prog)s -k "software engineer" "data engineer" --ats-only
  %(prog)s --sources remoteok,indeed -l remote
  %(prog)s --overwrite -k backend
        """,
    )
    parser.add_argument(
        "-k", "--keywords", nargs="+",
        help="Search keywords (default: from config)",
    )
    parser.add_argument(
        "-l", "--locations", nargs="+",
        help="Location filters (default: from config)",
    )
    parser.add_argument(
        "--ats-only", action="store_true",
        help="Only output URLs from known ATS platforms",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file path (default: ../jobs.txt)",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Overwrite jobs.txt instead of appending",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print URLs to stdout without writing to file",
    )
    parser.add_argument(
        "--sources",
        help=f"Comma-separated sources: {','.join(AVAILABLE_SOURCES.keys())} (default: all)",
    )
    parser.add_argument(
        "--max-per-source", type=int,
        help="Max results per source (default: 50)",
    )
    parser.add_argument(
        "--resolve", action="store_true",
        help="Resolve listing URLs to direct ATS application URLs",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show detailed output",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Merge CLI args with defaults
    config = {**DEFAULT_CONFIG}
    if args.keywords:
        config["keywords"] = args.keywords
    if args.locations:
        config["locations"] = args.locations
    if args.max_per_source:
        config["max_results_per_source"] = args.max_per_source

    # Resolve output path
    script_dir = Path(__file__).parent
    if args.output:
        output_path = Path(args.output)
    elif config["output_file"]:
        output_path = script_dir / config["output_file"]
    else:
        output_path = script_dir.parent / "jobs.txt"
    output_path = output_path.resolve()

    tracker_path = output_path.parent / "application_tracker.json"

    # Parse sources
    sources = None
    if args.sources:
        sources = [s.strip() for s in args.sources.split(",")]

    # Header
    print("=" * 60)
    print("  Job Feed Fetcher")
    print("=" * 60)
    print(f"  Keywords:  {', '.join(config['keywords'])}")
    print(f"  Locations: {', '.join(config['locations'])}")
    if sources:
        print(f"  Sources:   {', '.join(sources)}")
    if args.ats_only:
        print(f"  Filter:    ATS URLs only")
    print()

    # Fetch from all sources
    all_jobs = fetch_all_jobs(config, sources=sources, verbose=args.verbose)

    # Resolve listing URLs to ATS URLs
    if args.resolve:
        print("\n  Resolving listing URLs to ATS application URLs...")
        all_jobs = resolve_jobs(all_jobs, config, verbose=args.verbose)

    # Deduplicate
    deduped = deduplicate_jobs(all_jobs)

    # Load existing URLs
    existing_urls = load_existing_urls(str(output_path))
    applied_urls = load_applied_urls(str(tracker_path))
    skip_urls = existing_urls | applied_urls

    # Filter out already-known URLs
    new_jobs = [j for j in deduped if normalize_url(j["url"]) not in skip_urls]

    # ATS filter
    ats_count = sum(1 for j in new_jobs if is_ats_url(j["url"]))
    if args.ats_only:
        new_jobs = [j for j in new_jobs if is_ats_url(j["url"])]

    # Summary
    print()
    print("=" * 60)
    print("  Summary")
    print("=" * 60)
    print(f"  Total fetched:       {len(all_jobs)}")
    print(f"  After dedup:         {len(deduped)}")
    print(f"  ATS URLs found:      {ats_count}")
    print(f"  Already in jobs.txt: {len([j for j in deduped if normalize_url(j['url']) in existing_urls])}")
    print(f"  Already applied:     {len([j for j in deduped if normalize_url(j['url']) in applied_urls])}")
    print(f"  New URLs to add:     {len(new_jobs)}")
    print()

    if not new_jobs:
        print("  No new jobs to add.")
        return

    # Dry run: print URLs
    if args.dry_run:
        print("  --- Dry Run Output ---")
        for j in new_jobs:
            ats_tag = " [ATS]" if is_ats_url(j["url"]) else ""
            print(f"  {j['url']}{ats_tag}")
            if args.verbose:
                print(f"    {j['title']} (via {j['source']})")
        return

    # Write to file
    append = not args.overwrite
    write_jobs_file(new_jobs, str(output_path), append=append)
    mode_str = "Appended" if append else "Wrote"
    print(f"  {mode_str} {len(new_jobs)} URLs to {output_path}")


if __name__ == "__main__":
    main()
