"""
Search Google Hotels via SerpApi and cache results.

Input (stdin JSON):
{
  "q": "Da Nang Hai Chau District",  # free-text: city, area, or hotel name
  "check_in_date": "2026-05-15",      # YYYY-MM-DD
  "check_out_date": "2026-05-20",     # YYYY-MM-DD
  "adults": 2,                        # default 2
  "currency": "TWD",                  # default TWD
  "sort_by": null,                    # null=relevance, 3=lowest price, 8=highest rating, 13=most reviewed
  "hotel_class": null,                # null=any, 2/3/4/5 star
  "min_price": null,                  # price floor (in currency)
  "max_price": null,                  # price ceiling (in currency)
  "rating": null,                     # null=any, 7=3.5+, 8=4.0+, 9=4.5+
  "amenities": null,                  # comma-separated IDs (e.g. "35,9" = free breakfast, pool)
  "min_rating": null,                  # local post-filter: minimum overall rating (e.g. 4.0)
  "min_reviews": 20,                    # local post-filter: minimum review count (default 20)
  "max_hotel_class": null,             # local post-filter: maximum star rating (e.g. 3 = cap at 3-star)
  "local_sort": null,                  # local sort: "price" / "rating" / "value" (rating²/price)
  "cache_path": "trips/{slug}/data/hotels_cache.json",
  "max_results": 10
}

Output (stdout JSON):
{
  "cache_key": "...",
  "cache_hit": true/false,
  "hotels": [...],
  "filters_applied": [...],
  "warnings": [...],              # optional, e.g. "many hotels had no price data"
  "usage": {"searches_used": N, "searches_remaining": M},
  "fetched_at": "..."
}

SerpApi quota: 250 searches/month (free tier). Cache valid for 24 hours.

Common amenity IDs: 35=free breakfast, 9=pool, 19=parking, 4=free WiFi,
  6=air conditioning, 14=fitness center, 36=kitchen, 16=airport shuttle
"""
import json
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from serpapi_utils import (
    get_api_key, get_repo_root, check_usage, increment_usage,
    load_cache, save_cache, build_cache_key,
)


def _normalize_query(q):
    """Normalize hotel query for cache key: lowercase, sort words, strip extra spaces."""
    words = sorted(q.lower().split())
    return " ".join(words)


def _parse_hotel(prop):
    """Extract relevant fields from a SerpApi hotel property."""
    rate = prop.get("rate_per_night", {})
    total = prop.get("total_rate", {})
    coords = prop.get("gps_coordinates", {})

    # Extract OTA prices
    prices = []
    for p in prop.get("prices", []):
        source_rate = p.get("rate_per_night", {})
        prices.append({
            "source": p.get("source"),
            "rate_per_night": source_rate.get("extracted_lowest") or source_rate.get("lowest"),
        })

    return {
        "name": prop.get("name"),
        "hotel_class": prop.get("extracted_hotel_class"),
        "overall_rating": prop.get("overall_rating"),
        "reviews": prop.get("reviews"),
        "rate_per_night": {
            "lowest": rate.get("extracted_lowest") or rate.get("lowest"),
        },
        "total_rate": {
            "lowest": total.get("extracted_lowest") or total.get("lowest"),
        },
        "amenities": prop.get("amenities", []),
        "gps_coordinates": {
            "lat": coords.get("latitude"),
            "lng": coords.get("longitude"),
        } if coords else None,
        "check_in_time": prop.get("check_in_time"),
        "check_out_time": prop.get("check_out_time"),
        "prices": prices,
        "link": prop.get("link"),
        "property_token": prop.get("property_token"),
        "description": prop.get("description"),
        "nearby_places": [
            {
                "name": np.get("name"),
                "transport": [
                    {"type": t.get("type"), "duration": t.get("duration")}
                    for t in np.get("transportations", [])
                ],
            }
            for np in (prop.get("nearby_places", []) or [])[:3]
        ],
    }


def search_hotels(params):
    """Execute a Google Hotels search via SerpApi."""
    api_key = get_api_key()
    if not api_key:
        return {"error": "SERPAPI_API_KEY not set. Add it to .envrc and run direnv allow."}

    repo_root = get_repo_root()
    usage = check_usage(repo_root)
    if not usage["ok"]:
        return {
            "error": "monthly_quota_exhausted",
            "usage": usage,
            "message": f"SerpApi monthly limit reached ({usage['monthly_limit']}). Resets next month.",
        }

    # Build cache key
    q_normalized = _normalize_query(params["q"])
    adults = params.get("adults", 2)

    currency = params.get("currency", "TWD")
    filter_parts = []
    if currency != "TWD": filter_parts.append(f"cur{currency}")
    if params.get("hotel_class"): filter_parts.append(f"c{params['hotel_class']}+")
    if params.get("max_hotel_class"): filter_parts.append(f"c<={params['max_hotel_class']}")
    if params.get("min_price"): filter_parts.append(f"pmin{params['min_price']}")
    if params.get("max_price"): filter_parts.append(f"pmax{params['max_price']}")
    if params.get("rating"): filter_parts.append(f"rat{params['rating']}")
    if params.get("amenities"): filter_parts.append(f"am{params['amenities']}")
    if params.get("min_rating"): filter_parts.append(f"r{params['min_rating']}")
    if params.get("sort_by"): filter_parts.append(f"sb{params['sort_by']}")
    if params.get("local_sort"): filter_parts.append(f"ls{params['local_sort']}")
    filter_suffix = "-".join(filter_parts) if filter_parts else "nofilter"

    cache_key = build_cache_key(
        q_normalized.replace(" ", "-"),
        params["check_in_date"],
        params["check_out_date"],
        adults,
        filter_suffix,
    )
    cache_path = params["cache_path"]

    # Check cache
    cached = load_cache(cache_path, cache_key)
    if cached:
        print(f"Cache hit for {cache_key}", file=sys.stderr)
        return {
            "cache_key": cache_key,
            "cache_hit": True,
            "hotels": cached.get("hotels", []),
            "usage": usage,
            "fetched_at": cached.get("fetched_at"),
        }

    # Build SerpApi request
    print(f"Searching hotels: {params['q']} ({params['check_in_date']} ~ {params['check_out_date']})...",
          file=sys.stderr)

    from serpapi import Client
    client = Client(api_key=api_key)

    search_params = {
        "engine": "google_hotels",
        "q": params["q"],
        "check_in_date": params["check_in_date"],
        "check_out_date": params["check_out_date"],
        "adults": str(adults),
        "currency": params.get("currency", "TWD"),
        "hl": "zh-TW",
        "gl": "tw",
    }

    if params.get("sort_by"):
        search_params["sort_by"] = str(params["sort_by"])
    if params.get("hotel_class"):
        search_params["hotel_class"] = str(params["hotel_class"])
    if params.get("min_price"):
        search_params["min_price"] = str(params["min_price"])
    if params.get("max_price"):
        search_params["max_price"] = str(params["max_price"])
    if params.get("rating"):
        search_params["rating"] = str(params["rating"])
    if params.get("amenities"):
        search_params["amenities"] = str(params["amenities"])

    # Call API with retry
    result = None
    for attempt in range(3):
        try:
            result = client.search(search_params)
            break
        except Exception as e:
            if attempt < 2:
                wait = 2 ** (attempt + 1)
                print(f"SerpApi error, retrying in {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
            else:
                return {"error": f"SerpApi request failed after 3 attempts: {e}"}

    # Check for API error response
    if result.get("error"):
        return {"error": f"SerpApi returned error: {result['error']}"}

    # Parse results — fetch all, filter locally, then take top N
    properties = result.get("properties", [])
    max_results = params.get("max_results", 10)

    parsed = []
    for prop in properties:
        # Skip sponsored results
        if prop.get("sponsored"):
            continue
        parsed.append(_parse_hotel(prop))

    total_before_filter = len(parsed)
    filters_applied = []
    warnings = []

    # Post-filter: remove hotels with no price data
    before = len(parsed)
    parsed = [h for h in parsed if h.get("rate_per_night", {}).get("lowest") is not None]
    if before != len(parsed):
        removed = before - len(parsed)
        filters_applied.append(f"has_price ({before}->{len(parsed)})")
        if removed > before * 0.5:
            warnings.append(f"{removed}/{before} hotels had no price data (removed). "
                            f"This may indicate peak-season pricing unavailability. "
                            f"Try broadening your search area or adjusting dates.")
            print(f"  ⚠ {removed}/{before} hotels had no price data", file=sys.stderr)

    # Post-filter: minimum rating (e.g. 4.0)
    if params.get("min_rating"):
        min_r = float(params["min_rating"])
        before = len(parsed)
        parsed = [h for h in parsed if (h.get("overall_rating") or 0) >= min_r]
        filters_applied.append(f"min_rating>={min_r} ({before}->{len(parsed)})")

    # Post-filter: minimum review count (default 20 to exclude unreliable entries)
    min_reviews = params.get("min_reviews", 20)
    if min_reviews:
        before = len(parsed)
        parsed = [h for h in parsed if (h.get("reviews") or 0) >= min_reviews]
        if before != len(parsed):
            filters_applied.append(f"min_reviews>={min_reviews} ({before}->{len(parsed)})")

    # Post-filter: max hotel class (e.g. hotel_class=3 from API gives 3+, max_hotel_class=3 caps at 3)
    if params.get("max_hotel_class"):
        max_cls = int(params["max_hotel_class"])
        before = len(parsed)
        parsed = [h for h in parsed if (h.get("hotel_class") or 0) <= max_cls]
        filters_applied.append(f"max_hotel_class<={max_cls} ({before}->{len(parsed)})")

    # Local sort — respect local_sort, default to API order (relevance)
    local_sort = params.get("local_sort")
    if local_sort == "price":
        parsed.sort(key=lambda x: (x.get("rate_per_night", {}).get("lowest") or float("inf")))
        filters_applied.append("sort:price")
    elif local_sort == "rating":
        parsed.sort(key=lambda x: -(x.get("overall_rating") or 0))
        filters_applied.append("sort:rating")
    elif local_sort == "value":
        # Weighted value score: rating^2 / price — squares rating to give it more weight
        # e.g. 4.7 star @ TWD 8000 scores 2.76, vs 4.2 star @ TWD 5400 scores 3.27
        #      but 4.7 star @ TWD 7000 scores 3.16, beating both
        parsed.sort(key=lambda x: -(
            (x.get("overall_rating") or 0) ** 2 /
            max(x.get("rate_per_night", {}).get("lowest") or 1, 1)
        ))
        filters_applied.append("sort:value(rating²/price)")

    # Take top N after filtering
    parsed = parsed[:max_results]

    # Add rank, currency, and cheapest OTA source
    for i, h in enumerate(parsed):
        h["rank"] = i + 1
        h["currency"] = currency
        # Mark cheapest booking source
        if h.get("prices"):
            valid_prices = [p for p in h["prices"] if p.get("rate_per_night")]
            if valid_prices:
                cheapest = min(valid_prices, key=lambda p: p["rate_per_night"])
                h["cheapest_source"] = cheapest["source"]

    # Save to cache
    from datetime import datetime, timezone as tz
    fetched_at = datetime.now(tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache_data = {
        "hotels": parsed,
        "filters_applied": filters_applied,
        "fetched_at": fetched_at,
        "raw_params": search_params,
    }
    save_cache(cache_path, cache_key, cache_data)

    # Increment usage
    nights = None
    try:
        from datetime import date
        d1 = date.fromisoformat(params["check_in_date"])
        d2 = date.fromisoformat(params["check_out_date"])
        nights = (d2 - d1).days
    except Exception:
        pass
    nights_str = f" {nights}晚" if nights else ""
    query_summary = f"{params['q']} {params['check_in_date']}~{params['check_out_date']}{nights_str}"
    updated_usage = increment_usage(repo_root, "google_hotels", query_summary)

    print(f"  ✓ Found {len(parsed)} hotels", file=sys.stderr)
    if parsed:
        cheapest = min((h["rate_per_night"]["lowest"] for h in parsed if h["rate_per_night"]["lowest"]), default=None)
        if cheapest:
            print(f"  💰 Cheapest: {currency} {cheapest}/night", file=sys.stderr)
    print(f"  📊 Usage: {updated_usage['searches_used']}/{check_usage(repo_root)['monthly_limit']}", file=sys.stderr)

    output = {
        "cache_key": cache_key,
        "cache_hit": False,
        "hotels": parsed,
        "filters_applied": filters_applied,
        "usage": updated_usage,
        "fetched_at": cache_data["fetched_at"],
    }
    if warnings:
        output["warnings"] = warnings
    return output


def main():
    # Force UTF-8 on stdin/stdout for Windows compatibility
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    input_data = json.load(sys.stdin)
    result = search_hotels(input_data)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print(file=sys.stdout)


if __name__ == "__main__":
    main()
