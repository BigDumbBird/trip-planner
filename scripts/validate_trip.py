"""
Validate trip data files before render.
Usage: python3 scripts/validate_trip.py trips/{slug}

Checks all JSON files for required fields and consistency.
Exits with code 1 on first error, prints clear message.
"""
import json
import sys
import pathlib


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_trip_json(data, path):
    for field in ("title", "slug", "date_range", "cities", "icon"):
        assert field in data, f"{path}: missing '{field}'"
    assert isinstance(data["cities"], list), f"{path}: 'cities' must be array"


def validate_itinerary(data, path):
    days = data if isinstance(data, list) else data.get("days", data)
    if isinstance(data, dict) and "days" in data:
        days = data["days"]
    assert isinstance(days, list), f"{path}: expected days array"

    for i, day in enumerate(days):
        prefix = f"{path} Day {day.get('day', i+1)}"
        assert "day" in day, f"{prefix}: missing 'day' number"
        assert "places" in day, f"{prefix}: missing 'places'"
        assert isinstance(day["places"], list), f"{prefix}: 'places' must be array"

        prev_time = ""
        for j, place in enumerate(day["places"]):
            pp = f"{prefix} place[{j}] '{place.get('title', '?')}'"
            assert "type" in place, f"{pp}: missing 'type'"
            assert "title" in place, f"{pp}: missing 'title'"
            assert "time" in place, f"{pp}: missing 'time'"
            assert place.get("lat") is not None, f"{pp}: missing 'lat'"
            assert place.get("lng") is not None, f"{pp}: missing 'lng'"

            # Time ascending check
            t = place.get("time", "")
            if prev_time and t < prev_time:
                print(f"WARNING: {pp}: time {t} < previous {prev_time}", file=sys.stderr)
            prev_time = t


def validate_info(data, path):
    assert "sections" in data, f"{path}: missing 'sections'"
    for i, s in enumerate(data["sections"]):
        assert "title" in s, f"{path} section[{i}]: missing 'title'"
        assert "type" in s, f"{path} section[{i}]: missing 'type'"
        assert s["type"] in ("table", "text"), f"{path} section[{i}]: type must be 'table' or 'text'"


def validate_array(data, path, required_fields):
    assert isinstance(data, list), f"{path}: expected array"
    for i, item in enumerate(data):
        for field in required_fields:
            assert field in item, f"{path} item[{i}]: missing '{field}'"


def main():
    trip_dir = pathlib.Path(sys.argv[1])
    data_dir = trip_dir / "data" if (trip_dir / "data").exists() else trip_dir
    errors = []

    # Check file existence
    required = ["trip.json", "itinerary.json", "info.json", "places_cache.json"]
    optional = ["reservations.json", "todo.json", "packing.json"]

    for f in required:
        if not (data_dir / f).exists():
            errors.append(f"MISSING: {data_dir / f}")
    for f in optional:
        if not (data_dir / f).exists():
            print(f"  OPTIONAL MISSING: {f}", file=sys.stderr)

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate each file
    try:
        validate_trip_json(load(data_dir / "trip.json"), "trip.json")
        validate_itinerary(load(data_dir / "itinerary.json"), "itinerary.json")
        validate_info(load(data_dir / "info.json"), "info.json")

        if (data_dir / "reservations.json").exists():
            validate_array(load(data_dir / "reservations.json"), "reservations.json", ["label", "note"])
        if (data_dir / "todo.json").exists():
            validate_array(load(data_dir / "todo.json"), "todo.json", ["label", "hint"])
        if (data_dir / "packing.json").exists():
            validate_array(load(data_dir / "packing.json"), "packing.json", ["label", "category"])

    except AssertionError as e:
        print(f"VALIDATION ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"UNEXPECTED ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ All validations passed for {trip_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
