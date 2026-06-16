"""
Central configuration for the Hospital Data Collection Agent.

All values are read from environment variables so secrets never live in code.
Copy `.env.example` to `.env` and fill in the values you have.

Nothing in this file requires network access to import - the app will
start fine with zero keys set and will simply run in MOCK data mode.
"""

import os

# --- General -----------------------------------------------------------
# --- General -----------------------------------------------------------
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "10000"))
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

# --- Data source mode ---------------------------------------------------
# "mock"   -> uses sample_data/mock_hospitals.json (default, no internet
#             needed, good for testing the UI/pipeline end to end)
# "live"   -> multi-source waterfall:
#              1. Google Places API (if GOOGLE_PLACES_API_KEY is set)
#              2. OpenStreetMap / Overpass API (free, no key needed)
#              3. JustDial scraper (India hospitals)
#              4. Yelp scraper (USA/global)
#             Results from all sources are merged and deduplicated.
# "osm"    -> OpenStreetMap / Overpass only (free, no key, global)
# "places" -> Google Places API only (requires GOOGLE_PLACES_API_KEY)
DATA_SOURCE_MODE = os.environ.get("DATA_SOURCE_MODE", "mock")

GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")

# --- Optional LLM-assisted intent parsing --------------------------------
# If set, the planner agent will call the Anthropic API to turn free-text
# requests into structured filters. If empty, a deterministic keyword/regex
# parser is used instead (no network call, fully offline).
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# --- Rate limiting / scraping etiquette ----------------------------------
# Minimum seconds to wait between outbound requests to any single external
# data source. Applies only when DATA_SOURCE_MODE == "places".
SOURCE_REQUEST_DELAY_SECONDS = float(os.environ.get("SOURCE_REQUEST_DELAY_SECONDS", "1.0"))

# Maximum records returned for a single search, regardless of what the
# user asked for. Prevents runaway scraping jobs.
MAX_RECORDS_HARD_CAP = int(os.environ.get("MAX_RECORDS_HARD_CAP", "200"))

# --- Output ---------------------------------------------------------------
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

ALL_FIELDS = [
    "hospital_name",
    "phone",
    "alternate_phone",
    "email",
    "website",
    "address",
    "area",
    "city",
    "state",
    "country",
    "postal_code",
    "emergency_contact",
    "hospital_type",
    "number_of_beds",
    "specializations",
    "departments",
    "doctor_information",
    "rating",
    "latitude",
    "longitude",
]

# Pre-defined field presets referenced by the planner / UI quick buttons
FIELD_PRESETS = {
    "basic": ["hospital_name", "phone", "city", "state"],
    "name_and_phone": ["hospital_name", "phone"],
    "detailed": [
        "hospital_name",
        "phone",
        "email",
        "website",
        "address",
        "area",
        "city",
        "state",
        "specializations",
    ],
    "full": ALL_FIELDS,
}
