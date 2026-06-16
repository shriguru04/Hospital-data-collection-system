# Hospital Data Collection & Reporting Agent

A chat-driven console for requesting structured hospital data from **live web sources**, watching results move through a 10-step pipeline (validate → deduplicate → normalize → export), and downloading the output as Excel, CSV, JSON, or PDF.

---

## Quick start (copy-paste these three commands)

```bash
# 1 — enter the project folder
cd hospital-data-agent

# 2 — one-shot setup + start (Linux/macOS)
chmod +x run.sh && ./run.sh

# 2 — one-shot setup + start (Windows)
run.bat

# 3 — open the UI
# Browser will open at http://127.0.0.1:5000
```

That's it. The app starts in **mock mode** so you can test the whole pipeline immediately with no internet and no API keys.  
To switch to **live web data**, see §4 below.

---

## Open in VS Code

```bash
code hospital-data-agent.code-workspace
```

- Python interpreter is pre-set to `./venv/bin/python`  
- Press **F5** to launch with the built-in debugger (after running `./run.sh` at least once so the venv exists)

---

## Manual setup (if you don't want the shell scripts)

```bash
cd hospital-data-agent

# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate          # Linux/macOS
venv\Scripts\activate.bat         # Windows

# Install dependencies
pip install -r requirements.txt

# (Optional) configure live data sources
cp .env.example .env
# Edit .env — see §4

# Run
python -m backend.app
```

---

## Using the chat UI

Type a plain-English request in the chat box. Examples:

```
hospitals in Andheri, Mumbai, Maharashtra, India
top 10 private hospitals in Pune, Maharashtra, India — detailed information — export as xlsx
hospitals in San Francisco, California, USA with emergency available
hospital in Bandra Mumbai with rating above 4 only name and phone csv
City = Mumbai, State = Maharashtra, top 20, full, pdf
```

The agent extracts filters from your message and shows them as chips under the chat.  
You can refine them across multiple messages (e.g. say "only government type" next).

Once filters look right, click **Run Search**.  
The **Pipeline Ledger** on the right stamps each of the 10 workflow steps live.

After results appear, choose a format and click **Generate File**.  
The download link appears instantly — file is written to `outputs/`.

### Field presets

| Say this          | Fields returned |
|-------------------|-----------------|
| `only name and phone` | hospital_name, phone |
| `basic`           | name, phone, city, state |
| `detailed information` | name, phone, email, website, address, area, city, state, specializations |
| `full` / `all fields` | all 20 fields |

### Custom schema

Open **Export → Custom schema**, paste your template's column headers  
(comma-separated), click **Guess Mapping**, adjust any column-to-field  
mapping, then export. Unmapped columns fill with `"N/A"` — no values are  
ever invented.

---

## Live data source modes

Set `DATA_SOURCE_MODE` in your `.env` file:

| Mode | What it does | Requires |
|------|-------------|----------|
| `mock` | Reads `sample_data/mock_hospitals.json` | Nothing — default |
| **`live`** | **Multi-source waterfall (recommended)** | Internet access |
| `osm` | OpenStreetMap / Overpass API only | Internet access |
| `places` | Google Places API only | API key + internet |

### `live` mode — source waterfall

When `DATA_SOURCE_MODE=live`, the agent queries sources in this order and merges results:

```
1. Google Places API     — structured, fast, most accurate
   (skipped if GOOGLE_PLACES_API_KEY is empty)

2. OpenStreetMap / Overpass API   — free, global, no key needed
   Nominatim geocodes your city → Overpass fetches hospitals within 20 km

3. JustDial scraper      — justdial.com (best for Indian cities)
   Parses JSON-LD structured data + HTML cards from listing pages

4. Yelp scraper          — yelp.com (best for USA / Western cities)
   Parses JSON-LD structured data from Yelp search results
```

All results are merged, then cleaned, validated, and deduplicated before export.

### Getting a Google Places API key (optional but recommended)

```
1. Go to https://console.cloud.google.com/
2. Create a project (or use an existing one)
3. Enable "Places API"
4. Create an API key under Credentials
5. Add it to your .env:
   GOOGLE_PLACES_API_KEY=AIza...
```

The free tier gives 10,000 searches/month (more than enough for typical use).

### OSM-only mode (completely free, no sign-up)

```bash
# .env
DATA_SOURCE_MODE=osm
```

OpenStreetMap has good global hospital coverage. Fields available from OSM:
name, phone, email, website, address, beds (if tagged), emergency status, lat/lon.  
Fields OSM cannot provide: rating, departments, doctor info, specializations beyond broad tags.

---

## Project structure

```
hospital-data-agent/
├── README.md
├── requirements.txt          Flask + pandas + openpyxl + reportlab + requests + bs4 + lxml
├── run.sh                    One-shot setup + run (Linux/macOS)
├── run.bat                   One-shot setup + run (Windows)
├── .env.example              Copy to .env — all config options documented
├── .gitignore
├── hospital-data-agent.code-workspace   VS Code workspace (interpreter + debug config)
│
├── sample_data/
│   └── mock_hospitals.json   6 sample records for offline testing
│
├── outputs/                  Generated xlsx/csv/json/pdf files land here
│
├── backend/
│   ├── app.py                Flask app + API routes (chat / search / export / download)
│   ├── config.py             All config — reads from environment variables
│   └── agents/
│       ├── planner.py        Steps 1-2: parse chat message → structured filter dict
│       ├── data_sources.py   Steps 3-4: Google Places + OSM/Overpass + JustDial + Yelp
│       ├── cleaning.py       Steps 5-8: validate / deduplicate / normalize / schema-map
│       └── export.py         Steps 9-10: generate xlsx / csv / json / pdf
│
└── frontend/
    ├── index.html            Chat UI ("Case File Console")
    ├── style.css             Dark terminal + paper-ledger aesthetic
    └── script.js             All UI logic — pipeline ledger, results table, export
```

---

## How chat maps onto the 10-step workflow

| Step | Module | What happens |
|------|--------|-------------|
| 1. Understand requirements | `planner.parse` | Free-text → structured filter dict |
| 2. Identify target locations | `planner.parse` | Extracts area/city/state/country/postal code |
| 3. Search trusted sources | `data_sources._get_sources()` | Google Places → OSM → JustDial → Yelp |
| 4. Collect hospital records | `data_sources.run_pipeline` | Each source searched, results merged |
| 5. Validate records | `cleaning.clean_and_validate` | Regex checks on phone/email/website |
| 6. Remove duplicates | `cleaning.deduplicate` | Dedup on (name, address, phone) |
| 7. Normalize data | `cleaning.clean_and_validate` | Blanks / "na" / "-" → "N/A", whitespace trimmed |
| 8. Map to schema | `cleaning.map_to_schema` or `apply_custom_schema` | Selects/renames columns |
| 9. Generate report | `export.export*` | Summary + Hospital Details + Contact sheets |
| 10. Export format | `export.export` | Writes `.xlsx` / `.csv` / `.json` / `.pdf` to `outputs/` |

---

## API reference

| Endpoint | Method | Body | Returns |
|----------|--------|------|---------|
| `/api/health` | GET | — | `{status, data_source_mode, llm_planner_enabled}` |
| `/api/chat` | POST | `{message, filters}` | `{reply, filters, ready, summary}` |
| `/api/search` | POST | `{filters}` | `{records, quality_report}` |
| `/api/schema/guess` | POST | `{headers: [...]}` | `{mapping: [{column, field}]}` |
| `/api/export` | POST | `{records, fields, format, quality_report, filters, custom_mapping?}` | `{filename, download_url}` |
| `/outputs/<filename>` | GET | — | File download |

---

## Configuration reference

All values set in `.env` (copy from `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `127.0.0.1` | Flask bind address |
| `PORT` | `5000` | Flask port |
| `DEBUG` | `true` | Flask debug mode |
| `DATA_SOURCE_MODE` | `mock` | `mock` / `live` / `osm` / `places` |
| `GOOGLE_PLACES_API_KEY` | *(empty)* | Required only for `places` mode; enhances `live` mode |
| `ANTHROPIC_API_KEY` | *(empty)* | Optional — enables LLM-based chat parsing |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Model used for chat parsing |
| `SOURCE_REQUEST_DELAY_SECONDS` | `1.0` | Minimum delay between outbound HTTP requests |
| `MAX_RECORDS_HARD_CAP` | `200` | Hard ceiling on records per search |

---

## Troubleshooting

**`ModuleNotFoundError`** — not in the virtual environment. Run `./run.sh` again.

**"No matching hospitals found"** in `mock` mode — only 6 sample records exist (Mumbai/Andheri, Mumbai/Bandra, Pune, San Francisco). Switch to `DATA_SOURCE_MODE=live`.

**JustDial returns N/A for most fields** — JustDial changes their HTML structure periodically. The scraper extracts JSON-LD structured data first (most reliable); if that's gone, CSS selectors may need updating in `backend/agents/data_sources.py` → `JustDialScraper._card_to_record`.

**Yelp returns 0 results** — Yelp occasionally serves a CAPTCHA to scrapers. Use `DATA_SOURCE_MODE=osm` or add a `GOOGLE_PLACES_API_KEY` for reliable results.

**Port already in use** — change `PORT=5001` in `.env`.

**PDF looks cramped** — switch to `basic` or `name_and_phone` preset, or export to Excel which splits data across multiple sheets.

---

## Known data field limitations by source

| Field | Google Places | OpenStreetMap | JustDial | Yelp |
|-------|:---:|:---:|:---:|:---:|
| Name | ✅ | ✅ | ✅ | ✅ |
| Phone | ✅ | ✅ (if tagged) | ✅ | ✅ |
| Email | ❌ | ✅ (if tagged) | ✅ | ❌ |
| Website | ✅ | ✅ (if tagged) | ❌ | ✅ |
| Address | ✅ | ✅ | ✅ | ✅ |
| Rating | ✅ | ❌ | ✅ | ✅ |
| Beds | ❌ | ✅ (if tagged) | ❌ | ❌ |
| Emergency | partial | ✅ | ❌ | ❌ |
| Specializations | ❌ | partial | ❌ | ❌ |
| Lat/Lon | ✅ | ✅ | partial | partial |
| Doctor info | ❌ | ❌ | ❌ | ❌ |

`"N/A"` is always used for unavailable fields. No data is invented.

