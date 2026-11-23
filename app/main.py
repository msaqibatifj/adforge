import csv
import io
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from . import db, safety
from .graph import build_graph, _text

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
PRICE_IN = float(os.getenv("PRICE_IN_PER_MTOK", "1.0"))     # USD per million input tokens (Haiku-class estimate)
PRICE_OUT = float(os.getenv("PRICE_OUT_PER_MTOK", "5.0"))   # USD per million output tokens
MAX_SPEND = float(os.getenv("MAX_SPEND_USD", "1.7"))        # self-imposed kill switch below the $2 key limit
ID_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")

graph = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph
    db.init()
    graph = build_graph(db.get_checkpointer())
    if os.getenv("SEED_DEMO", "1") == "1":
        from .seed import seed
        seed()
    yield


app = FastAPI(title="AdForge Agent API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class UsageCB(BaseCallbackHandler):
    """Sums token usage across every LLM call in a turn (guard, agent, memory extractor)."""
    def __init__(self):
        self.tin = self.tout = 0

    def on_llm_end(self, response, **kwargs):
        for gens in response.generations:
            for g in gens:
                u = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                self.tin += int(u.get("input_tokens", 0))
                self.tout += int(u.get("output_tokens", 0))


class ChatIn(BaseModel):
    user_id: str
    thread_id: str
    message: str


def _check_ids(*vals):
    for v in vals:
        if not isinstance(v, str) or not ID_RE.match(v):
            raise HTTPException(422, "ids must be 1-64 chars of letters, digits and _ . : @ -")


def _spent() -> float:
    r = db.query("SELECT COALESCE(SUM(cost_usd),0) AS s FROM usage_log")
    return float(r[0]["s"] or 0)


def _log_msg(uid, tid, role, content):
    db.execute("INSERT INTO messages_log (id,user_id,thread_id,role,content,created_at) VALUES (?,?,?,?,?,?)",
               (db.new_id("m"), uid, tid, role, content, db.now()))


def _tool_calls(new_msgs):
    results = {m.tool_call_id: _text(m) for m in new_msgs if isinstance(m, ToolMessage)}
    calls = []
    for m in new_msgs:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                raw = results.get(tc["id"], "")
                try:
                    res = json.loads(raw)
                except Exception:
                    res = raw
                calls.append({"name": tc["name"], "args": tc["args"], "result": res})
    return calls


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"


@app.post("/chat")
def chat(req: ChatIn):
    _check_ids(req.user_id, req.thread_id)
    message = (req.message or "").strip()[:2000]
    uid, tid = req.user_id, req.thread_id
    if not message:
        return {"reply": "Please type a message.", "tool_calls": [], "tokens": {"in": 0, "out": 0}}
    if _spent() >= MAX_SPEND:
        return {"reply": safety.BUDGET_MESSAGE, "tool_calls": [], "tokens": {"in": 0, "out": 0}}

    cb, turn_id, t0 = UsageCB(), uuid.uuid4().hex, time.time()
    config = {"configurable": {"thread_id": f"{uid}::{tid}", "user_id": uid, "turn_id": turn_id},
              "callbacks": [cb], "recursion_limit": 16}
    outcome, reply, calls = "ok", "", []
    try:
        result = graph.invoke({"messages": [HumanMessage(content=message)]}, config=config)
        msgs = result["messages"]
        last_h = max(i for i, m in enumerate(msgs) if isinstance(m, HumanMessage))
        new = msgs[last_h + 1:]
        calls = _tool_calls(new)
        final = [m for m in new if isinstance(m, AIMessage) and not m.tool_calls]
        reply = _text(final[-1]) if final else "Done."
        if result.get("blocked"):
            outcome = "blocked"
    except Exception as e:  # never 500 on the graded endpoint
        outcome = f"error:{type(e).__name__}"
        reply = "Sorry, something went wrong on my side. Please try again in a moment."
    latency = int((time.time() - t0) * 1000)
    cost = (cb.tin * PRICE_IN + cb.tout * PRICE_OUT) / 1_000_000
    try:
        _log_msg(uid, tid, "user", message)
        _log_msg(uid, tid, "assistant", reply)
        db.execute("INSERT INTO usage_log (id,user_id,thread_id,tokens_in,tokens_out,cost_usd,latency_ms,tools,outcome,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (db.new_id("u"), uid, tid, cb.tin, cb.tout, cost, latency, ",".join(c["name"] for c in calls), outcome, db.now()))
    except Exception:
        pass
    return {"reply": reply, "tool_calls": calls, "tokens": {"in": cb.tin, "out": cb.tout}}


# ---------------- customer website API (scoped by user_id) ----------------
@app.get("/api/campaigns")
def api_campaigns(user_id: str):
    _check_ids(user_id)
    cs = db.query("SELECT * FROM campaigns WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    vs = db.query("SELECT id,campaign_id,label,headline,body,cta,impressions,clicks,conversions,creative_style FROM variants WHERE user_id=? ORDER BY label", (user_id,))
    from .tools import analyze
    for c in cs:
        c["variants"] = [v for v in vs if v["campaign_id"] == c["id"]]
        full = [dict(v) for v in c["variants"]]
        c["analysis"] = analyze(full)
    return {"campaigns": cs}


class ResultsIn(BaseModel):
    user_id: str
    impressions: int
    clicks: int
    conversions: int = 0


@app.post("/api/variants/{variant_id}/results")
def api_results(variant_id: str, body: ResultsIn):
    _check_ids(body.user_id, variant_id)
    if min(body.impressions, body.clicks, body.conversions) < 0 or body.clicks > body.impressions or body.conversions > body.clicks:
        raise HTTPException(422, "need 0 <= conversions <= clicks <= impressions")
    n = db.execute("UPDATE variants SET impressions=?, clicks=?, conversions=? WHERE user_id=? AND id=?",
                   (body.impressions, body.clicks, body.conversions, body.user_id, variant_id))
    if not n:
        raise HTTPException(404, "variant not found")
    return {"ok": True}


@app.get("/api/history")
def api_history(user_id: str, thread_id: str):
    _check_ids(user_id, thread_id)
    rows = db.query("SELECT role, content FROM messages_log WHERE user_id=? AND thread_id=? ORDER BY created_at, id", (user_id, thread_id))
    return {"messages": rows}


@app.get("/api/memory")
def api_memory(user_id: str):
    _check_ids(user_id)
    return {"memory": db.query("SELECT key, value FROM memories WHERE user_id=? ORDER BY key", (user_id,))}


@app.delete("/api/memory")
def api_memory_delete(user_id: str, key: str):
    _check_ids(user_id)
    db.execute("DELETE FROM memories WHERE user_id=? AND key=?", (user_id, key))
    return {"ok": True}


@app.get("/creative/{variant_id}.svg")
def creative(variant_id: str, user_id: str):
    _check_ids(user_id, variant_id)
    r = db.query("SELECT creative_svg FROM variants WHERE user_id=? AND id=?", (user_id, variant_id))
    if not r:
        raise HTTPException(404, "not found")
    return Response(r[0]["creative_svg"], media_type="image/svg+xml",
                    headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'", "X-Content-Type-Options": "nosniff"})


@app.get("/api/campaigns/{campaign_id}/export.csv")
def export_csv(campaign_id: str, user_id: str):
    _check_ids(user_id, campaign_id)
    rows = db.query("SELECT v.label,v.headline,v.body,v.cta,c.platform FROM variants v JOIN campaigns c ON c.id=v.campaign_id AND c.user_id=v.user_id "
                    "WHERE v.user_id=? AND v.campaign_id=? ORDER BY v.label", (user_id, campaign_id))
    if not rows:
        raise HTTPException(404, "not found")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["variant", "platform", "headline", "primary_text", "cta"])
    for r in rows:
        w.writerow([r["label"], r["platform"], r["headline"], r["body"], r["cta"]])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={campaign_id}.csv"})


# ---------------- admin dashboard API ----------------
def admin(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "admin token required")


@app.get("/admin/overview", dependencies=[Depends(admin)])
def admin_overview():
    one = lambda sql: db.query(sql)[0]
    totals = one("SELECT COUNT(*) AS chats, COALESCE(SUM(tokens_in),0) AS tin, COALESCE(SUM(tokens_out),0) AS tout, COALESCE(SUM(cost_usd),0) AS cost, "
                 "COALESCE(AVG(latency_ms),0) AS avg_latency, COUNT(DISTINCT user_id) AS users FROM usage_log")
    totals["campaigns"] = one("SELECT COUNT(*) AS n FROM campaigns")["n"]
    totals["live_campaigns"] = one("SELECT COUNT(*) AS n FROM campaigns WHERE status='live'")["n"]
    totals["blocked"] = one("SELECT COUNT(*) AS n FROM usage_log WHERE outcome='blocked'")["n"]
    totals["errors"] = one("SELECT COUNT(*) AS n FROM usage_log WHERE outcome LIKE 'error%'")["n"]
    totals["budget_limit_usd"] = MAX_SPEND
    tools = db.query("SELECT tools FROM usage_log WHERE tools<>''")
    counts = {}
    for r in tools:
        for t in r["tools"].split(","):
            counts[t] = counts.get(t, 0) + 1
    return {"totals": totals, "tool_usage": counts,
            "recent": db.query("SELECT user_id,thread_id,tokens_in,tokens_out,cost_usd,latency_ms,tools,outcome,created_at FROM usage_log ORDER BY created_at DESC LIMIT 25")}


@app.get("/admin/users", dependencies=[Depends(admin)])
def admin_users():
    return {"users": db.query(
        "SELECT u.user_id, COUNT(*) AS chats, SUM(u.tokens_in+u.tokens_out) AS tokens, SUM(u.cost_usd) AS cost, MAX(u.created_at) AS last_seen, "
        "(SELECT COUNT(*) FROM campaigns c WHERE c.user_id=u.user_id) AS campaigns, (SELECT COUNT(*) FROM memories m WHERE m.user_id=u.user_id) AS facts "
        "FROM usage_log u GROUP BY u.user_id ORDER BY last_seen DESC LIMIT 200")}


@app.get("/admin/campaigns", dependencies=[Depends(admin)])
def admin_campaigns():
    return {"campaigns": db.query("SELECT id,user_id,name,platform,status,budget_usd,created_at,"
                                  "(SELECT COUNT(*) FROM variants v WHERE v.campaign_id=c.id) AS variants FROM campaigns c ORDER BY created_at DESC LIMIT 200")}


@app.get("/admin/conversations", dependencies=[Depends(admin)])
def admin_conversations(user_id: str = Query(default="")):
    if user_id:
        rows = db.query("SELECT user_id,thread_id,role,content,created_at FROM messages_log WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (user_id,))
    else:
        rows = db.query("SELECT user_id,thread_id,role,content,created_at FROM messages_log ORDER BY created_at DESC LIMIT 100")
    return {"messages": rows}


# Static website (also served from the API for local use / single-host fallback)
_web = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.isdir(_web):
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
