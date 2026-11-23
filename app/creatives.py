"""Deterministic SVG ad-creative renderer (1200x628 link-ad format). No LLM cost."""
from xml.sax.saxutils import escape

STYLES = {
    "bold":    {"bg": ("#4f46e5", "#ec4899"), "fg": "#ffffff", "btn": "#ffffff", "btn_fg": "#4f46e5"},
    "minimal": {"bg": ("#ffffff", "#f1f5f9"), "fg": "#0f172a", "btn": "#0f172a", "btn_fg": "#ffffff"},
    "dark":    {"bg": ("#0f172a", "#1e293b"), "fg": "#f8fafc", "btn": "#22d3ee", "btn_fg": "#0f172a"},
    "sunset":  {"bg": ("#f97316", "#facc15"), "fg": "#1c1917", "btn": "#1c1917", "btn_fg": "#fef3c7"},
}


def _wrap(text: str, width: int, max_lines: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(0, width - 1)].rstrip() + "…"
    return lines


def render(headline: str, body: str, cta: str, label: str, style: str = "bold") -> str:
    s = STYLES.get(style, STYLES["bold"])
    hsize = 76 if len(headline) <= 28 else 62 if len(headline) <= 45 else 50
    hlines = _wrap(headline, int(1000 / (hsize * 0.52)), 3)
    blines = _wrap(body, 62, 3)
    y = 190
    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 628" width="1200" height="628">',
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{s["bg"][0]}"/>'
        f'<stop offset="1" stop-color="{s["bg"][1]}"/></linearGradient></defs>',
        '<rect width="1200" height="628" fill="url(#g)"/>',
        f'<circle cx="1080" cy="80" r="120" fill="{s["fg"]}" opacity="0.08"/>',
        f'<text x="60" y="70" font-family="Arial,sans-serif" font-size="22" fill="{s["fg"]}" opacity="0.7">VARIANT {escape(label)}</text>',
    ]
    for line in hlines:
        out.append(f'<text x="60" y="{y}" font-family="Arial,Helvetica,sans-serif" font-weight="800" font-size="{hsize}" fill="{s["fg"]}">{escape(line)}</text>')
        y += int(hsize * 1.15)
    y += 20
    for line in blines:
        out.append(f'<text x="60" y="{y}" font-family="Arial,sans-serif" font-size="30" fill="{s["fg"]}" opacity="0.92">{escape(line)}</text>')
        y += 42
    if cta:
        w = max(220, 26 * len(cta) + 70)
        out.append(f'<rect x="60" y="520" rx="32" width="{w}" height="64" fill="{s["btn"]}"/>')
        out.append(f'<text x="{60 + w // 2}" y="562" text-anchor="middle" font-family="Arial,sans-serif" font-weight="700" font-size="28" fill="{s["btn_fg"]}">{escape(cta)}</text>')
    out.append("</svg>")
    return "".join(out)
