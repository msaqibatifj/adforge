const API = (window.ADFORGE_API || "").replace(/\/$/, "");
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const rid = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "").slice(0, 16) : Math.random().toString(36).slice(2, 18));
let userId = localStorage.getItem("adforge_user") || ("web_" + rid());
localStorage.setItem("adforge_user", userId);
let threadId = localStorage.getItem("adforge_thread") || ("t_" + rid());
localStorage.setItem("adforge_thread", threadId);
$("#who").textContent = "account: " + userId.slice(0, 14) + (userId.length > 14 ? "…" : "");

const j = async (path, opts) => { const r = await fetch(API + path, opts); if (!r.ok) throw new Error(r.status); return r.json(); };
const md = t => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");

function add(role, text, calls) {
  const d = document.createElement("div"); d.className = "msg " + (role === "user" ? "user" : "bot"); d.innerHTML = md(text);
  if (calls && calls.length) { const t = document.createElement("div"); t.className = "tools"; t.innerHTML = "tools: " + calls.map(c => `<b>${esc(c.name)}</b>`).join(" → "); d.appendChild(t); }
  $("#log").appendChild(d); $("#log").scrollTop = 1e9; return d;
}

async function loadHistory() {
  $("#log").innerHTML = "";
  try { const h = await j(`/api/history?user_id=${encodeURIComponent(userId)}&thread_id=${encodeURIComponent(threadId)}`); h.messages.forEach(m => add(m.role, m.content)); } catch (e) {}
  if (!$("#log").children.length) add("bot", "Hi! I'm AdForge. Tell me what you sell and where you want to advertise (Meta, Google, LinkedIn, TikTok…) and I'll write A/B ad variants with creatives. Paste your results later and I'll tell you the winner.");
}

const CHIPS = ["Create a Meta campaign for my handmade candles, audience: gift buyers, tone: cozy", "Show my campaigns", "Which variant is winning?", "Launch my latest campaign"];
$("#chips").innerHTML = CHIPS.map(c => `<button class="chip">${esc(c)}</button>`).join("");
$("#chips").onclick = e => { if (e.target.classList.contains("chip")) { $("#m").value = e.target.textContent; $("#f").requestSubmit(); } };

$("#f").onsubmit = async e => {
  e.preventDefault(); const text = $("#m").value.trim(); if (!text) return; $("#m").value = ""; add("user", text);
  const wait = add("bot", "…"); $("#send").disabled = true;
  try {
    const r = await j("/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, thread_id: threadId, message: text }) });
    wait.remove(); add("bot", r.reply, r.tool_calls);
  } catch (err) { wait.remove(); add("bot", "Network error — is the API awake? (free hosts sleep; retry in ~30s)"); }
  $("#send").disabled = false; refresh();
};

function pct(n) { return (n || 0).toFixed(2) + "%"; }
async function loadCampaigns() {
  const el = $("#camps");
  try {
    const { campaigns } = await j("/api/campaigns?user_id=" + encodeURIComponent(userId));
    if (!campaigns.length) { el.innerHTML = '<div class="empty">No campaigns yet. Ask the agent to create one →</div>'; return; }
    el.innerHTML = campaigns.map(c => {
      const a = c.analysis || {}, rows = Object.fromEntries((a.variants || []).map(v => [v.label, v]));
      const maxCtr = Math.max(0.0001, ...(a.variants || []).map(v => v.ctr_pct || 0));
      return `<div class="camp"><header><b>${esc(c.name)}</b><span class="badge ${c.status === "live" ? "live" : ""}">${esc(c.status)}</span><span class="badge">${esc(c.platform)}</span><div class="sp"></div>
        <small>${c.budget_usd ? "$" + c.budget_usd + " budget" : ""}</small><a href="${API}/api/campaigns/${c.id}/export.csv?user_id=${encodeURIComponent(userId)}"><small>export CSV</small></a></header>
        <div class="vars">${c.variants.map(v => { const r = rows[v.label] || {}; return `<div class="var"><img loading="lazy" alt="creative ${esc(v.label)}" src="${API}/creative/${v.id}.svg?user_id=${encodeURIComponent(userId)}">
          <div class="b"><h4>${esc(v.label)} — ${esc(v.headline)} ${a.verdict === "winner" && a.leader === v.label ? '<span class="badge win">winner</span>' : ""}</h4>${esc(v.body)}
          <div class="stats"><span>${v.impressions} impr</span><span>${v.clicks} clicks</span><span>${v.conversions} conv</span><span>CTR ${pct(r.ctr_pct)}</span></div>
          <div class="bar"><i style="width:${Math.round(100 * (r.ctr_pct || 0) / maxCtr)}%"></i></div>
          <div class="res" data-v="${v.id}"><input placeholder="impr" value="${v.impressions || ""}"><input placeholder="clicks" value="${v.clicks || ""}"><input placeholder="conv" value="${v.conversions || ""}"><button class="ghost">Save</button></div></div></div>`; }).join("")}</div>
        <div class="rec">${esc(a.recommendation || "")}</div></div>`;
    }).join("");
  } catch (e) { el.innerHTML = '<div class="empty">Could not reach the API.</div>'; }
}
$("#camps").onclick = async e => {
  if (e.target.tagName !== "BUTTON") return; const row = e.target.closest(".res"); const [i, c, k] = [...row.querySelectorAll("input")].map(x => parseInt(x.value || "0", 10) || 0);
  try { await j(`/api/variants/${row.dataset.v}/results`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, impressions: i, clicks: c, conversions: k }) }); loadCampaigns(); }
  catch (err) { alert("Need conversions ≤ clicks ≤ impressions."); }
};
async function loadMemory() {
  const { memory } = await j("/api/memory?user_id=" + encodeURIComponent(userId)).catch(() => ({ memory: [] }));
  $("#mem").innerHTML = memory.length ? memory.map(m => `<div class="mem"><div><b>${esc(m.key)}</b><br><span>${esc(m.value)}</span></div><button class="ghost" data-k="${esc(m.key)}">Forget</button></div>`).join("")
    : '<div class="empty">Nothing yet. Tell the agent about your brand and audience and it will remember across chats.</div>';
}
$("#mem").onclick = async e => { const k = e.target.dataset && e.target.dataset.k; if (k) { await fetch(`${API}/api/memory?user_id=${encodeURIComponent(userId)}&key=${encodeURIComponent(k)}`, { method: "DELETE" }); loadMemory(); } };
document.querySelectorAll(".tab").forEach(t => t.onclick = () => { document.querySelectorAll(".tab").forEach(x => x.classList.toggle("on", x === t)); $("#camps").style.display = t.dataset.t === "camps" ? "" : "none"; $("#mem").style.display = t.dataset.t === "mem" ? "" : "none"; });
$("#newchat").onclick = () => { threadId = "t_" + rid(); localStorage.setItem("adforge_thread", threadId); loadHistory(); };
$("#switch").onclick = () => { const v = prompt("Enter an account id to switch to (e.g. demo_user_1), or leave empty for a fresh account:", ""); if (v === null) return; userId = v.trim() || ("web_" + rid()); localStorage.setItem("adforge_user", userId); threadId = "t_" + rid(); localStorage.setItem("adforge_thread", threadId); location.reload(); };
function refresh() { loadCampaigns(); loadMemory(); }
loadHistory(); refresh();
