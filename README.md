# NYC Apartment Check

A chat assistant that helps NYC renters and buyers do due diligence on a building before they sign. Give it a street address and it pulls official NYC Open Data: housing violations, resident complaints, and the landlord's wider record. It then summarizes the key numbers and patterns in plain language. 

## Tools

| Tool | What it does |
|---|---|
| `get_building_violations` | Counts HPD housing violations for the building by class (A/B/C/I) and open/closed, plus the 5 most recent as examples. |
| `get_311_complaints` | Counts housing complaints (and residential noise) that residents filed through 311 for the building, by type. Defaults to the last 3 years, up to 10. |
| `get_landlord_portfolio` | Finds the registered owner and managing agent companies and totals HPD violations across other buildings registered under the same names. |

## How to use

This agent takes free text as input. Give an NYC street address (with the borough) and the agent looks up the building's violations and complaints. Additonally, you can ask about the landlord's portfolio. 

Example queries:

- `245 W 104th St, Manhattan`
- `Is 22 Stagg St, Brooklyn any good? Look at the last 5 years of complaints.`
- `Who owns 245 W 104th St, Manhattan, and what else do they manage?`

### What the agent doesn't have

- **Specific apartments**: all data is at the building level, so it can't tell you about a particular unit.
- **Full complaint details**: it sees complaint counts by type, not the text of each complaint.
- **Anything outside these datasets**: rent prices, amenities, neighborhood safety, or buildings you haven't asked about. It will say so rather than guess.

## Setup

1. A GCP project with billing and the Agent Platform API (formerly Vertex AI) enabled.
2. `gcloud auth application-default login`
3. `uv run app.py`, then open http://localhost:8000.

## About the data

All figures come from NYC Open Data and the NYC geocoder (GeoSearch).

- **HPD violations**: issued after an inspection (often triggered by a 311 complaint) when a rental building breaks the Housing Maintenance Code. Classes: A non-hazardous (90 days to fix), B hazardous (30 days), C immediately hazardous (24 hours), I informational/administrative. Counts cover all time. No violations can mean a clean building or a non-residential one HPD doesn't cover.
- **311 complaints**: what residents reported, not verified findings. Zero complaints doesn't prove there are no problems.
- **Landlord portfolio**: built from HPD registrations. Names are matched after ignoring case, punctuation and suffix spelling, so counts are a minimum. A managing agent can serve many unrelated owners, so agent totals aren't one landlord's record. Individual owners are never named or searched.

