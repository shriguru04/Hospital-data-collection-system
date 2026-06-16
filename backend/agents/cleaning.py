"""
Data Cleaning Agent + Deduplication Agent + Schema Mapping Agent
(collapsed into one module - all pure functions, no I/O).
"""

import re

from .. import config

PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-()]{6,18}[0-9]$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^https?://[^\s]+\.[^\s]+$")

NA_VALUES = {"", "n/a", "na", "none", "null", "not available", "-"}


def _normalize_value(value):
    if value is None:
        return "N/A"
    if isinstance(value, str) and value.strip().lower() in NA_VALUES:
        return "N/A"
    if isinstance(value, str):
        return value.strip()
    return value


def _validate_record(record):
    """Returns list of validation issue strings for this record."""
    issues = []

    phone = record.get("phone", "N/A")
    if phone != "N/A" and not PHONE_RE.match(str(phone)):
        issues.append("invalid_phone")

    email = record.get("email", "N/A")
    if email != "N/A" and not EMAIL_RE.match(str(email)):
        issues.append("invalid_email")

    website = record.get("website", "N/A")
    if website != "N/A" and not URL_RE.match(str(website)):
        issues.append("invalid_website")

    return issues


def clean_and_validate(records):
    """
    Normalizes every field to a consistent format and flags rows with
    validation problems (does not drop them - flags only, per the
    "mark missing/invalid as N/A, never silently fabricate" rule).
    """
    cleaned = []
    quality_report = {
        "total_raw": len(records),
        "rows_with_issues": 0,
        "issue_breakdown": {"invalid_phone": 0, "invalid_email": 0, "invalid_website": 0},
        "na_field_counts": {field: 0 for field in config.ALL_FIELDS},
    }

    for record in records:
        normalized = {field: _normalize_value(record.get(field, "N/A")) for field in config.ALL_FIELDS}

        issues = _validate_record(normalized)
        if issues:
            quality_report["rows_with_issues"] += 1
            for issue in issues:
                quality_report["issue_breakdown"][issue] += 1
            normalized["_validation_issues"] = issues
        else:
            normalized["_validation_issues"] = []

        for field in config.ALL_FIELDS:
            if normalized[field] == "N/A":
                quality_report["na_field_counts"][field] += 1

        cleaned.append(normalized)

    return cleaned, quality_report


def _dedup_key(record):
    name = str(record.get("hospital_name", "")).strip().lower()
    address = str(record.get("address", "")).strip().lower()
    phone = str(record.get("phone", "")).strip().lower()
    return (name, address, phone)


def deduplicate(records):
    seen = set()
    unique = []
    duplicates = 0

    for record in records:
        key = _dedup_key(record)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append(record)

    return unique, duplicates


def map_to_schema(records, fields):
    """
    Step 8 - Schema Mapping Agent.

    `fields` is a list of field names (subset of config.ALL_FIELDS, or
    arbitrary names if a custom schema/template was uploaded - see
    `apply_custom_schema` below).
    """
    mapped = []
    for record in records:
        row = {}
        for field in fields:
            row[field] = record.get(field, "N/A")
        mapped.append(row)
    return mapped


def apply_custom_schema(records, column_mapping):
    """
    Supports "If user uploads a custom schema: Follow the uploaded schema
    exactly."

    `column_mapping` is an ordered dict / list of tuples:
        [(output_column_name, internal_field_name_or_None), ...]

    If internal_field_name is None or not recognised, the output column
    is filled with "N/A" for every row (we never invent values for
    columns we have no data for).
    """
    mapped = []
    for record in records:
        row = {}
        for output_name, internal_field in column_mapping:
            if internal_field and internal_field in record:
                row[output_name] = record[internal_field]
            else:
                row[output_name] = "N/A"
        mapped.append(row)
    return mapped


def guess_column_mapping(uploaded_headers):
    """
    Best-effort fuzzy match between uploaded template headers and our
    internal field names, for the "Uploaded Excel Template" / "Custom
    Table Structure" input parameter. Falls back to None (-> "N/A"
    column) when no reasonable match is found.
    """
    normalized_internal = {f.replace("_", "").lower(): f for f in config.ALL_FIELDS}

    # Common header aliases users actually type in templates
    aliases = {
        "name": "hospital_name",
        "hospitalname": "hospital_name",
        "contactnumber": "phone",
        "phonenumber": "phone",
        "mobile": "phone",
        "emailid": "email",
        "emailaddress": "email",
        "addressline": "address",
        "pincode": "postal_code",
        "zipcode": "postal_code",
        "type": "hospital_type",
        "beds": "number_of_beds",
        "noofbeds": "number_of_beds",
        "specialization": "specializations",
        "department": "departments",
        "doctor": "doctor_information",
        "doctors": "doctor_information",
        "lat": "latitude",
        "lng": "longitude",
        "long": "longitude",
    }

    mapping = []
    for header in uploaded_headers:
        key = re.sub(r"[^a-z0-9]", "", header.lower())
        internal = normalized_internal.get(key) or aliases.get(key)
        mapping.append((header, internal))
    return mapping
