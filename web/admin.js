const API = (window.ADFORGE_API || "").replace(/\/$/, "");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const app = document.getElementById("app");
let token = sessionStorage.getItem("adforge_admin") || "";
const get = async p => { const r = await fetch(API + p, { headers: { "X-Admin-Token": token } }); if (r.status === 401) throw new Error("401"); return r.json(); };
document.getElementById("logout").onclick = () => { sessionStorage.removeItem("adforge_admin"); location.reload(); };

function login(msg) {
  app.innerHTML = `<div class="card" style="max-width:420px;margin:60px auto;padding:22px"><h3 style="border:0;padding:0 0 12px">Admin sign-in</h3><p style="color:var(--mute)">${esc(msg || "Enter the admin token configured on the server (ADMIN_TOKEN).")}</p>
  <form id="lf" style="display:flex;gap:8px"><input id="tk" type="password" placeholder="Admin token" style="flex:1"><button>Sign in</button></form></div>`;
  document.getElementById("lf").onsubmit = e => { e.preventDefault(); token = document.getElementById("tk").value; sessionStorage.setItem("adforge_admin", token); load(); };
}
const kpi = (l, v) => `<div class="kpi"><b>${esc(v)}</b><span>${esc(l)}</span></div>`;
const table = (heads, rows) => `<table><thead><tr>${heads.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="${heads.length}" style="color:var(--mute)">No data yet</td></tr>`}</tbody></table>`;

async function load() {
  if (!token) return login();
  try {
    const [o, u, c, m] = await Promise.all([get("/admin/overview"), get("/admin/users"), get("/admin/campaigns"), get("/admin/conversations")]);
    const t = o.totals, tools = Object.entries(o.tool_usage).sort((a, b) => b[1] - a[1]), max = Math.max(1, ...tools.map(x => x[1]));
    app.innerHTML = `<div class="kpis">${kpi("Chat turns", t.chats)}${kpi("Users", t.users)}${kpi("Campaigns", t.campaigns + " (" + t.live_campaigns + " live)")}${kpi("Tokens in/out", t.tin + " / " + t.tout)}
      ${kpi("Spend", "$" + Number(t.cost).toFixed(4) + " / $" + t.budget_limit_usd)}${kpi("Avg latency", Math.round(t.avg_latency) + " ms")}${kpi("Blocked (safety)", t.blocked)}${kpi("Errors", t.errors)}</div>
      <div class="grid2"><div class="card"><h3>Tool usage</h3><div class="pane">${tools.map(([n, k]) => `<div style="margin-bottom:8px"><div style="display:flex;justify-content:space-between"><span>${esc(n)}</span><span>${k}</span></div><div class="bar"><i style="width:${100 * k / max}%"></i></div></div>`).join("") || '<div class="empty">No tool calls yet</div>'}</div></div>
      <div class="card"><h3>Users</h3><div class="pane">${table(["user", "chats", "tokens", "cost $", "campaigns", "facts"], u.users.map(x => [x.user_id, x.chats, x.tokens, Number(x.cost).toFixed(4), x.campaigns, x.facts]))}</div></div></div>
      <div class="card" style="margin-top:16px"><h3>Campaigns (all users)</h3><div class="pane">${table(["id", "user", "name", "platform", "status", "variants", "budget"], c.campaigns.map(x => [x.id, x.user_id, x.name, x.platform, x.status, x.variants, "$" + x.budget_usd]))}</div></div>
      <div class="card" style="margin-top:16px"><h3>Recent agent calls</h3><div class="pane">${table(["time", "user", "in", "out", "ms", "tools", "outcome"], o.recent.map(x => [x.created_at.replace("T", " ").slice(0, 19), x.user_id, x.tokens_in, x.tokens_out, x.latency_ms, x.tools, x.outcome]))}</div></div>
      <div class="card" style="margin-top:16px"><h3>Recent conversations</h3><div class="pane">${table(["time", "user", "thread", "role", "message"], m.messages.slice(0, 40).map(x => [x.created_at.replace("T", " ").slice(0, 19), x.user_id, x.thread_id, x.role, x.content.slice(0, 140)]))}</div></div>`;
  } catch (e) { sessionStorage.removeItem("adforge_admin"); token = ""; login(e.message === "401" ? "Invalid admin token." : "Could not reach the API."); }
}
load();
