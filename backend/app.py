"""
Hospital Data Collection & Reporting Agent - Flask backend.

Run with:
    python -m backend.app

(from the project root - see README.md for full setup instructions)

Endpoints
---------
GET  /                  -> serves the chat UI (frontend/index.html)
POST /api/chat          -> {message, filters} -> {reply, filters, ready}
POST /api/search        -> {filters} -> {records, quality_report}
POST /api/export        -> {records, fields, format, filters} -> {filename, download_url}
GET  /api/uploads/schema -> POST a .csv/.xlsx file -> returns guessed column mapping
GET  /outputs/<filename> -> download a generated export file
"""

import os

from flask import Flask, request, jsonify, send_from_directory, render_template

from . import config
from .agents import planner, data_sources, export as export_agent, cleaning

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

app = Flask(
    __name__,
    static_folder=FRONTEND_DIR,
    static_url_path="",
    template_folder=FRONTEND_DIR,
)


# --------------------------------------------------------------------------
# Frontend
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


# --------------------------------------------------------------------------
# Chat / Planner Agent
# --------------------------------------------------------------------------
@app.route("/api/chat", methods=["POST"])
def chat():
    body = request.get_json(force=True) or {}
    message = body.get("message", "")
    existing_filters = body.get("filters") or {}

    filters = planner.parse(message, existing_filters)
    ready = planner.is_ready_to_search(filters)
    summary = planner.filters_summary(filters)

    if ready:
        reply = (
            "Got it. Here's what I'll search for:\n"
            f"{summary}\n\n"
            "Tap \"Run Search\" to fetch results, or keep telling me "
            "more filters (hospital type, specialty, minimum rating, "
            "emergency-only, number of records, output format)."
        )
    else:
        reply = (
            "I need at least a location to search on - a country, "
            "state, city, area, or postal code. "
            "For example: \"hospitals in Andheri, Mumbai, Maharashtra, India\"."
        )

    return jsonify({"reply": reply, "filters": filters, "ready": ready, "summary": summary})


# --------------------------------------------------------------------------
# Search / Web-Scraping / Cleaning / Dedup / Schema-Mapping Agents
# --------------------------------------------------------------------------
@app.route("/api/search", methods=["POST"])
def search():
    body = request.get_json(force=True) or {}
    filters = body.get("filters") or {}

    if not planner.is_ready_to_search(filters):
        return jsonify({"error": "At least one location filter (country, state, city, area, postal_code) is required."}), 400

    try:
        records, quality_report = data_sources.run_pipeline(filters)
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the UI
        return jsonify({"error": f"Search failed: {exc}"}), 500

    if not records:
        return jsonify({
            "records": [],
            "quality_report": quality_report,
            "message": "No matching hospitals found for the selected filters.",
        })

    return jsonify({"records": records, "quality_report": quality_report})


# --------------------------------------------------------------------------
# Custom schema upload (Step: "follow uploaded schema exactly")
# --------------------------------------------------------------------------
@app.route("/api/schema/guess", methods=["POST"])
def guess_schema():
    """
    Body: {"headers": ["Hospital Name", "Contact Number", "City", ...]}
    Returns a guessed mapping the UI can show for the user to confirm/edit
    before export.
    """
    body = request.get_json(force=True) or {}
    headers = body.get("headers") or []
    mapping = cleaning.guess_column_mapping(headers)
    return jsonify({"mapping": [{"column": h, "field": f} for h, f in mapping]})


# --------------------------------------------------------------------------
# Export Agent
# --------------------------------------------------------------------------
@app.route("/api/export", methods=["POST"])
def do_export():
    body = request.get_json(force=True) or {}
    records = body.get("records") or []
    fields = body.get("fields") or config.FIELD_PRESETS["detailed"]
    fmt = (body.get("format") or "xlsx").lower()
    quality_report = body.get("quality_report")
    filters = body.get("filters") or {}

    custom_mapping = body.get("custom_mapping")  # [{"column": ..., "field": ...}, ...]
    if custom_mapping:
        mapping_tuples = [(item["column"], item.get("field")) for item in custom_mapping]
        records = cleaning.apply_custom_schema(records, mapping_tuples)
        fields = [item["column"] for item in custom_mapping]

    if fmt not in ("xlsx", "csv", "json", "pdf"):
        return jsonify({"error": f"Unsupported format '{fmt}'. Use xlsx, csv, json or pdf."}), 400

    if not records:
        return jsonify({"error": "No records to export. Run a search first."}), 400

    filename = export_agent.export(
        fmt=fmt,
        records=records,
        fields=fields,
        quality_report=quality_report,
        search_criteria=filters,
    )

    return jsonify({"filename": filename, "download_url": f"/outputs/{filename}"})


@app.route("/outputs/<path:filename>")
def download(filename):
    return send_from_directory(config.OUTPUT_DIR, filename, as_attachment=True)


# --------------------------------------------------------------------------
# Health check
# --------------------------------------------------------------------------
@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "data_source_mode": config.DATA_SOURCE_MODE,
        "llm_planner_enabled": bool(config.ANTHROPIC_API_KEY),
    })


if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
