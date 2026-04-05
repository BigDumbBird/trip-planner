"""
Search Google Flights via SerpApi and cache results.

Input (stdin JSON):
{
  "departure_id": "TPE",          # IATA airport code
  "arrival_id": "NRT",            # IATA airport code
  "outbound_date": "2026-05-15",  # YYYY-MM-DD
  "return_date": "2026-05-20",    # YYYY-MM-DD (null for one-way)
  "type": 1,                      # 1=round-trip, 2=one-way
  "adults": 1,                    # default 1
  "currency": "TWD",              # default TWD
  "stops": null,                  # null=any, 1=nonstop, 2=≤1stop, 3=≤2stops
  "travel_class": null,           # null=economy, 2=premium, 3=business, 4=first
  "sort_by": null,                # null=top, 2=price, 3=departure, 5=duration
  "include_airlines": null,       # comma-separated IATA codes (e.g. "VJ,7C,MM")
  "exclude_airlines": null,       # comma-separated IATA codes
  "max_price": null,              # max ticket price (in currency)
  "max_duration": null,           # max flight duration in minutes
  "cache_path": "trips/{slug}/data/flights_cache.json",
  "max_results": 10
}

Convenience filter (local post-filter, not API-level):
  "lcc_only": true                # only show flights operated by known LCCs

Output (stdout JSON):
{
  "cache_key": "...",
  "cache_hit": true/false,
  "flights": [...],
  "price_insights": {...},
  "filters_applied": [...],
  "warnings": [...],              # optional, e.g. "no LCC on this route"
  "usage": {"searches_used": N, "searches_remaining": M},
  "fetched_at": "..."
}

SerpApi quota: 250 searches/month (free tier). Cache valid for 24 hours.

Note: Round-trip (type=1) prices include BOTH legs. Only outbound flights are shown;
use departure_token to query return flights (costs 1 additional search credit).
Children are not supported by SerpApi — prices reflect adult fares only.
"""
import json
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from serpapi_utils import (
    get_api_key, get_repo_root, check_usage, increment_usage,
    load_cache, save_cache, build_cache_key,
)

# Known low-cost carriers (IATA codes) — used by lcc_only filter
# Covers major LCCs operating in Asia-Pacific, with some global coverage
LCC_AIRLINES = {
    # Taiwan / East Asia
    "IT",  # Tigerair Taiwan (台灣虎航)
    "MM",  # Peach Aviation (樂桃航空)
    "GK",  # Jetstar Japan
    "7C",  # Jeju Air (濟州航空)
    "LJ",  # Jin Air (真航空)
    "TW",  # T'way Air (德威航空)
    "ZE",  # Eastar Jet
    "BX",  # Air Busan (釜山航空)
    "RS",  # Air Seoul
    # Southeast Asia
    "VJ",  # VietJet Air (越捷航空)
    "QH",  # Bamboo Airways
    "AK",  # AirAsia (亞航)
    "D7",  # AirAsia X
    "FD",  # Thai AirAsia
    "Z2",  # AirAsia Philippines
    "QZ",  # AirAsia Indonesia
    "3K",  # Jetstar Asia
    "TR",  # Scoot (酷航)
    "SL",  # Thai Lion Air
    "DD",  # Nok Air
    "OD",  # Batik Air (formerly Malindo)
    "5J",  # Cebu Pacific (宿霧太平洋)
    # Hong Kong / Macau
    "UO",  # HK Express (香港快運)
    # NX (Air Macau) removed — full-service carrier, not LCC
    # Japan full-service LCC
    "BC",  # Skymark Airlines
    "6J",  # Solaseed Air
    # JW (Vanilla Air) removed — merged into Peach (MM) in 2019
    # Global
    "FR",  # Ryanair
    "W6",  # Wizz Air
    "U2",  # easyJet
    "NK",  # Spirit Airlines
    "F9",  # Frontier Airlines
    "WN",  # Southwest Airlines
    "G4",  # Allegiant Air
}


def _is_lcc(flight):
    """Check if all segments of a flight are operated by known LCCs."""
    for seg in flight.get("segments", []):
        # Extract IATA code from flight number (e.g. "VJ 843" → "VJ")
        fn = seg.get("flight_number", "")
        code = fn.split()[0] if fn else ""
        if code not in LCC_AIRLINES:
            return False
    return True


def _parse_flight(flight_data, label="other"):
    """Extract relevant fields from a SerpApi flight result."""
    segments = []
    for seg in flight_data.get("flights", []):
        dep = seg.get("departure_airport", {})
        arr = seg.get("arrival_airport", {})
        segments.append({
            "airline": seg.get("airline"),
            "flight_number": seg.get("flight_number"),
            "departure_airport": dep.get("id"),
            "departure_time": dep.get("time"),
            "arrival_airport": arr.get("id"),
            "arrival_time": arr.get("time"),
            "duration_min": seg.get("duration"),
        })

    flight_numbers = [s["flight_number"] for s in segments if s.get("flight_number")]
    airlines = list(dict.fromkeys(s["airline"] for s in segments if s.get("airline")))

    return {
        "label": label,
        "airline": ", ".join(airlines) if airlines else None,
        "flight_numbers": flight_numbers,
        "departure": {
            "airport": segments[0]["departure_airport"] if segments else None,
            "time": segments[0]["departure_time"] if segments else None,
        },
        "arrival": {
            "airport": segments[-1]["arrival_airport"] if segments else None,
            "time": segments[-1]["arrival_time"] if segments else None,
        },
        "total_duration_min": flight_data.get("total_duration"),
        "stops": len(flight_data.get("layovers", [])),
        "layovers": [
            {"airport": lo.get("id"), "duration_min": lo.get("duration")}
            for lo in flight_data.get("layovers", [])
        ] if flight_data.get("layovers") else [],
        "price": flight_data.get("price"),
        "trip_type": flight_data.get("type"),  # "Round trip" / "One way"
        "carbon_emissions": flight_data.get("carbon_emissions", {}).get("this_flight"),
        "segments": segments,
        "departure_token": flight_data.get("departure_token"),  # for querying return flights
        "booking_token": flight_data.get("booking_token"),
    }


def search_flights(params):
    """Execute a Google Flights search via SerpApi."""
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

    # Build cache key — include filter params so different filters don't collide
    departure_id = params["departure_id"].upper()
    arrival_id = params["arrival_id"].upper()
    trip_type = params.get("type", 1)
    return_date = params.get("return_date") if trip_type == 1 else "oneway"
    adults = params.get("adults", 1)

    filter_parts = []
    if params.get("travel_class"): filter_parts.append(f"tc{params['travel_class']}")
    if params.get("stops"): filter_parts.append(f"st{params['stops']}")
    if params.get("lcc_only"): filter_parts.append("lcc")
    if params.get("include_airlines"): filter_parts.append(f"inc{params['include_airlines']}")
    if params.get("exclude_airlines"): filter_parts.append(f"exc{params['exclude_airlines']}")
    if params.get("max_price"): filter_parts.append(f"mp{params['max_price']}")
    if params.get("max_duration"): filter_parts.append(f"md{params['max_duration']}")
    if params.get("sort_by"): filter_parts.append(f"sb{params['sort_by']}")
    filter_suffix = "-".join(filter_parts) if filter_parts else "nofilter"

    cache_key = build_cache_key(departure_id, arrival_id, params["outbound_date"], return_date, adults, filter_suffix)
    cache_path = params["cache_path"]

    # Check cache
    cached = load_cache(cache_path, cache_key)
    if cached:
        print(f"Cache hit for {cache_key}", file=sys.stderr)
        return {
            "cache_key": cache_key,
            "cache_hit": True,
            "flights": cached.get("flights", []),
            "price_insights": cached.get("price_insights"),
            "usage": usage,
            "fetched_at": cached.get("fetched_at"),
        }

    # Build SerpApi request
    print(f"Searching flights: {departure_id} → {arrival_id} on {params['outbound_date']}...", file=sys.stderr)

    from serpapi import Client
    client = Client(api_key=api_key)

    search_params = {
        "engine": "google_flights",
        "departure_id": departure_id,
        "arrival_id": arrival_id,
        "outbound_date": params["outbound_date"],
        "type": str(trip_type),
        "adults": str(adults),
        "currency": params.get("currency", "TWD"),
        "hl": "zh-TW",
        "gl": "tw",
    }

    if trip_type == 1 and params.get("return_date"):
        search_params["return_date"] = params["return_date"]
    if params.get("stops"):
        search_params["stops"] = str(params["stops"])
    if params.get("travel_class"):
        search_params["travel_class"] = str(params["travel_class"])
    if params.get("sort_by"):
        search_params["sort_by"] = str(params["sort_by"])
    if params.get("include_airlines"):
        search_params["include_airlines"] = params["include_airlines"]
    if params.get("exclude_airlines"):
        search_params["exclude_airlines"] = params["exclude_airlines"]
    if params.get("max_price"):
        search_params["max_price"] = str(params["max_price"])
    if params.get("max_duration"):
        search_params["max_duration"] = str(params["max_duration"])

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

    # Parse results
    best_flights = result.get("best_flights", [])
    other_flights = result.get("other_flights", [])

    parsed = []
    for f in best_flights:
        parsed.append(_parse_flight(f, label="best"))
    for f in other_flights:
        parsed.append(_parse_flight(f, label="other"))

    total_before_filter = len(parsed)

    # Post-filters
    filters_applied = []
    warnings = []
    if params.get("lcc_only"):
        parsed = [f for f in parsed if _is_lcc(f)]
        filters_applied.append(f"lcc_only ({total_before_filter}->{len(parsed)})")
        if len(parsed) == 0 and total_before_filter > 0:
            warnings.append(f"lcc_only filter removed all {total_before_filter} flights. "
                            f"This route likely has no LCC coverage. "
                            f"Consider removing lcc_only filter to see available options.")
            print(f"  ⚠ No LCC flights found on this route ({total_before_filter} flights available without filter)",
                  file=sys.stderr)

    # Local sort — respect sort_by, default to price
    sort_by = params.get("sort_by")
    if sort_by == 5:  # duration
        parsed.sort(key=lambda x: x.get("total_duration_min") or float("inf"))
    elif sort_by == 3:  # departure time
        parsed.sort(key=lambda x: x.get("departure", {}).get("time") or "")
    else:  # default: price
        parsed.sort(key=lambda x: x.get("price") or float("inf"))

    # Take top N
    max_results = params.get("max_results", 10)
    parsed = parsed[:max_results]

    # Add rank + lcc flag
    for i, f in enumerate(parsed):
        f["rank"] = i + 1
        f["is_lcc"] = _is_lcc(f)

    # Price insights
    price_insights = result.get("price_insights")

    # Save to cache
    from datetime import datetime, timezone as tz
    fetched_at = datetime.now(tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache_data = {
        "flights": parsed,
        "price_insights": price_insights,
        "filters_applied": filters_applied,
        "fetched_at": fetched_at,
        "raw_params": search_params,
    }
    save_cache(cache_path, cache_key, cache_data)

    # Increment usage
    query_summary = f"{departure_id}→{arrival_id} {params['outbound_date']}"
    if trip_type == 1 and params.get("return_date"):
        query_summary += f"~{params['return_date']}"
    updated_usage = increment_usage(repo_root, "google_flights", query_summary)

    count = len(parsed)
    print(f"  ✓ Found {count} flights (best: {len(best_flights)}, other: {len(other_flights)})", file=sys.stderr)
    if price_insights:
        low = price_insights.get("lowest_price")
        rng = price_insights.get("typical_price_range")
        if low:
            print(f"  💰 Lowest: {params.get('currency', 'TWD')} {low}", file=sys.stderr)
        if rng:
            print(f"  📊 Typical range: {params.get('currency', 'TWD')} {rng[0]}~{rng[1]}", file=sys.stderr)
    print(f"  📊 Usage: {updated_usage['searches_used']}/{check_usage(repo_root)['monthly_limit']}", file=sys.stderr)

    output = {
        "cache_key": cache_key,
        "cache_hit": False,
        "flights": parsed,
        "price_insights": price_insights,
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
    result = search_flights(input_data)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print(file=sys.stdout)


if __name__ == "__main__":
    main()
