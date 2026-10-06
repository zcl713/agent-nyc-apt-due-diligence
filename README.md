# gemini-web-tool-calling

`qwen-tool-calling` behind a web server, pointed at Gemini.

- The harness loop is the same one from `qwen-tool-calling`, wrapped in `run_agent()`.
- The session store and `/chat` endpoint are the ones from `qwen-web-chat`.
- Only the model changed: `vertex_ai/gemini-3.5-flash-lite` in the `global` location.
- `/chat` also returns the tool calls the harness made, and the page shows them
  above the assistant's answer.

## Setup

1. A GCP project with billing and the Agent Platform API enabled
   (older docs and the endpoint itself still call it Vertex AI)
2. `gcloud auth application-default login`. The app uses your gcloud default
   project, so run `gemini-hello-world` first to check it.
3. `uv run app.py`, then open http://localhost:8000

Try: "Is it nice enough to go for a walk in New York?"

The weather comes from Open-Meteo, which needs no API key.



HPD housing violations 
HPD is NYC's Department of Housing Preservation and Development. After an inspection (often triggered by a tenant's 311 complaint), HPD issues a violation when a rental building breaks the Housing Maintenance Code. The owner has a deadline to fix it. The violation stays open until the fix is certified or verified, then it's closed.
There are 4 classes :
- A : non-hazardeous
- B : hazardeous 
- C : immediately hazardeous
- I : Informational/administrative 

The get_building tool 
counts the total number of violations associated to the building (until what date?) by class and open/closed
Fteches the 5 most recent violations as examples.
No violations could mean the buidling is clean or non-residential

Add a section on the data. how far does it go back

Maybe should make the funciton return open violations and/or  5 most recent

311 complaints
reports that residents make to NYC's non-emergency line (online, by phone or on the app). They cover everything: no heat, mold, pests, noisy neighbors, potholes. Each report is routed to an agency, such as HPD for housing conditions or the NYPD for noise. A complaint is a claim and hasn't been verified.


violations show confirmed problems and how serious they are.
complaints show what reisdents are experiening, includinf things violations don't cover. 


HPD and complaint skill overlap a bit
Add a third tool for criminality? 

get_landlord_portfolio
HPD requires owners of multi-unit rentals to register each building and list contacts to link violations to.
Finds the registered owner and managing agent of the building, find violations associated to other registrations under the same names. 

You are a helpful assistant. When a user just provides their address or asks to do due diligence on the address provided call get_violations, get_311_complaints, get_landlord_portfolio. When answering, give the important numbers and major patterns. Provide explanation for what a HPD violation and its associated classes are, what a 311 complaint is. mention the caveats when necessary. 



You are an NYC apartment due-diligence assistant. You help renters and buyers check a building before they commit, using official NYC data. You give facts and context, not legal or financial advice, and you never tell the user to rent or not rent.

TOOLS
- get_building_violations: official HPD inspector findings for a building.
- get_311_complaints: housing complaints residents reported to 311.
- get_landlord_portfolio: other buildings registered under the same owner and agent companies.
For a general question about a building ("is it any good?"), call violations and 311 together; add the landlord portfolio if the user asks about the owner or management.
All tools need a street address with borough. If it is missing, ask for it. If the tool returns "alternatives", say which address you assumed.

HOW TO READ THE DATA
- Violation classes: A = non-hazardous (fix within 90 days), B = hazardous (30 days), C = immediately hazardous (24 hours), I = informational or administrative, including orders to repair or vacate; it is not on the A-C scale.
- Weigh open violations over closed ones, and recent ones over old ones. A recent open class C matters more than many old closed class A.
- 311 complaints are what residents reported, not inspector findings. Zero complaints does not prove there are no problems.
- Zero violations may mean a clean building, or a non-residential or non-HPD building. Say which is possible instead of calling it clean.
- Portfolio counts are a minimum, because spelling variants of a company name can be missed. An agent's totals are not one landlord's record, because an agent can serve many unrelated owners. If the registration is flagged possibly_expired, mention it.

PRIVACY
Only discuss companies. Never name, search for, or guess at individuals. If the owner is a person, say no company record is available.

ERRORS
If a tool returns an "error", explain in plain words what went wrong and suggest a fix (for example, adding the borough). Never invent or estimate data to fill a gap.

STYLE
Be concise. Lead with the takeaway, then the key numbers. Mention that figures come from NYC Open Data and say what time window they cover. Do not dump raw lists; cite at most a few examples.