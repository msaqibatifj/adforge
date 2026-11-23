"""Idempotent demo data: two separate demo users so isolation can be demonstrated."""
from . import creatives, db

DEMO = {
    "demo_user_1": {
        "memory": {"brand": "StrideCo", "audience": "weekend runners aged 25-40", "tone": "energetic"},
        "campaign": ("c_demo0001", "StrideCo Summer Run", "StrideCo lightweight running shoes", "meta", "weekend runners aged 25-40", "energetic", 500, "live"),
        "variants": [("A", "Run Further, Feel Lighter", "StrideCo shoes weigh 20% less. Your best mile is waiting.", "Shop Now", 5200, 156, 12),
                     ("B", "Your Pace. Your Rules.", "Cushioning that adapts to every stride. Free returns for 60 days.", "Learn More", 5100, 224, 21),
                     ("C", "Summer Sale: 30% Off", "The shoe runners love, now 30% off through Sunday.", "Get Offer", 5000, 140, 9)],
    },
    "demo_user_2": {
        "memory": {"brand": "Bloom & Co", "audience": "gift buyers looking for flowers", "tone": "warm"},
        "campaign": ("c_demo0002", "Bloom Mother's Day", "Bloom & Co same-day flower delivery", "google", "gift buyers", "warm", 300, "draft"),
        "variants": [("A", "Same-Day Flower Delivery", "Fresh bouquets delivered in hours. Order by 2pm.", "Order Now", 0, 0, 0),
                     ("B", "Make Her Day Bloom", "Hand-tied bouquets, delivered fresh. Order by 2pm today.", "Send Flowers", 0, 0, 0)],
    },
}


def seed():
    for uid, d in DEMO.items():
        if db.query("SELECT 1 AS x FROM campaigns WHERE id=?", (d["campaign"][0],)):
            continue
        for k, v in d["memory"].items():
            db.execute("INSERT INTO memories (user_id,key,value,updated_at) VALUES (?,?,?,?) ON CONFLICT (user_id,key) DO NOTHING", (uid, k, v, db.now()))
        cid, name, product, platform, aud, tone, budget, status = d["campaign"]
        db.execute("INSERT INTO campaigns (id,user_id,name,product,platform,audience,tone,budget_usd,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (cid, uid, name, product, platform, aud, tone, budget, status, db.now()))
        for label, h, b, cta, imp, clk, conv in d["variants"]:
            db.execute("INSERT INTO variants (id,user_id,campaign_id,label,headline,body,cta,impressions,clicks,conversions,creative_style,creative_svg,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"v_{cid[-4:]}{label.lower()}", uid, cid, label, h, b, cta, imp, clk, conv, "bold", creatives.render(h, b, cta, label), db.now()))
