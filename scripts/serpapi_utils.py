"""
Shared utilities for SerpApi integration: usage tracking and cache management.

Usage tracking:
  - Global monthly counter in $REPO/serpapi_usage.json
  - Auto-resets when month changes
  - Logs every search for auditability

Cache:
  - Per-trip cache files (flights_cache.json, hotels_cache.json)
  - Keyed by normalized search parameters
  - 24-hour expiry (stale entries kept for reference, but trigger re-fetch)
"""
import json
import os
import sys
from datetime import datetime, timezone


# ── Usage tracking ──────────────────────────────────────────────────────────

DEFAULT_MONTHLY_LIMIT = 250


def _usage_path(repo_root):
    return os.path.join(repo_root, "serpapi_usage.json")


def _current_month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load_usage(repo_root):
    path = _usage_path(repo_root)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"WARNING: Corrupted usage file {path}: {e}. Starting fresh.", file=sys.stderr)
    return {
        "monthly_limit": DEFAULT_MONTHLY_LIMIT,
        "current_month": _current_month(),
        "searches_used": 0,
        "log": [],
    }


def _save_usage(repo_root, data):
    path = _usage_path(repo_root)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def check_usage(repo_root):
    """Check remaining SerpApi quota for this month.

    Returns dict with searches_used, searches_remaining, ok (bool).
    Auto-resets counter if month has changed.
    """
    data = _load_usage(repo_root)
    current = _current_month()

    # Auto-reset on new month
    if data.get("current_month") != current:
        data["current_month"] = current
        data["searches_used"] = 0
        data["log"] = []
        _save_usage(repo_root, data)

    limit = data.get("monthly_limit", DEFAULT_MONTHLY_LIMIT)
    used = data["searches_used"]
    remaining = max(0, limit - used)

    return {
        "searches_used": used,
        "searches_remaining": remaining,
        "monthly_limit": limit,
        "ok": remaining > 0,
    }


def increment_usage(repo_root, engine, query_summary):
    """Increment monthly search counter and append to log."""
    data = _load_usage(repo_root)
    current = _current_month()

    if data.get("current_month") != current:
        data["current_month"] = current
        data["searches_used"] = 0
        data["log"] = []

    data["searches_used"] += 1
    data["log"].append({
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine": engine,
        "query_summary": query_summary,
    })
    _save_usage(repo_root, data)

    limit = data.get("monthly_limit", DEFAULT_MONTHLY_LIMIT)
    return {
        "searches_used": data["searches_used"],
        "searches_remaining": max(0, limit - data["searches_used"]),
    }


# ── Cache management ───────────────────────────────────────────────────────

CACHE_MAX_AGE_HOURS = 24


def load_cache(cache_path, cache_key):
    """Load a cached entry if it exists and is fresh (< 24h).

    Returns the cached data dict, or None if missing/stale.
    """
    if not os.path.exists(cache_path):
        return None

    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"WARNING: Corrupted cache file {cache_path}: {e}. Will re-fetch.", file=sys.stderr)
        return None

    entry = cache.get(cache_key)
    if entry is None:
        return None

    # Check freshness
    fetched_at = entry.get("fetched_at")
    if fetched_at:
        try:
            fetched = datetime.strptime(fetched_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
            if age_hours < CACHE_MAX_AGE_HOURS:
                return entry
            else:
                print(f"Cache stale ({age_hours:.1f}h old), will re-fetch.", file=sys.stderr)
                return None
        except ValueError:
            pass

    return None


def save_cache(cache_path, cache_key, data):
    """Write an entry to the cache file (append-only dict)."""
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)

    data["fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache[cache_key] = data

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
        f.write("\n")


def build_cache_key(*parts):
    """Build a normalized cache key from parts (lowercased, joined by -)."""
    return "-".join(str(p).lower().strip() for p in parts if p is not None)


# ── API key check ──────────────────────────────────────────────────────────

def get_api_key():
    """Get SERPAPI_API_KEY from environment. Returns key string or None."""
    key = os.environ.get("SERPAPI_API_KEY", "").strip()
    return key if key else None


def get_repo_root():
    """Get the repo root (parent of scripts/)."""
    return str(__import__("pathlib").Path(__file__).parent.parent)
