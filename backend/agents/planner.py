"""
Planner / Location-Extractor Agent (collapsed into one module).

Responsibility: turn a free-text user message into a structured filter
dict that the rest of the pipeline can act on.

Two modes:
  1. Deterministic mode (default, no API key needed): regex + keyword based
     extraction. Good enough for messages like
         "Find hospitals in Andheri, Mumbai, Maharashtra, India, only
          government type, top 10, give me name and phone, export as csv"
  2. LLM mode (if ANTHROPIC_API_KEY is set): sends the message + current
     known filters to Claude and asks for a strict JSON object back.
     This is more robust for messy phrasing but requires network access
     and an API key - NOT exercised by default.

Either way the output shape is identical, so the rest of the app never
needs to know which mode produced it.
"""

import json
import re

from .. import config

EMPTY_FILTERS = {
    "country": None,
    "state": None,
    "city": None,
    "area": None,
    "postal_code": None,
    "hospital_type": None,
    "specialty": None,
    "min_rating": None,
    "emergency_only": False,
    "num_records": None,
    "fields": None,          # list of field names, or None = use a preset
    "field_preset": "detailed",
    "output_format": None,   # xlsx | csv | json | pdf
}

HOSPITAL_TYPES = ["government", "private", "trust", "charitable", "military", "specialty", "multispecialty"]

FORMAT_ALIASES = {
    "excel": "xlsx",
    "xlsx": "xlsx",
    "spreadsheet": "xlsx",
    "csv": "csv",
    "json": "json",
    "pdf": "pdf",
}

PRESET_TRIGGERS = {
    "name and phone": "name_and_phone",
    "only name and phone": "name_and_phone",
    "basic": "basic",
    "detailed": "detailed",
    "detailed information": "detailed",
    "full": "full",
    "all fields": "full",
    "everything": "full",
}


LOCATION_STOP_PATTERNS = [
    r"\bonly\s+name\s+and\s+phone\b",
    r"\bname\s+and\s+phone\b",
    r"\bdetailed\s+information\b",
    r"\ball\s+fields\b",
    r"\beverything\b",
    r"\bdetailed\b",
    r"\bbasic\b",
    r"\bfull\b",
    r"\btop\s+\d+\b",
    r"\bgovernment\b",
    r"\bprivate\b",
    r"\btrust\b",
    r"\bcharitable\b",
    r"\bmilitary\b",
    r"\bspecialty\b",
    r"\bspecialization\b",
    r"\brating\b",
    r"\bemergency\b",
    r"\bexport\b",
    r"\bformat\b",
    r"\bgive\s+me\b",
    r"\breturn\b",
    r"\bexcel\b",
    r"\bspreadsheet\b",
    r"\bcsv\b",
    r"\bjson\b",
    r"\bpdf\b",
    r"\bxlsx\b",
    r"\bwith\b",
    r"\bhaving\b",
    r"\bwhere\b",
]

# Small lookup so "in Pune" -> city, but "in Mumbai, India" -> city + country.
# Not exhaustive - it only needs to cover common cases well enough for a
# deterministic parser; the LLM mode (if enabled) doesn't need this at all.
KNOWN_COUNTRIES = {
    "india", "usa", "u.s.a", "u.s.a.", "united states", "united states of america",
    "uk", "u.k.", "united kingdom", "canada", "australia", "germany", "france",
    "china", "japan", "brazil", "mexico", "uae", "united arab emirates",
    "singapore", "south africa", "italy", "spain", "netherlands",
}

# When the last chunk IS a recognised country, map the remaining chunks
# (most-specific first) using these label sets.
REST_LABELS = {0: [], 1: ["city"], 2: ["city", "state"], 3: ["area", "city", "state"]}

# When no chunk is a recognised country, assume the LAST chunk is the most
# specific location we're confident about (a city) and work backwards.
NO_COUNTRY_LABELS = {1: ["city"], 2: ["area", "city"], 3: ["area", "city", "state"], 4: ["area", "city", "state", "country"]}


CANONICAL_COUNTRY_NAMES = {
    "usa": "USA", "u.s.a": "USA", "u.s.a.": "USA",
    "united states": "United States", "united states of america": "United States of America",
    "uk": "UK", "u.k.": "UK", "united kingdom": "United Kingdom",
    "uae": "UAE", "united arab emirates": "United Arab Emirates",
}


def _assign_location_chunks(chunks, filters):
    if not chunks:
        return filters
    if len(chunks) > 4:
        chunks = chunks[-4:]

    last = chunks[-1].strip().lower()
    if last in KNOWN_COUNTRIES:
        rest = chunks[:-1]
        labels = REST_LABELS.get(len(rest), [])
        for label, value in zip(labels, rest):
            if not filters.get(label):
                filters[label] = value.title()
        if not filters.get("country"):
            filters["country"] = CANONICAL_COUNTRY_NAMES.get(last, chunks[-1].title())
    else:
        labels = NO_COUNTRY_LABELS.get(len(chunks), [])
        for label, value in zip(labels, chunks):
            if not filters.get(label):
                filters[label] = value.title()

    return filters


def _extract_location(text, filters):
    """
    Looks for patterns like 'in <area>, <city>, <state>, <country>' or
    standalone 'City = Mumbai' style key=value pairs (as shown in the
    original spec's filtering example).
    """
    text_l = text.lower()

    # key = value style: "city = mumbai", "state: maharashtra"
    kv_pattern = re.findall(r"(country|state|city|area|postal\s*code|pincode|zip)\s*[:=]\s*([a-zA-Z0-9 .\-]+)", text_l)
    for key, value in kv_pattern:
        key = key.replace(" ", "")
        value = value.strip().title()
        if key in ("postalcode", "pincode", "zip"):
            filters["postal_code"] = value
        elif key in ("country", "state", "city", "area"):
            filters[key] = value

    # "in X, Y, Z" style - take comma separated chunks after "in", but stop
    # at the first phrase that's clearly NOT part of a location (field
    # presets, hospital types, output formats, "top N", etc.)
    m = re.search(r"\bin\s+(.+)$", text, re.IGNORECASE)
    if m:
        remainder = m.group(1)

        cutoff = len(remainder)
        for pattern in LOCATION_STOP_PATTERNS:
            sm = re.search(pattern, remainder, re.IGNORECASE)
            if sm and sm.start() < cutoff:
                cutoff = sm.start()

        location_text = remainder[:cutoff]
        chunks = [c.strip() for c in location_text.split(",") if c.strip() and c.strip().lower() != "and"]

        filters = _assign_location_chunks(chunks, filters)

    return filters


def _extract_hospital_type(text, filters):
    text_l = text.lower()
    for h_type in HOSPITAL_TYPES:
        if h_type in text_l:
            filters["hospital_type"] = h_type.title()
            break
    return filters


def _extract_num_records(text, filters):
    m = re.search(r"\btop\s+(\d+)\b", text.lower()) or re.search(r"\b(\d+)\s+(hospitals|records|results)\b", text.lower())
    if m:
        filters["num_records"] = min(int(m.group(1)), config.MAX_RECORDS_HARD_CAP)
    return filters


def _extract_min_rating(text, filters):
    m = re.search(r"rating\s*(?:above|over|>=|greater than)?\s*(\d(\.\d)?)", text.lower())
    if m:
        filters["min_rating"] = float(m.group(1))
    return filters


def _extract_emergency(text, filters):
    if "emergency" in text.lower() and ("only" in text.lower() or "available" in text.lower() or "24/7" in text.lower() or "24x7" in text.lower()):
        filters["emergency_only"] = True
    return filters


def _extract_fields(text, filters):
    text_l = text.lower()
    for trigger, preset in PRESET_TRIGGERS.items():
        if trigger in text_l:
            filters["field_preset"] = preset
            filters["fields"] = config.FIELD_PRESETS[preset]
            break
    return filters


def _extract_format(text, filters):
    text_l = text.lower()
    for alias, fmt in FORMAT_ALIASES.items():
        if alias in text_l:
            filters["output_format"] = fmt
            break
    return filters


def parse_with_rules(message, existing_filters=None):
    """Deterministic parser. Merges new info into existing_filters (dict)."""
    filters = dict(existing_filters) if existing_filters else dict(EMPTY_FILTERS)

    filters = _extract_location(message, filters)
    filters = _extract_hospital_type(message, filters)
    filters = _extract_num_records(message, filters)
    filters = _extract_min_rating(message, filters)
    filters = _extract_emergency(message, filters)
    filters = _extract_fields(message, filters)
    filters = _extract_format(message, filters)

    if filters.get("fields") is None:
        filters["fields"] = config.FIELD_PRESETS[filters.get("field_preset", "detailed")]

    return filters


def parse_with_llm(message, existing_filters=None):
    """
    Optional LLM-assisted parsing via the Anthropic API.

    Requires `requests` + ANTHROPIC_API_KEY. Falls back to the rule-based
    parser on any error so the app never hard-fails because of this.
    """
    if not config.ANTHROPIC_API_KEY:
        return parse_with_rules(message, existing_filters)

    import requests  # local import - keeps this optional dependency lazy

    existing_filters = existing_filters or dict(EMPTY_FILTERS)

    system_prompt = (
        "You extract hospital-search filters from a user's message. "
        "Return ONLY a JSON object (no prose, no markdown fences) with these keys: "
        "country, state, city, area, postal_code, hospital_type, specialty, "
        "min_rating, emergency_only (bool), num_records (int or null), "
        "field_preset (one of basic|name_and_phone|detailed|full), "
        "output_format (one of xlsx|csv|json|pdf|null). "
        "Merge with the existing filters given below - only change fields the "
        "new message clearly addresses, keep the rest unchanged. "
        f"Existing filters: {json.dumps(existing_filters)}"
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "content-type": "application/json",
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": config.ANTHROPIC_MODEL,
                "max_tokens": 500,
                "system": system_prompt,
                "messages": [{"role": "user", "content": message}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        text = text.strip().strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
        parsed = json.loads(text)

        filters = dict(existing_filters)
        filters.update({k: v for k, v in parsed.items() if v is not None})
        if filters.get("num_records"):
            filters["num_records"] = min(int(filters["num_records"]), config.MAX_RECORDS_HARD_CAP)
        if not filters.get("fields"):
            filters["fields"] = config.FIELD_PRESETS.get(filters.get("field_preset", "detailed"), config.FIELD_PRESETS["detailed"])
        return filters
    except Exception:
        # Network error, bad JSON, rate limit, etc. -> safe fallback
        return parse_with_rules(message, existing_filters)


def parse(message, existing_filters=None):
    if config.ANTHROPIC_API_KEY:
        return parse_with_llm(message, existing_filters)
    return parse_with_rules(message, existing_filters)


def filters_summary(filters):
    """Human readable one-liner describing the current filters - shown in chat."""
    location_parts = [filters.get(k) for k in ("area", "city", "state", "country") if filters.get(k)]
    location = ", ".join(location_parts) if location_parts else "(any location)"

    bits = [f"Location: {location}"]
    if filters.get("hospital_type"):
        bits.append(f"Type: {filters['hospital_type']}")
    if filters.get("specialty"):
        bits.append(f"Specialty: {filters['specialty']}")
    if filters.get("min_rating"):
        bits.append(f"Min rating: {filters['min_rating']}")
    if filters.get("emergency_only"):
        bits.append("Emergency: required")
    if filters.get("num_records"):
        bits.append(f"Limit: {filters['num_records']} records")
    bits.append(f"Fields: {filters.get('field_preset', 'detailed')}")
    if filters.get("output_format"):
        bits.append(f"Format: {filters['output_format']}")
    return " | ".join(bits)


def is_ready_to_search(filters):
    """At minimum we need *some* location to avoid a meaningless global query."""
    return any(filters.get(k) for k in ("country", "state", "city", "area", "postal_code"))
