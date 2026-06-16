"""
Export Agent.

Generates the final downloadable file in one of the four supported
formats. Each function writes into config.OUTPUT_DIR and returns the
filename (not the full path - the Flask route builds the download URL).
"""

import csv
import json
import os
import uuid
from datetime import datetime

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from .. import config


def _timestamped_name(base, ext):
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"{base}_{ts}_{suffix}.{ext}"


def export_csv(records, fields, base_name="hospital_data"):
    filename = _timestamped_name(base_name, "csv")
    path = os.path.join(config.OUTPUT_DIR, filename)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({k: record.get(k, "N/A") for k in fields})

    return filename


def export_json(records, fields, quality_report=None, base_name="hospital_data"):
    filename = _timestamped_name(base_name, "json")
    path = os.path.join(config.OUTPUT_DIR, filename)

    payload = {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "total_records": len(records),
        "fields": fields,
        "data_quality_report": quality_report or {},
        "records": [{k: record.get(k, "N/A") for k in fields} for record in records],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    return filename


def export_xlsx(records, fields, quality_report=None, search_criteria=None, base_name="hospital_data"):
    filename = _timestamped_name(base_name, "xlsx")
    path = os.path.join(config.OUTPUT_DIR, filename)

    df = pd.DataFrame([{k: record.get(k, "N/A") for k in fields} for record in records])

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        # Sheet 1: Summary
        summary_rows = [
            ("Generated (UTC)", datetime.utcnow().isoformat() + "Z"),
            ("Total Hospitals", len(records)),
        ]
        if search_criteria:
            for key, value in search_criteria.items():
                if value:
                    summary_rows.append((f"Filter: {key}", value))
        if quality_report:
            summary_rows.append(("Duplicates Removed", quality_report.get("duplicates_removed", 0)))
            summary_rows.append(("Rows With Validation Issues", quality_report.get("rows_with_issues", 0)))
            summary_rows.append(("Data Source", quality_report.get("source", "unknown")))

        pd.DataFrame(summary_rows, columns=["Metric", "Value"]).to_excel(writer, sheet_name="Summary", index=False)

        # Sheet 2: Hospital Details (everything except contact-only fields)
        detail_fields = [f for f in fields if f not in ("phone", "alternate_phone", "email", "emergency_contact")]
        if detail_fields:
            df[detail_fields].to_excel(writer, sheet_name="Hospital Details", index=False)

        # Sheet 3: Contact Information
        contact_fields = [f for f in ("hospital_name", "phone", "alternate_phone", "email", "emergency_contact", "website") if f in fields]
        if contact_fields:
            df[contact_fields].to_excel(writer, sheet_name="Contact Information", index=False)

        # Always include the full mapped dataset too, in case a custom
        # schema mixes fields across the categories above
        df.to_excel(writer, sheet_name="Full Dataset", index=False)

    return filename


def export_pdf(records, fields, quality_report=None, search_criteria=None, base_name="hospital_data"):
    filename = _timestamped_name(base_name, "pdf")
    path = os.path.join(config.OUTPUT_DIR, filename)

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=landscape(A4), title="Hospital Data Report")
    elements = []

    elements.append(Paragraph("Hospital Data Report", styles["Title"]))
    elements.append(Spacer(1, 6 * mm))

    # Search Criteria
    elements.append(Paragraph("Search Criteria", styles["Heading2"]))
    if search_criteria:
        criteria_text = ", ".join(f"{k}: {v}" for k, v in search_criteria.items() if v)
        elements.append(Paragraph(criteria_text or "No filters applied", styles["Normal"]))
    else:
        elements.append(Paragraph("No filters applied", styles["Normal"]))
    elements.append(Spacer(1, 4 * mm))

    # Summary statistics
    elements.append(Paragraph("Summary Statistics", styles["Heading2"]))
    summary_lines = [f"Total Hospitals Found: {len(records)}"]
    if quality_report:
        summary_lines.append(f"Duplicates Removed: {quality_report.get('duplicates_removed', 0)}")
        summary_lines.append(f"Rows With Validation Issues: {quality_report.get('rows_with_issues', 0)}")
        summary_lines.append(f"Data Source: {quality_report.get('source', 'unknown')}")
    for line in summary_lines:
        elements.append(Paragraph(line, styles["Normal"]))
    elements.append(Spacer(1, 4 * mm))

    # Hospital table
    elements.append(Paragraph("Hospital Table", styles["Heading2"]))
    table_data = [fields] + [[str(record.get(f, "N/A")) for f in fields] for record in records]
    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f7")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 4 * mm))

    # Timestamp
    elements.append(Paragraph(f"Generated: {datetime.utcnow().isoformat()}Z (UTC)", styles["Normal"]))

    doc.build(elements)
    return filename


def export(fmt, records, fields, quality_report=None, search_criteria=None, base_name="hospital_data"):
    if fmt == "csv":
        return export_csv(records, fields, base_name)
    if fmt == "json":
        return export_json(records, fields, quality_report, base_name)
    if fmt == "pdf":
        return export_pdf(records, fields, quality_report, search_criteria, base_name)
    # default / "xlsx"
    return export_xlsx(records, fields, quality_report, search_criteria, base_name)
