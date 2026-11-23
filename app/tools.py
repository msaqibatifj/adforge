"""AdForge tools. Every tool takes the acting user from the RunnableConfig (never from LLM arguments),
and every SQL statement filters on user_id -> a user can never read or write another user's rows."""
import json
import math
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from . import creatives, db

# platform -> (max headline chars, max body chars)
PLATFORM_LIMITS = {
    "google": (30, 90), "meta": (40, 125), "facebook": (40, 125), "instagram": (40, 125),
    "linkedin": (70, 150), "tiktok": (40, 100), "youtube": (40, 100), "x": (60, 280),
    "twitter": (60, 280), "generic": (60, 150),
}
MAX_VARIANTS_PER_CAMPAIGN = 6


def _uid(config: RunnableConfig) -> str:
    return config["configurable"]["user_id"]


def _turn(config: RunnableConfig) -> str:
    return config["configurable"].get("turn_id", "")


def _limits(platform: str) -> tuple[int, int]:
    p = (platform or "generic").lower()
    for key, val in PLATFORM_LIMITS.items():
        if key in p:
            return val
    return PLATFORM_LIMITS["generic"]


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _campaign(uid: str, ref: str):
    """Resolve a campaign by id, name, or 'latest' -- always scoped to the user."""
    ref = (ref or "").strip()
    if ref.lower() in ("", "latest", "last", "current", "recent"):
        rows = db.query("SELECT * FROM campaigns WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT 1", (uid,))
    else:
        rows = db.query("SELECT * FROM campaigns WHERE user_id=? AND id=?", (uid, ref))
        if not rows:
            rows = db.query("SELECT * FROM campaigns WHERE user_id=? AND lower(name)=lower(?) ORDER BY created_at DESC LIMIT 1", (uid, ref))
    return rows[0] if rows else None


def _variants(uid: str, campaign_id: str) -> list[dict]:
    return db.query("SELECT * FROM variants WHERE user_id=? AND campaign_id=? ORDER BY label", (uid, campaign_id))


def _variant(uid: str, variant_id: str, campaign_id: str, label: str):
    if variant_id:
        rows = db.query("SELECT * FROM variants WHERE user_id=? AND id=?", (uid, variant_id))
        return rows[0] if rows else None
    c = _campaign(uid, campaign_id)
    if not c or not label:
        return None
    rows = db.query("SELECT * FROM variants WHERE user_id=? AND campaign_id=? AND upper(label)=upper(?)", (uid, c["id"], label.strip()))
    return rows[0] if rows else None


class VariantIn(BaseModel):
    headline: str = Field(description="Short attention-grabbing headline")
    body: str = Field(description="Primary ad text")
    cta: str = Field(default="Learn More", description="Call-to-action button text, max 20 chars")


@tool
def create_campaign(name: str, product: str, platform: str = "meta", audience: str = "",
                    budget_usd: float = 0, tone: str = "", config: RunnableConfig = None) -> str:
    """Create a new ad campaign for the current user. platform: meta, google, instagram, linkedin, tiktok, x, youtube.
    Returns campaign_id and the character limits that ad variants must respect."""
    uid = _uid(config)
    name, product = (name or "").strip()[:80], (product or "").strip()[:200]
    if not name or not product:
        return _j({"error": "name and product are required"})
    cid = db.new_id("c")
    db.execute(
        "INSERT INTO campaigns (id,user_id,name,product,platform,audience,tone,budget_usd,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cid, uid, name, product, (platform or "meta").lower()[:20], (audience or "")[:200], (tone or "")[:60],
         max(0.0, float(budget_usd or 0)), "draft", db.now()),
    )
    h, b = _limits(platform)
    return _j({"campaign_id": cid, "status": "draft", "limits": {"headline_max": h, "body_max": b, "cta_max": 20}})


@tool
def save_ad_variants(campaign_id: str, variants: list[VariantIn], config: RunnableConfig = None) -> str:
    """Save 1-5 A/B ad variants (copy) to a campaign. Labels A, B, C... are assigned automatically and an SVG
    creative is rendered for each. Rejects copy exceeding the platform's character limits so you can shorten it."""
    uid = _uid(config)
    c = _campaign(uid, campaign_id)
    if not c:
        return _j({"error": "campaign not found"})
    hmax, bmax = _limits(c["platform"])
    existing = _variants(uid, c["id"])
    if not variants or len(variants) > 5 or len(existing) + len(variants) > MAX_VARIANTS_PER_CAMPAIGN:
        return _j({"error": f"provide 1-5 variants; campaign may hold at most {MAX_VARIANTS_PER_CAMPAIGN}"})
    problems = []
    for i, v in enumerate(variants):
        v = v if isinstance(v, VariantIn) else VariantIn(**v)
        if len(v.headline) > hmax:
            problems.append(f"variant {i + 1} headline is {len(v.headline)} chars (max {hmax})")
        if len(v.body) > bmax:
            problems.append(f"variant {i + 1} body is {len(v.body)} chars (max {bmax})")
        if len(v.cta) > 20:
            problems.append(f"variant {i + 1} cta is {len(v.cta)} chars (max 20)")
    if problems:
        return _j({"error": "copy exceeds platform limits; shorten and retry", "problems": problems})
    saved = []
    for i, v in enumerate(variants):
        v = v if isinstance(v, VariantIn) else VariantIn(**v)
        label = chr(ord("A") + len(existing) + i)
        vid = db.new_id("v")
        svg = creatives.render(v.headline, v.body, v.cta, label, "bold")
        db.execute(
            "INSERT INTO variants (id,user_id,campaign_id,label,headline,body,cta,creative_style,creative_svg,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (vid, uid, c["id"], label, v.headline, v.body, v.cta, "bold", svg, db.now()),
        )
        saved.append({"variant_id": vid, "label": label, "headline": v.headline, "body": v.body, "cta": v.cta})
    return _j({"campaign_id": c["id"], "saved": saved})


@tool
def generate_creative(variant_id: str = "", campaign_id: str = "latest", label: str = "",
                      style: str = "bold", config: RunnableConfig = None) -> str:
    """(Re)render the visual ad creative for a variant. Identify it by variant_id, or by campaign_id + label (A/B/C).
    style: bold, minimal, dark, sunset."""
    uid = _uid(config)
    v = _variant(uid, variant_id, campaign_id, label)
    if not v:
        return _j({"error": "variant not found"})
    style = style if style in creatives.STYLES else "bold"
    svg = creatives.render(v["headline"], v["body"], v["cta"], v["label"], style)
    db.execute("UPDATE variants SET creative_style=?, creative_svg=? WHERE user_id=? AND id=?", (style, svg, uid, v["id"]))
    return _j({"variant_id": v["id"], "label": v["label"], "style": style, "creative_url": f"/creative/{v['id']}.svg"})


@tool
def record_results(impressions: int, clicks: int, conversions: int = 0, variant_id: str = "",
                   campaign_id: str = "latest", label: str = "", config: RunnableConfig = None) -> str:
    """Record (overwrite) the performance totals of one variant. Identify it by variant_id or by campaign_id + label."""
    uid = _uid(config)
    v = _variant(uid, variant_id, campaign_id, label)
    if not v:
        return _j({"error": "variant not found"})
    try:
        i, c, k = int(impressions), int(clicks), int(conversions or 0)
    except (TypeError, ValueError):
        return _j({"error": "impressions, clicks and conversions must be integers"})
    if min(i, c, k) < 0 or c > i or k > c:
        return _j({"error": "invalid numbers: need 0 <= conversions <= clicks <= impressions"})
    db.execute("UPDATE variants SET impressions=?, clicks=?, conversions=? WHERE user_id=? AND id=?", (i, c, k, uid, v["id"]))
    return _j({"variant_id": v["id"], "label": v["label"], "impressions": i, "clicks": c, "conversions": k,
               "ctr_pct": round(100 * c / i, 2) if i else 0})


def _p_value(x1: int, n1: int, x2: int, n2: int) -> float:
    if n1 == 0 or n2 == 0:
        return 1.0
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (x1 / n1 - x2 / n2) / se
    return math.erfc(abs(z) / math.sqrt(2))


def analyze(variants: list[dict]) -> dict:
    """Two-proportion z-test of the leading variant vs. every other variant."""
    rows = []
    use_cvr = False  # winners are decided on CTR (standard for ad A/B tests); CVR is reported alongside
    for v in variants:
        ctr = v["clicks"] / v["impressions"] if v["impressions"] else 0
        cvr = v["conversions"] / v["clicks"] if v["clicks"] else 0
        rows.append({"label": v["label"], "headline": v["headline"], "impressions": v["impressions"], "clicks": v["clicks"],
                     "conversions": v["conversions"], "ctr_pct": round(ctr * 100, 2), "cvr_pct": round(cvr * 100, 2),
                     "_m": cvr if use_cvr else ctr})
    metric = "conversion_rate" if use_cvr else "ctr"
    if len(rows) < 2:
        return {"metric": metric, "variants": [{k: v for k, v in r.items() if k != "_m"} for r in rows],
                "verdict": "need_more_variants", "recommendation": "Add at least 2 variants to run an A/B test."}
    ranked = sorted(rows, key=lambda r: r["_m"], reverse=True)
    lead = ranked[0]
    if lead["impressions"] == 0 or all(r["impressions"] == 0 for r in rows):
        verdict, rec, conf = "no_data", "No results recorded yet. Record impressions/clicks per variant first.", None
    else:
        def counts(r):  # (successes, trials) for the chosen metric
            return (r["conversions"], r["clicks"]) if use_cvr else (r["clicks"], r["impressions"])
        ps = [(_p_value(*counts(lead), *counts(o)), o["label"]) for o in ranked[1:]]
        worst_p = max(p for p, _ in ps)
        enough = all(counts(r)[1] >= 100 for r in rows)
        conf = round((1 - worst_p) * 100, 1)
        if enough and worst_p < 0.05:
            verdict = "winner"
            rec = f"Variant {lead['label']} is the statistically significant winner ({conf}% confidence). Shift budget to it."
        else:
            verdict = "inconclusive"
            why = "each variant needs at least 100 " + ("clicks" if use_cvr else "impressions") if not enough else f"only {conf}% confidence (need 95%)"
            rec = f"No significant winner yet ({why}). Variant {lead['label']} leads; keep the test running."
    return {"metric": metric, "verdict": verdict, "leader": lead["label"], "confidence_pct": conf,
            "variants": [{k: v for k, v in r.items() if k != "_m"} for r in ranked], "recommendation": rec}


@tool
def analyze_ab_test(campaign_id: str = "latest", config: RunnableConfig = None) -> str:
    """Analyze A/B results of a campaign: CTR/conversion rate per variant, significance test, winner and recommendation."""
    uid = _uid(config)
    c = _campaign(uid, campaign_id)
    if not c:
        return _j({"error": "campaign not found"})
    out = analyze(_variants(uid, c["id"]))
    out["campaign_id"], out["campaign"] = c["id"], c["name"]
    return _j(out)


@tool
def list_campaigns(config: RunnableConfig = None) -> str:
    """List the current user's campaigns (id, name, platform, status, number of variants)."""
    uid = _uid(config)
    rows = db.query(
        "SELECT c.id, c.name, c.platform, c.status, c.budget_usd, (SELECT COUNT(*) FROM variants v WHERE v.campaign_id=c.id AND v.user_id=c.user_id) AS variants "
        "FROM campaigns c WHERE c.user_id=? ORDER BY c.created_at DESC LIMIT 25", (uid,))
    return _j({"campaigns": rows})


@tool
def get_campaign(campaign_id: str = "latest", config: RunnableConfig = None) -> str:
    """Get one campaign with all its variants and current results."""
    uid = _uid(config)
    c = _campaign(uid, campaign_id)
    if not c:
        return _j({"error": "campaign not found"})
    vs = [{k: v[k] for k in ("id", "label", "headline", "body", "cta", "impressions", "clicks", "conversions")} for v in _variants(uid, c["id"])]
    return _j({"campaign": {k: c[k] for k in ("id", "name", "product", "platform", "audience", "tone", "budget_usd", "status")}, "variants": vs})


def _gate(uid: str, c: dict, action: str, config: RunnableConfig, confirmed: bool, summary: str):
    """Human-in-the-loop gate. Execution is allowed only if the user was asked in an EARLIER turn
    (a pending approval from a different turn exists) AND the model passes confirmed=true now."""
    pend = db.query("SELECT * FROM approvals WHERE user_id=? AND campaign_id=? AND action=? AND status='pending' ORDER BY created_at DESC",
                    (uid, c["id"], action))
    earlier = [p for p in pend if p["turn_id"] != _turn(config)]
    if confirmed and earlier:
        db.execute("UPDATE approvals SET status='approved' WHERE user_id=? AND id=?", (uid, earlier[0]["id"]))
        return True, None
    if not pend:
        db.execute("INSERT INTO approvals (id,user_id,campaign_id,action,status,turn_id,created_at) VALUES (?,?,?,?,?,?,?)",
                   (db.new_id("a"), uid, c["id"], action, "pending", _turn(config), db.now()))
    return False, _j({"status": "needs_confirmation", "action": action, "campaign": c["name"], "summary": summary,
                      "instruction": "Do NOT proceed. Ask the user to explicitly confirm; only after they reply yes in a new message call this tool again with confirmed=true."})


@tool
def launch_campaign(campaign_id: str = "latest", confirmed: bool = False, config: RunnableConfig = None) -> str:
    """RISKY: take a campaign live (commits ad spend). Requires explicit user confirmation in a separate, later message;
    call first with confirmed=false to request approval."""
    uid = _uid(config)
    c = _campaign(uid, campaign_id)
    if not c:
        return _j({"error": "campaign not found"})
    n = len(_variants(uid, c["id"]))
    if n == 0:
        return _j({"error": "campaign has no ad variants yet; create variants first"})
    ok, msg = _gate(uid, c, "launch", config, confirmed, f"Launch '{c['name']}' on {c['platform']} with {n} variants and a ${c['budget_usd']:.0f} budget.")
    if not ok:
        return msg
    db.execute("UPDATE campaigns SET status='live' WHERE user_id=? AND id=?", (uid, c["id"]))
    return _j({"status": "live", "campaign_id": c["id"], "note": "Marked live in AdForge. Download the ad export from the dashboard to publish on the ad platform."})


@tool
def delete_campaign(campaign_id: str, confirmed: bool = False, config: RunnableConfig = None) -> str:
    """RISKY: permanently delete a campaign and all its variants/results. Requires explicit user confirmation in a
    separate, later message; call first with confirmed=false to request approval."""
    uid = _uid(config)
    c = _campaign(uid, campaign_id)
    if not c:
        return _j({"error": "campaign not found"})
    n = len(_variants(uid, c["id"]))
    ok, msg = _gate(uid, c, "delete", config, confirmed, f"Permanently delete '{c['name']}' and its {n} variants.")
    if not ok:
        return msg
    db.execute("DELETE FROM variants WHERE user_id=? AND campaign_id=?", (uid, c["id"]))
    db.execute("DELETE FROM campaigns WHERE user_id=? AND id=?", (uid, c["id"]))
    return _j({"status": "deleted", "campaign": c["name"]})


TOOLS = [create_campaign, save_ad_variants, generate_creative, record_results, analyze_ab_test,
         list_campaigns, get_campaign, launch_campaign, delete_campaign]
