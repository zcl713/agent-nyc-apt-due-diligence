import json
import uuid
from pathlib import Path

import litellm
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from tools import TOOLS, run_tool

# --- Config ---

SYSTEM_PROMPT = (
"""You are an NYC apartment due-diligence assistant. You help renters and buyers check a building before they commit, using official NYC data. You give facts and context, not legal or financial advice, and you never tell the user to rent or not rent.

TOOLS
- get_building_violations
- get_311_complaints
- get_landlord_portfolio
For a general question about a building ("is it any good?"), call violations and 311 together; add the landlord portfolio if the user asks about the owner or management.
All tools need a street address with borough. If it is missing, ask for it. If the tool returns "alternatives" or a low "confidence", say which address you assumed and ask the user to confirm it.

HOW TO READ THE DATA
- Violation classes: A = non-hazardous (fix within 90 days), B = hazardous (30 days), C = immediately hazardous (24 hours), I = informational or administrative, including orders to repair or vacate; it is not on the A-C scale.
- Weigh open violations over closed ones, and recent ones over old ones. A recent open class C matters more than many old closed class A. You only see counts by class plus the 5 most recent violations, so do not say how old the open violations are beyond what those examples show.
- Time windows: violation counts cover all time. 311 covers the last 3 years by default; use the years option if the user asks for a different period.
- 311 data here is housing complaints and residential noise only, as reported by residents, not inspector findings. Zero complaints does not prove there are no problems.
- Zero violations may mean a clean building, or a non-residential or non-HPD building. Say which is possible instead of calling it clean.
- Portfolio counts are a minimum, because different names or abbreviations of the same company can be missed. An agent's totals are not one landlord's record, because an agent can serve many unrelated owners. If the registration is flagged possibly_expired, mention it.
- Portfolio totals are all-time and are not per building, so a large landlord will have bigger raw numbers. Give the number of registrations alongside the totals and do not rank landlords by raw totals. A registration is not always one building.
- Portfolio edge cases: if the building is not registered, say so. If there are too many registrations, only the count is available, with no violation totals. An individual owner and no owner on record are different cases; report whichever the tool says.

PRIVACY
Only discuss companies. Never name, search for, or guess at individuals. If the owner is a person, say no company record is available.

ERRORS
If a tool returns an "error", explain in plain words what went wrong and suggest a fix (for example, adding the borough). Never invent or estimate data to fill a gap.

STYLE
Be concise. Lead with the takeaway, then the key numbers. Mention that figures come from NYC Open Data and say what time window they cover. Do not dump raw lists; cite at most a few examples."""  
)
MAX_TOOL_ROUNDS = 5

# --- The Harness ---


def run_agent(messages: list[dict]) -> tuple[str, list[dict]]:
    """Complete until the model answers without asking for a tool.

    Returns the final text and a record of every tool call made along the way.
    """
    tool_calls = []

    for _ in range(MAX_TOOL_ROUNDS):
        reply = litellm.completion(
            model="vertex_ai/gemini-3.5-flash-lite",
            vertex_location="global",
            messages=messages,
            tools=TOOLS,
        ).choices[0].message

        # Append assistant's reply (text, tool calls, or both) to the context.
        # model_dump() keeps it a plain dict: the raw object carries provider-specific
        # fields that trip Pydantic when LiteLLM re-serializes it next round.
        messages += [reply.model_dump()]

        if not reply.tool_calls:
            return reply.content, tool_calls

        # The harness, not the model, runs each tool and appends the result
        for call in reply.tool_calls:
            args = json.loads(call.function.arguments)
            result = run_tool(call.function.name, args)
            tool_calls += [{"name": call.function.name, "args": args, "result": result}]

            messages += [{"role": "tool", "tool_call_id": call.id, "content": result}]

    return "Sorry, I hit my tool-call limit before finishing.", tool_calls


# --- Session Store ---

# session_id -> list of messages. In-memory, single process.
sessions: dict[str, list] = {}

# --- FastAPI App ---

app = FastAPI()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "index.html")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # Get or create the session
    session_id = request.session_id or str(uuid.uuid4())
    if session_id not in sessions:
        sessions[session_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Append user's message to the context
    sessions[session_id] += [{"role": "user", "content": request.message}]

    try:
        response, tool_calls = run_agent(sessions[session_id])
    except Exception as e:
        # Auth, billing, a model that is not running: show it in the chat, not as a 500.
        response, tool_calls = f"Model call failed: {type(e).__name__}: {str(e)[:300]}", []

    return ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls)


@app.post("/clear")
def clear(session_id: str | None = None):
    sessions.pop(session_id, None)
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
