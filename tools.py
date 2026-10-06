"""The tools the harness can run, and the JSON that describes them to the model."""

import json
import re
import requests
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# URLs for the NYC Open Data datasets and the geocoder service used
GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"
HPD_VIOLATIONS_URL = "https://data.cityofnewyork.us/resource/wvxf-dwi5.json"
THREE_ELEVEN_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
HPD_REGISTRATIONS_URL = "https://data.cityofnewyork.us/resource/tesw-yqqr.json"
HPD_CONTACTS_URL = "https://data.cityofnewyork.us/resource/feu5-w2e2.json"

# Besides everything filed with HPD (building conditions), also keep noise complaints which go to the NYPD
EXTRA_TYPES = ["Noise - Residential"]

# 311 look-back window in years: default, and the most the model may request
DEFAULT_YEARS = 3
MAX_YEARS = 10

# Max number of buildings we investigate per landlord or agent
MAX_PORTFOLIO = 300

# Spellings of legal suffixes that are unified when comparing company names
SUFFIX_ALIASES = {"CORPORATION": "CORP", "COMPANY": "CO", "LIMITED": "LTD", "INCORPORATED": "INC"}

# Contact types that mean the owner is a person
INDIVIDUAL_OWNER_TYPES = {"IndividualOwner", "JointOwner"}


# ---------------------------------------------------------------------------
# Data access: geocoding and Socrata requests
# ---------------------------------------------------------------------------

def resolve_address(address: str) -> dict:
    """Look up a NYC building by street address; returns its BBL, BIN, matched address and coordinates.
    Include the borough, e.g. "245 W 104th St, Manhattan". Returns an "error" field if no building is found."""

    # Clean and check the input
    address = (address or "").strip()
    if not address:
        return {"error": "empty_input", "message": "No address was provided."}

    # Ask the NYC geocoder for up to 5 candidate address matches
    try:
        resp = requests.get(
            GEOSEARCH_URL,
            params={"text": address, "size": 5},
            timeout=10
        )

        # Raise an exception on HTTP errors
        resp.raise_for_status()

        features = resp.json().get("features", [])

    except (requests.RequestException, ValueError) as e:
        return {"error": "geocoder_unavailable",
                "message": f"Address lookup service failed: {e}"}

    # From the 5 candidate matches, keep only results that are actual buildings (have a BBL)
    buildings = [
        f for f in features
        if f.get("properties", {}).get("addendum", {}).get("pad", {}).get("bbl")
    ]
    if not buildings:
        # No matches found
        return {"error": "not_found",
                "message": f"No NYC building found for '{address}'. "
                           "Ask for the street number, street name, and borough."}

    best = buildings[0]
    props = best["properties"]
    pad = props["addendum"]["pad"]
    lon, lat = best["geometry"]["coordinates"]  # GeoJSON order is [lon, lat]

    return {
        "bbl": pad["bbl"],
        "bin": pad.get("bin"),
        "label": props.get("label"),
        "latitude": lat,
        "longitude": lon,
        "confidence": props.get("confidence"),
        "alternatives": [f["properties"].get("label") for f in buildings[1:]],
    }

def _soda_get(url: str, params: dict) -> list[dict]:
    """Generic Socrata request: returns rows, or raises on network/HTTP/JSON errors"""
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def _split_bbl(bbl: str) -> tuple[str, str, str]:
    """Extract the borough, block and lot from the BBL ID and removes leading zeros: '3030310015' -> ('3', '3031', '15')"""
    # A BBL has fixed positions: 1 digit borough, 5 digits block, 4 digits lot
    return bbl[0], str(int(bbl[1:6])), str(int(bbl[6:]))


# ---------------------------------------------------------------------------
# Formatting and summarizing helpers
# ---------------------------------------------------------------------------

def _clean_text(text: str, limit: int = 200) -> str:
    """Make free text safe and compact for the model"""
    # Drop non-printable characters
    text = "".join(ch for ch in text if ch.isprintable() or ch.isspace())
    # Collapse runs of whitespace/newlines into single spaces, then cap the length to save tokens
    return " ".join(text.split())[:limit]

def _format_location(row: dict) -> str:
    """Build 'apt 5, story 3' from whatever parts exist in the violation row. If both are missing, return 'not specified'"""
    parts = []
    if row.get("apartment"):
        parts.append(f"apt {row['apartment']}")
    if row.get("story"):
        parts.append(f"story {row['story']}")
    return ", ".join(parts) or "not specified"

def _normalize_name(name: str) -> str:
    """Canonical form of a company name so harmless spelling variants compare equal:
    'ABC Realty, L.L.C.' and 'abc realty llc' -> 'ABC REALTY LLC'. Legal suffixes are
    unified but kept, since 'ABC LLC' and 'ABC INC' are different entities."""
    name = name.upper().replace("&", " AND ")
    name = re.sub(r"[.'’]", "", name)            # L.L.C. -> LLC, O'Brien -> OBRIEN
    name = re.sub(r"[^A-Z0-9]+", " ", name)       # remaining punctuation -> space
    return " ".join(SUFFIX_ALIASES.get(t, t) for t in name.split())

def _company_names(contacts: list[dict], contact_type: str) -> list[str]:
    """Collects unique company names for one contact role (example: 'CorporateOwner', 'Agent')."""
    names: dict[str, str] = {}   # normalized key -> first spelling seen
    for c in contacts:
        name = c.get("corporationname")
        if c.get("type") == contact_type and name:
            names.setdefault(_normalize_name(name), name)
    return list(names.values())

def _count_by_class(rows: list[dict]) -> dict:
    """Turn grouped violation rows into totals per violation class : {"by_class": {"B": {"open": 8, "closed": 3}}, "total_violations", "total_open"}."""
    by_class: dict[str, dict[str, int]] = {}
    for row in rows:
        cls = row.get("class", "?")
        status = "open" if row.get("violationstatus") == "Open" else "closed"   # the data says "Close"
        by_class.setdefault(cls, {"open": 0, "closed": 0})
        by_class[cls][status] += int(row["n"])   # n arrives as a string
    return {
        "by_class": dict(sorted(by_class.items())),
        "total_violations": sum(c["open"] + c["closed"] for c in by_class.values()),
        "total_open": sum(c["open"] for c in by_class.values()),
    }

def _summarize_311(rows: list[dict]) -> dict:
    """Turn grouped 311 rows into totals per complaint type"""
    merged: dict[str, dict] = {}
    for row in rows:
        # Uppercase the complaint type to handle variations in spelling
        key = (row.get("complaint_type") or "UNKNOWN").upper()
        count = int(row["n"])                          # n arrives as a string
        latest = (row.get("latest") or "")[:10]        # timestamp -> "2026-09-29"
        if key not in merged:
            merged[key] = {"count": count, "latest": latest}
        else:
            merged[key]["count"] += count
            merged[key]["latest"] = max(merged[key]["latest"], latest)

    # Sort complaint type by descending count
    by_type = dict(sorted(merged.items(), key=lambda kv: kv[1]["count"], reverse=True))
    return {
        "total_complaints": sum(v["count"] for v in by_type.values()),
        "by_type": by_type,
    }

def _portfolio(contact_type: str, companies: list[str]) -> dict:
    """Count the HPD registrations listing any of `companies` under `contact_type`
    (CorporateOwner or Agent), and total the violations across them."""

    # The database can't normalize names, so fetch every contact whose name starts with the
    #    same first word as one of ours, then keep only those that match after normalizing.
    targets = {_normalize_name(c) for c in companies}
    prefixes = {re.sub(r"[^A-Z0-9].*", "", c.upper()) for c in companies}
    like = " OR ".join(f"upper(corporationname) like '{p}%'" for p in sorted(prefixes) if p)
    if not like:
        return {"names": companies, "registrations": 0}
    rows = _soda_get(HPD_CONTACTS_URL, {
        "$select": "registrationid, corporationname",
        "$where": f"type='{contact_type}' AND ({like})",
        "$group": "registrationid, corporationname",
        "$limit": 50000,
    })
    ids = sorted({r["registrationid"] for r in rows
                  if _normalize_name(r.get("corporationname") or "") in targets})
    result = {"names": companies, "registrations": len(ids)}

    # If there are too many to put in one request URL report the count only
    if len(ids) > MAX_PORTFOLIO:
        result["note"] = f"More than {MAX_PORTFOLIO} registrations, too many to total violations."
        return result
    if not ids:
        return result

    # Count violations across all registrations found
    id_list = ", ".join(f"'{i}'" for i in ids)
    rows = _soda_get(HPD_VIOLATIONS_URL, {
        "$select": "class, violationstatus, count(*) as n",
        "$where": f"registrationid in ({id_list})",
        "$group": "class, violationstatus",
    })
    result.update(_count_by_class(rows))
    return result


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

# TOOL 1
def get_building_violations(address: str) -> dict:
    """Get HPD housing violations"""

    # Find the building associated to the address and split into borough, block and lot
    building = resolve_address(address)
    if "error" in building:
        return building
    boroid, block, lot = _split_bbl(building["bbl"])
    where = f"boroid='{boroid}' AND block='{block}' AND lot='{lot}'"

    try:
        # Counts per class and status, calculated by the database
        count_rows = _soda_get(HPD_VIOLATIONS_URL, {
            "$select": "class, violationstatus, count(*) as n",
            "$where": where,
            "$group": "class, violationstatus",
        })
        # Record the five most recent violations
        recent_rows = _soda_get(HPD_VIOLATIONS_URL, {
            "$select": "class, inspectiondate, violationstatus, apartment, story, novdescription",
            "$where": where,
            "$order": "inspectiondate DESC",
            "$limit": 5,
        })
    except (requests.RequestException, ValueError) as e:
        return {"error": "data_source_unavailable",
                "message": f"NYC Open Data request failed: {e}"}

    counts = _count_by_class(count_rows)

    # Zero rows could mean: a clean building or one HPD doesn't track
    if counts["total_violations"] == 0:
        return {
            "building": building["label"],
            "total_violations": 0,
            "note": "No HPD violations found. This can mean a clean building, or a "
                    "non-residential building HPD doesn't cover.",
        }

    return {
        "building": building["label"],
        **counts,
        "recent_examples": [
            {
                "class": r.get("class"),
                "date": (r.get("inspectiondate") or "")[:10],
                # Same open/closed wording as the counts (the raw data says "Close")
                "status": "open" if r.get("violationstatus") == "Open" else "closed",
                "location": _format_location(r),
                "description": _clean_text(r.get("novdescription") or ""),
            }
            for r in recent_rows
        ],
    }

# TOOL 2
def get_311_complaints(address: str, years: int = DEFAULT_YEARS) -> dict:
    """Get 311 tenant complaints"""

    # Find the building associated to the address
    building = resolve_address(address)
    if "error" in building:
        return building

    # Time window: the model may send a string or an absurd value, so coerce and clamp
    try:
        years = max(1, min(int(years), MAX_YEARS))
    except (TypeError, ValueError):
        years = DEFAULT_YEARS
    since = (date.today() - timedelta(days=365 * years)).isoformat()

    # Filter by building, time window, and either an HPD complaint (building
    #    conditions) or one of the extra types
    types = ", ".join(f"'{t}'" for t in EXTRA_TYPES)
    where = (
        f"bbl='{building['bbl']}' "
        f"AND created_date >= '{since}T00:00:00' "
        f"AND (agency='HPD' OR complaint_type in ({types}))"
    )

    try:
        # One row per complaint type
        rows = _soda_get(THREE_ELEVEN_URL, {
            "$select": "complaint_type, count(*) as n, max(created_date) as latest",
            "$where": where,
            "$group": "complaint_type",
            "$order": "n DESC",
        })
    except (requests.RequestException, ValueError) as e:
        return {"error": "data_source_unavailable",
                "message": f"NYC Open Data request failed: {e}"}

    result = {
        "building": building["label"],
        "window": f"last {years} year{'s' if years != 1 else ''} (since {since})",
    }

    # No rows means either a quiet building or one whose tenants don't use 311
    if not rows:
        result.update(total_complaints=0, by_type={},
                      note="No matching 311 complaints in this window. 311 only reflects what "
                           "residents report, so this does not prove there are no problems.")
        return result

    # Merge case variants and add totals.
    result.update(_summarize_311(rows))
    return result

# TOOL 3
def get_landlord_portfolio(address: str) -> dict:
    """Get the HPD violations associated to the owner and managing agent from an address"""

    # Find the building associated to the address and split into borough, block and lot
    building = resolve_address(address)
    if "error" in building:
        return building
    boroid, block, lot = _split_bbl(building["bbl"])

    try:
        # Finds all registrations on this lot, newest end date first
        regs = _soda_get(HPD_REGISTRATIONS_URL, {
            "$where": f"boroid='{boroid}' AND block='{block}' AND lot='{lot}'",
            "$order": "registrationenddate DESC",
        })
        if not regs:
            return {"building": building["label"], "registered": False,
                    "note": "No HPD registration found. Some small or owner-occupied buildings "
                            "are not required to register, or this may not be a residential building."}

        # Retrieves contacts for those registrations. Only the type and company name are requested,
        #    so personal names never enter this program.
        id_list = ", ".join(f"'{r['registrationid']}'" for r in regs)   # IDs are digits from the dataset
        contacts = _soda_get(HPD_CONTACTS_URL, {
            "$select": "type, corporationname",
            "$where": f"registrationid in ({id_list})",
            "$limit": 50000,   # Socrata defaults to 1000 rows
        })
        owner_names = _company_names(contacts, "CorporateOwner")
        agent_names = _company_names(contacts, "Agent")
        owner_is_individual = any(c.get("type") in INDIVIDUAL_OWNER_TYPES for c in contacts)

        # Portfolio totals per company
        if owner_names:
            owner = _portfolio("CorporateOwner", owner_names)
        elif owner_is_individual:
            owner = {"note": "Registered to an individual owner; not named or searched."}
        else:
            owner = {"note": "No owner company on record."}
        agent = _portfolio("Agent", agent_names) if agent_names else {"note": "No managing agent company on record."}

    except (requests.RequestException, ValueError) as e:
        return {"error": "data_source_unavailable",
                "message": f"NYC Open Data request failed: {e}"}

    # Registration status: ISO date strings compare correctly as plain strings
    end = (regs[0].get("registrationenddate") or "")[:10]
    return {
        "building": building["label"],
        "registered": True,
        "registration": {
            "count": len(regs),
            "latest_end_date": end or None,
            "possibly_expired": bool(end) and end < date.today().isoformat(),
        },
        "owner": owner,
        "managing_agent": agent,
        "note": "Portfolios are buildings registered under the same company name(s) as this building. "
        "Names are matched after ignoring case, punctuation and suffix spelling (LLC vs L.L.C.); other "
        "variants (different words, abbreviations) are missed, so counts are a minimum. "
        "A managing agent can serve many unrelated owners, so agent totals are not one landlord's record.",
    }


# ---------------------------------------------------------------------------
# Tool registry and dispatch
# ---------------------------------------------------------------------------

# What the model sees: the "set notes" in the screenplay.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_building_violations",
            "description": "Get official HPD violations for a building",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "Address, e.g. '22 Stagg St, Brooklyn'"},
                },
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_311_complaints",
            "description": "Get housing complaints residents filed to 311 for a NYC building",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "Address, e.g. '22 Stagg St, Brooklyn'"},
                    "years": {"type": "integer", "minimum": 1, "maximum": MAX_YEARS,
                              "description": f"How many years back to look (default {DEFAULT_YEARS})"},
                },
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_landlord_portfolio",
            "description": "Get the HPD violations of buildings owned by the same landlord and managed by the same agency",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "Address, e.g. '22 Stagg St, Brooklyn'"},
                },
                "required": ["address"],
            },
        },
    },
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {
    "get_building_violations": get_building_violations,
    "get_311_complaints": get_311_complaints,
    "get_landlord_portfolio": get_landlord_portfolio,
}

def run_tool(name: str, args: dict) -> str:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}"})
    try:
        return json.dumps(TOOL_MAP[name](**args))
    except TypeError as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}"})
