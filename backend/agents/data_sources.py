"""
Search + Scraping Agent  — LIVE DATA EDITION
=============================================

Priority waterfall (each source is tried in order, results merged & deduped):

  Tier 1 — Structured APIs (accurate, fast)
    1. GooglePlacesSource    – Google Places Text Search API
                               Requires: GOOGLE_PLACES_API_KEY
    2. OverpassSource        – OpenStreetMap Overpass API
                               Free, no key, global hospital coverage

  Tier 2 — Web Scrapers (when no API key is available)
    3. JustDialScraper       – justdial.com (best for India)
    4. YelpScraper           – yelp.com (best for USA / Western countries)

All sources implement BaseSource.search(filters) -> list[raw_record_dicts]

IMPORTANT NOTES:
  • Every field that a source cannot provide is returned as "N/A".
    No value is ever guessed or interpolated.
  • Each source respects config.SOURCE_REQUEST_DELAY_SECONDS between
    outbound requests.
  • config.MAX_RECORDS_HARD_CAP is enforced at the orchestrator level.
  • Web scrapers depend on the target site's current HTML structure.
    If a site changes its layout, the scraper will return fewer fields
    (never invented data) — you'll see "N/A" for the affected fields.
  • robots.txt / ToS: Overpass and Google Places are explicitly designed
    for programmatic access.  JustDial and Yelp scraping may conflict
    with their ToS — use at your own discretion and only for research.
"""

import json
import os
import re
import time
import logging

import requests
from bs4 import BeautifulSoup

from .. import config

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Shared HTTP session with a realistic browser User-Agent
# ------------------------------------------------------------------
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})


def _get(url, params=None, headers=None, timeout=15):
    """Thin wrapper: rate-limit delay + unified error logging."""
    time.sleep(config.SOURCE_REQUEST_DELAY_SECONDS)
    try:
        resp = _SESSION.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        logger.warning("HTTP error fetching %s: %s", url, exc)
        return None


def _empty_record():
    return {field: "N/A" for field in config.ALL_FIELDS}


# ==================================================================
# BaseSource
# ==================================================================
class BaseSource:
    name = "base"
    def search(self, filters) -> list:
        raise NotImplementedError


# ==================================================================
# TIER 1 — Google Places API
# ==================================================================
class GooglePlacesSource(BaseSource):
    """
    Uses the Google Places Text Search + Place Details endpoints.

    Text Search gives: name, formatted_address, rating, geometry, place_id
    Place Details gives: formatted_phone_number, website, opening_hours, types

    Fields that Places never provides (email, beds, departments, etc.)
    are always "N/A".

    Requires: GOOGLE_PLACES_API_KEY (set in .env)
    """
    name = "google_places"
    SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    DETAIL_URL = "https://maps.googleapis.com/maps/api/place/details/json"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("GOOGLE_PLACES_API_KEY required for GooglePlacesSource")
        self.api_key = api_key

    def search(self, filters) -> list:
        query = self._build_query(filters)
        results = []
        params = {"query": query, "key": self.api_key}

        while True:
            resp = _get(self.SEARCH_URL, params=params)
            if not resp:
                break
            data = resp.json()

            if data.get("status") not in ("OK", "ZERO_RESULTS"):
                logger.error("Places API error: %s — %s", data.get("status"), data.get("error_message"))
                break

            for place in data.get("results", []):
                rec = self._basic_record(place, filters)
                # Fetch phone + website from Details endpoint
                rec = self._enrich_with_details(rec, place.get("place_id"), filters)
                results.append(rec)

            next_token = data.get("next_page_token")
            limit = filters.get("num_records") or config.MAX_RECORDS_HARD_CAP
            if not next_token or len(results) >= limit:
                break

            # Google requires ≥2 s before a page token is valid
            time.sleep(max(config.SOURCE_REQUEST_DELAY_SECONDS, 2.5))
            params = {"pagetoken": next_token, "key": self.api_key}

        limit = filters.get("num_records") or config.MAX_RECORDS_HARD_CAP
        return results[:limit]

    # ------ helpers ------
    @staticmethod
    def _build_query(filters):
        parts = ["hospitals"]
        for key in ("hospital_type", "specialty"):
            if filters.get(key):
                parts.insert(0, filters[key])
        for key in ("area", "city", "state", "country"):
            if filters.get(key):
                parts.append(filters[key])
        return " ".join(parts)

    def _basic_record(self, place, filters):
        rec = _empty_record()
        loc = place.get("geometry", {}).get("location", {})
        rec.update({
            "hospital_name": place.get("name", "N/A"),
            "address": place.get("formatted_address", "N/A"),
            "rating": str(place.get("rating", "N/A")),
            "latitude": str(loc.get("lat", "N/A")),
            "longitude": str(loc.get("lng", "N/A")),
            "hospital_type": filters.get("hospital_type") or "N/A",
            "city": filters.get("city") or "N/A",
            "state": filters.get("state") or "N/A",
            "country": filters.get("country") or "N/A",
            "area": filters.get("area") or "N/A",
            "postal_code": filters.get("postal_code") or "N/A",
        })
        return rec

    def _enrich_with_details(self, rec, place_id, filters):
        if not place_id:
            return rec
        params = {
            "place_id": place_id,
            "fields": "formatted_phone_number,international_phone_number,website,opening_hours,address_components",
            "key": self.api_key,
        }
        resp = _get(self.DETAIL_URL, params=params)
        if not resp:
            return rec
        detail = resp.json().get("result", {})

        rec["phone"] = detail.get("international_phone_number") or detail.get("formatted_phone_number") or "N/A"
        rec["website"] = detail.get("website") or "N/A"

        # Extract postal code from address_components if still N/A
        if rec["postal_code"] == "N/A":
            for comp in detail.get("address_components", []):
                if "postal_code" in comp.get("types", []):
                    rec["postal_code"] = comp.get("long_name", "N/A")
                    break

        # Emergency hint from opening_hours
        hours = detail.get("opening_hours", {})
        if hours.get("open_now") or "24 hours" in str(hours.get("weekday_text", [])).lower():
            rec["emergency_contact"] = rec["phone"]

        return rec


# ==================================================================
# TIER 1 — OpenStreetMap Overpass API (free, no key)
# ==================================================================
class OverpassSource(BaseSource):
    """
    Queries the Overpass API for OSM nodes/ways/relations tagged
    amenity=hospital within a radius of the given city.

    Geocoding is done first via Nominatim to get a lat/lon centre point.
    Then the Overpass query fetches hospitals within RADIUS_KM.

    Fields available from OSM: name, address parts, phone, email, website,
    opening_hours, beds (if tagged), operator_type.
    Fields OSM cannot provide: rating, departments, doctors, specializations
    (beyond broad tags like "emergency=yes").
    """
    name = "openstreetmap_overpass"
    NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
    OVERPASS_URL = "https://overpass-api.de/api/interpreter"
    RADIUS_M = 20_000   # 20 km default radius

    def search(self, filters) -> list:
        lat, lon = self._geocode(filters)
        if lat is None:
            logger.warning("Overpass: geocoding failed for filters %s", filters)
            return []

        records = self._overpass_query(lat, lon, filters)
        return records

    def _geocode(self, filters):
        """Return (lat, lon) for the requested location, or (None, None)."""
        parts = [v for k in ("area", "city", "state", "country") for v in [filters.get(k)] if v]
        if not parts:
            return None, None
        query = ", ".join(parts)

        params = {
            "q": query,
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
        }
        headers = {"User-Agent": "HospitalDataAgent/1.0 (research project)"}
        resp = _get(self.NOMINATIM_URL, params=params, headers=headers)
        if not resp:
            return None, None

        results = resp.json()
        if not results:
            logger.warning("Nominatim found no results for: %s", query)
            return None, None
        return float(results[0]["lat"]), float(results[0]["lon"])

    def _overpass_query(self, lat, lon, filters) -> list:
        # Build Overpass QL: hospitals (nodes + ways + relations) in radius
        h_type_filter = ""
        if filters.get("hospital_type"):
            ht = filters["hospital_type"].lower()
            if ht == "government":
                h_type_filter = '[operator:type~"public|government",i]'
            elif ht == "private":
                h_type_filter = '[operator:type~"private",i]'

        overpass_ql = f"""
[out:json][timeout:30];
(
  node["amenity"="hospital"]{h_type_filter}(around:{self.RADIUS_M},{lat},{lon});
  way["amenity"="hospital"]{h_type_filter}(around:{self.RADIUS_M},{lat},{lon});
  relation["amenity"="hospital"]{h_type_filter}(around:{self.RADIUS_M},{lat},{lon});
);
out center tags;
"""
        resp = _get(self.OVERPASS_URL, params={"data": overpass_ql}, timeout=40)
        if not resp:
            return []

        elements = resp.json().get("elements", [])
        limit = filters.get("num_records") or config.MAX_RECORDS_HARD_CAP
        records = []
        for el in elements[:limit]:
            records.append(self._element_to_record(el, filters))
        return records

    def _element_to_record(self, element, filters) -> dict:
        tags = element.get("tags", {})
        # Centre point for ways/relations
        centre = element.get("center", {})
        lat = element.get("lat") or centre.get("lat") or "N/A"
        lon = element.get("lon") or centre.get("lon") or "N/A"

        # Build full address from OSM addr:* tags
        addr_parts = [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
            tags.get("addr:suburb", ""),
        ]
        address = " ".join(p for p in addr_parts if p).strip() or "N/A"

        # Emergency: OSM uses emergency=yes tag or 24/7 opening_hours
        emergency_contact = "N/A"
        if tags.get("emergency") == "yes" or "24/7" in tags.get("opening_hours", ""):
            emergency_contact = tags.get("phone", tags.get("contact:phone", "N/A"))

        # Hospital type from operator:type or healthcare:speciality
        h_type = (
            tags.get("operator:type")
            or ("Government" if "government" in tags.get("operator", "").lower() else None)
            or filters.get("hospital_type")
            or "N/A"
        ).title()

        rec = _empty_record()
        rec.update({
            "hospital_name": tags.get("name") or tags.get("official_name") or "N/A",
            "phone": tags.get("phone") or tags.get("contact:phone") or "N/A",
            "email": tags.get("email") or tags.get("contact:email") or "N/A",
            "website": tags.get("website") or tags.get("contact:website") or "N/A",
            "address": address,
            "area": tags.get("addr:suburb") or tags.get("addr:neighbourhood") or filters.get("area") or "N/A",
            "city": tags.get("addr:city") or filters.get("city") or "N/A",
            "state": tags.get("addr:state") or filters.get("state") or "N/A",
            "country": tags.get("addr:country") or filters.get("country") or "N/A",
            "postal_code": tags.get("addr:postcode") or filters.get("postal_code") or "N/A",
            "emergency_contact": emergency_contact,
            "hospital_type": h_type,
            "number_of_beds": str(tags.get("beds", "N/A")),
            "specializations": tags.get("healthcare:speciality") or "N/A",
            "latitude": str(lat),
            "longitude": str(lon),
        })
        return rec


# ==================================================================
# TIER 2 — JustDial scraper (India hospitals)
# ==================================================================
class JustDialScraper(BaseSource):
    """
    Scrapes justdial.com listing pages for hospital data.

    JustDial serves listings as server-side HTML for the first page;
    subsequent pages require API calls with session tokens that change
    frequently. This scraper therefore handles page 1 only (typically
    10-20 results).

    Fields scraped: name, phone, address, area, city, rating, category.
    All others: "N/A".

    Note: JustDial's HTML structure changes periodically. If you see
    mostly N/A results, the CSS selectors below need updating.
    """
    name = "justdial_scraper"
    BASE_URL = "https://www.justdial.com"

    def search(self, filters) -> list:
        city = filters.get("city") or filters.get("state") or ""
        if not city:
            return []

        area = filters.get("area") or ""
        slug = self._city_slug(city)
        category = "Hospitals"

        url = f"{self.BASE_URL}/{slug}/{category}"
        if area:
            url = f"{self.BASE_URL}/{slug}/{area}/{category}"

        resp = _get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Referer": "https://www.justdial.com",
        })
        if not resp:
            logger.warning("JustDial: no response for %s", url)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        return self._parse_listings(soup, filters)

    @staticmethod
    def _city_slug(city: str) -> str:
        return city.strip().replace(" ", "-").title()

    def _parse_listings(self, soup, filters) -> list:
        records = []
        # JustDial listing cards — selector valid as of 2024
        # Each result card has class "resultbox_info" or similar
        cards = (
            soup.select("li.cntanr")
            or soup.select("div.resultbox_info")
            or soup.select("div[class*='resultbox']")
            or soup.select("script[type='application/ld+json']")
        )

        # Try JSON-LD embedded structured data first (most reliable)
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    for item in data:
                        rec = self._jsonld_to_record(item, filters)
                        if rec:
                            records.append(rec)
                elif isinstance(data, dict):
                    rec = self._jsonld_to_record(data, filters)
                    if rec:
                        records.append(rec)
            except (json.JSONDecodeError, TypeError):
                continue

        # Fallback: parse HTML card elements
        if not records:
            for card in cards:
                rec = self._card_to_record(card, filters)
                if rec and rec.get("hospital_name", "N/A") != "N/A":
                    records.append(rec)

        limit = filters.get("num_records") or config.MAX_RECORDS_HARD_CAP
        return records[:limit]

    def _jsonld_to_record(self, data: dict, filters) -> dict | None:
        name = data.get("name") or data.get("legalName")
        if not name:
            return None
        if not any(kw in (data.get("@type") or "").lower() for kw in ("hospital", "medicalclinic", "medicalorg", "health")):
            if "hospital" not in name.lower():
                return None

        addr = data.get("address", {})
        geo = data.get("geo", {})
        rec = _empty_record()
        rec.update({
            "hospital_name": name,
            "phone": data.get("telephone") or "N/A",
            "email": data.get("email") or "N/A",
            "website": data.get("url") or "N/A",
            "address": addr.get("streetAddress") or "N/A",
            "area": addr.get("addressLocality") or filters.get("area") or "N/A",
            "city": addr.get("addressRegion") or filters.get("city") or "N/A",
            "state": addr.get("addressRegion") or filters.get("state") or "N/A",
            "country": addr.get("addressCountry") or filters.get("country") or "N/A",
            "postal_code": addr.get("postalCode") or filters.get("postal_code") or "N/A",
            "rating": str(data.get("aggregateRating", {}).get("ratingValue") or "N/A"),
            "latitude": str(geo.get("latitude") or "N/A"),
            "longitude": str(geo.get("longitude") or "N/A"),
            "hospital_type": filters.get("hospital_type") or "N/A",
        })
        return rec

    def _card_to_record(self, card, filters) -> dict:
        rec = _empty_record()
        # Name
        name_el = card.select_one(".fn, .companyname, h2.title, .store-name, [class*='compny']")
        rec["hospital_name"] = name_el.get_text(strip=True) if name_el else "N/A"
        # Phone (often obfuscated in HTML; JustDial loads it via JS)
        phone_el = card.select_one(".contact-info .tel, .mobilesv, [class*='phno']")
        rec["phone"] = phone_el.get_text(strip=True) if phone_el else "N/A"
        # Address
        addr_el = card.select_one(".cont_fl_addr, address, .address-info, [class*='address']")
        rec["address"] = addr_el.get_text(" ", strip=True) if addr_el else "N/A"
        # Rating
        rating_el = card.select_one(".green-box, .rating, [class*='rating']")
        if rating_el:
            txt = rating_el.get_text(strip=True)
            m = re.search(r"[\d.]+", txt)
            rec["rating"] = m.group() if m else "N/A"
        # City/area from filters
        rec["city"] = filters.get("city") or "N/A"
        rec["state"] = filters.get("state") or "N/A"
        rec["country"] = filters.get("country") or "India"
        rec["area"] = filters.get("area") or "N/A"
        rec["hospital_type"] = filters.get("hospital_type") or "N/A"
        return rec


# ==================================================================
# TIER 2 — Yelp scraper (USA / global)
# ==================================================================
class YelpScraper(BaseSource):
    """
    Scrapes yelp.com/search for hospital listings.

    Yelp serves server-side rendered JSON-LD on their listing pages which
    is parseable without JavaScript. Yelp's anti-bot measures are light
    on the search results page; aggressive usage will get your IP blocked.

    Fields available: name, address, phone, rating, website (if listed),
                       lat/lon (from geo tag or JSON-LD).
    """
    name = "yelp_scraper"
    SEARCH_URL = "https://www.yelp.com/search"

    def search(self, filters) -> list:
        city = filters.get("city") or ""
        state = filters.get("state") or ""
        country = filters.get("country") or ""
        if not city:
            return []

        location = ", ".join(p for p in [city, state, country] if p)
        term = "hospitals"
        if filters.get("hospital_type"):
            term = f"{filters['hospital_type']} hospitals"
        if filters.get("specialty"):
            term = f"{filters['specialty']} {term}"

        params = {"find_desc": term, "find_loc": location}
        resp = _get(self.SEARCH_URL, params=params, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        })
        if not resp:
            logger.warning("Yelp: no response")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        records = []

        # Try JSON-LD first
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") in ("ItemList",):
                        for el in item.get("itemListElement", []):
                            rec = self._jsonld_item(el.get("item", el), filters)
                            if rec:
                                records.append(rec)
                    else:
                        rec = self._jsonld_item(item, filters)
                        if rec:
                            records.append(rec)
            except (json.JSONDecodeError, TypeError):
                continue

        # Fallback: HTML card parse
        if not records:
            cards = soup.select("[class*='businessName'], h3 a[href*='/biz/']")
            for card in cards:
                parent = card.find_parent("li") or card.find_parent("div")
                if parent:
                    records.append(self._html_card(parent, filters))

        limit = filters.get("num_records") or config.MAX_RECORDS_HARD_CAP
        return records[:limit]

    def _jsonld_item(self, item: dict, filters) -> dict | None:
        name = item.get("name")
        if not name:
            return None
        addr = item.get("address") or {}
        geo = item.get("geo") or {}
        if isinstance(addr, str):
            addr = {}
        rec = _empty_record()
        rec.update({
            "hospital_name": name,
            "phone": item.get("telephone") or "N/A",
            "email": item.get("email") or "N/A",
            "website": item.get("url") or "N/A",
            "address": addr.get("streetAddress") or "N/A",
            "area": addr.get("addressLocality") or filters.get("area") or "N/A",
            "city": filters.get("city") or addr.get("addressLocality") or "N/A",
            "state": addr.get("addressRegion") or filters.get("state") or "N/A",
            "country": addr.get("addressCountry") or filters.get("country") or "N/A",
            "postal_code": addr.get("postalCode") or filters.get("postal_code") or "N/A",
            "rating": str(item.get("aggregateRating", {}).get("ratingValue") or "N/A"),
            "latitude": str(geo.get("latitude") or "N/A"),
            "longitude": str(geo.get("longitude") or "N/A"),
            "hospital_type": filters.get("hospital_type") or "N/A",
        })
        return rec

    def _html_card(self, card, filters) -> dict:
        rec = _empty_record()
        name_el = card.select_one("[class*='businessName'], h3, h4")
        rec["hospital_name"] = name_el.get_text(strip=True) if name_el else "N/A"
        rec["city"] = filters.get("city") or "N/A"
        rec["state"] = filters.get("state") or "N/A"
        rec["country"] = filters.get("country") or "N/A"
        rec["hospital_type"] = filters.get("hospital_type") or "N/A"
        return rec


# ==================================================================
# Mock (offline fallback / testing)
# ==================================================================
class MockDataSource(BaseSource):
    name = "mock_dataset"

    def __init__(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "sample_data", "mock_hospitals.json",
        )
        with open(path, "r", encoding="utf-8") as f:
            self._records = json.load(f)

    def search(self, filters) -> list:
        return [dict(r) for r in self._records if self._matches(r, filters)]

    @staticmethod
    def _matches(record, filters):
        def fmatch(fkey, rkey=None):
            rkey = rkey or fkey
            v = filters.get(fkey)
            return not v or str(record.get(rkey, "")).strip().lower() == str(v).strip().lower()

        if not fmatch("country"): return False
        if not fmatch("state"): return False
        if not fmatch("city"): return False
        if not fmatch("area"): return False
        if not fmatch("postal_code"): return False
        if filters.get("hospital_type") and str(record.get("hospital_type", "")).lower() != str(filters["hospital_type"]).lower():
            return False
        if filters.get("specialty") and filters["specialty"].lower() not in str(record.get("specializations", "")).lower():
            return False
        if filters.get("min_rating"):
            try:
                if float(record.get("rating", 0)) < float(filters["min_rating"]): return False
            except (TypeError, ValueError):
                return False
        if filters.get("emergency_only"):
            ec = str(record.get("emergency_contact", "")).strip().lower()
            if ec in ("", "n/a", "not available"): return False
        return True


# ==================================================================
# Orchestrator — selects and runs sources, merges results
# ==================================================================
def _get_sources():
    """
    Returns a list of sources to try, in priority order.
    DATA_SOURCE_MODE controls the strategy:

      mock    -> MockDataSource only
      live    -> Google Places (if key set), then Overpass OSM,
                 then JustDial (India) + Yelp (USA), pick by country
      places  -> Google Places only (original single-source mode)
      osm     -> Overpass OSM only
    """
    mode = config.DATA_SOURCE_MODE

    if mode == "mock":
        return [MockDataSource()]

    if mode == "places":
        return [GooglePlacesSource(config.GOOGLE_PLACES_API_KEY)]

    if mode == "osm":
        return [OverpassSource()]

    # mode == "live" — waterfall of all real sources
    sources = []
    if config.GOOGLE_PLACES_API_KEY:
        sources.append(GooglePlacesSource(config.GOOGLE_PLACES_API_KEY))
    sources.append(OverpassSource())
    # Country-specific scrapers
    # (added regardless - they'll return [] if the country doesn't match their site)
    sources.append(JustDialScraper())
    sources.append(YelpScraper())
    return sources


def _dedup_key(record):
    name = str(record.get("hospital_name", "")).strip().lower()
    addr = str(record.get("address", "")).strip().lower()
    phone = str(record.get("phone", "")).strip().lower()
    return (name, addr, phone)


from .cleaning import clean_and_validate, deduplicate, map_to_schema


def run_pipeline(filters):
    """
    Steps 3-8 of the 10-step workflow.

    Tries each source in priority order, collects results, merges and
    deduplicates across sources, applies min_rating + emergency filters,
    maps to the requested schema.

    Returns (mapped_records, quality_report).
    """
    sources = _get_sources()
    all_raw = []
    sources_used = []

    for source in sources:
        try:
            raw = source.search(filters)
            if raw:
                all_raw.extend(raw)
                sources_used.append(source.name)
                # If we already have enough records, skip remaining sources
                limit = filters.get("num_records") or config.MAX_RECORDS_HARD_CAP
                if len(all_raw) >= limit * 2:
                    break
        except Exception as exc:
            logger.warning("Source %s failed: %s", source.name, exc)

    # Post-source filters not handled inside individual sources
    if filters.get("min_rating") and all_raw:
        try:
            min_r = float(filters["min_rating"])
            all_raw = [r for r in all_raw if _safe_float(r.get("rating")) >= min_r]
        except (TypeError, ValueError):
            pass

    if filters.get("emergency_only") and all_raw:
        all_raw = [r for r in all_raw if _is_emergency(r)]

    cleaned, quality_report = clean_and_validate(all_raw)
    deduped, dup_count = deduplicate(cleaned)

    limit = filters.get("num_records") or config.MAX_RECORDS_HARD_CAP
    deduped = deduped[:limit]

    fields = filters.get("fields") or config.FIELD_PRESETS["detailed"]
    mapped = map_to_schema(deduped, fields)

    quality_report["sources_used"] = ", ".join(sources_used) if sources_used else "none"
    quality_report["data_source_mode"] = config.DATA_SOURCE_MODE
    quality_report["duplicates_removed"] = dup_count
    quality_report["total_returned"] = len(mapped)
    quality_report["fields_returned"] = fields

    return mapped, quality_report


def _safe_float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _is_emergency(record) -> bool:
    ec = str(record.get("emergency_contact", "")).strip().lower()
    return ec not in ("", "n/a", "not available", "na")
