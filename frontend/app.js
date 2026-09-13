const state = { requests: [], filtered: [], activeId: null };

const el = (id) => document.getElementById(id);

async function loadRequests() {
  const res = await fetch("/api/requests");
  state.requests = await res.json();
  state.filtered = state.requests;
  renderList();
}

function renderList() {
  const list = el("request-list");
  list.innerHTML = "";
  for (const r of state.filtered) {
    const item = document.createElement("div");
    item.className = "list-item" + (r.request_id === state.activeId ? " active" : "");
    item.innerHTML = `
      <div class="row1"><span>${r.request_id}</span><span>${r.requested_amount}</span></div>
      <div class="row2">${r.request_type} · ${r.user_id}</div>
    `;
    item.addEventListener("click", () => selectRequest(r.request_id));
    list.appendChild(item);
  }
}

el("search").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  state.filtered = !q
    ? state.requests
    : state.requests.filter(
        (r) =>
          r.request_id.toLowerCase().includes(q) ||
          r.user_id.toLowerCase().includes(q) ||
          r.request_type.toLowerCase().includes(q)
      );
  renderList();
});

async function selectRequest(requestId) {
  state.activeId = requestId;
  renderList();
  const res = await fetch(`/api/decision/${requestId}`);
  if (!res.ok) return;
  const data = await res.json();
  renderDecision(data);
}

function fmtMoney(currency, amount) {
  const n = Number(amount);
  return `${currency} ${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function statusLabel(s) {
  return s.replace(/_/g, " ");
}

function renderDecision(data) {
  el("empty-state").hidden = true;
  el("content").hidden = false;

  const { request, profile, decision, forecast } = data;
  const currency = profile.home_currency;

  el("req-type").textContent = request.request_type.replace(/_/g, " ");
  el("req-id").textContent = request.request_id;
  el("req-text").textContent = request.request_text;
  el("req-amount").textContent = fmtMoney(currency, request.requested_amount);
  el("req-date").textContent = request.request_date;
  el("req-deadline").textContent = request.desired_completion_date;

  const statusEl = el("status-value");
  statusEl.textContent = statusLabel(decision.affordability_status);
  statusEl.dataset.status = decision.affordability_status;

  el("method-value").textContent = statusLabel(decision.recommended_payment_method);
  el("safe-value").textContent = fmtMoney(currency, decision.amount_safe_to_pay);
  el("earliest-value").textContent = decision.earliest_date_for_full_payment || "—";

  renderPlan(decision.payment_plan, currency);
  renderChanges(decision.spending_changes_needed);
  el("explanation").textContent = decision.decision_explanation;

  renderChart(forecast, profile.minimum_balance_to_keep, profile.current_available_balance, currency, request.request_date);
}

function renderPlan(planStr, currency) {
  const table = el("plan-table");
  table.innerHTML = "";
  if (planStr === "none") {
    table.innerHTML = `<tr><td>No payment recommended.</td></tr>`;
    return;
  }
  const rows = planStr.split("|").map((p) => p.split(":"));
  const thead = document.createElement("tr");
  thead.innerHTML = `<th>Date</th><th>Amount</th>`;
  table.appendChild(thead);
  for (const [date, amount] of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${date}</td><td>${fmtMoney(currency, amount)}</td>`;
    table.appendChild(tr);
  }
}

function renderChanges(changesStr) {
  const container = el("changes-list");
  container.innerHTML = "";
  if (changesStr === "none") {
    const tag = document.createElement("span");
    tag.className = "tag none";
    tag.textContent = "No spending changes needed";
    container.appendChild(tag);
    return;
  }
  for (const c of changesStr.split("|")) {
    const tag = document.createElement("span");
    if (c.startsWith("stop:")) {
      tag.className = "tag stop";
      tag.textContent = `Stop ${c.split(":")[1]}`;
    } else if (c.startsWith("reduce_to:")) {
      const [, eid, amt] = c.split(":");
      tag.className = "tag reduce";
      tag.textContent = `Reduce ${eid} → ${amt}`;
    } else {
      tag.className = "tag";
      tag.textContent = c;
    }
    container.appendChild(tag);
  }
}

function renderChart(series, minBalance, startBalance, currency, requestDate) {
  const w = 620, h = 260, padL = 64, padR = 16, padT = 16, padB = 28;
  const values = series.map((p) => p.balance).concat([minBalance, startBalance]);
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const span = maxV - minV || 1;
  const yFor = (v) => padT + (h - padT - padB) * (1 - (v - minV) / span);
  const xFor = (i) => padL + ((w - padL - padR) * i) / (series.length - 1);

  const linePoints = series.map((p, i) => `${xFor(i)},${yFor(p.balance)}`).join(" ");
  const minY = yFor(minBalance);

  const yTicks = 4;
  let gridLines = "";
  let yLabels = "";
  for (let i = 0; i <= yTicks; i++) {
    const v = minV + (span * i) / yTicks;
    const y = yFor(v);
    gridLines += `<line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" stroke="#e3e6e9" stroke-width="1" />`;
    yLabels += `<text x="${padL - 8}" y="${y + 4}" text-anchor="end" font-size="10.5" fill="#5b6673">${Math.round(v).toLocaleString()}</text>`;
  }

  const xTickIdxs = [0, Math.floor((series.length - 1) / 3), Math.floor((2 * (series.length - 1)) / 3), series.length - 1];
  let xLabels = "";
  for (const i of xTickIdxs) {
    xLabels += `<text x="${xFor(i)}" y="${h - 6}" text-anchor="middle" font-size="10.5" fill="#5b6673">${series[i].date.slice(5)}</text>`;
  }

  el("chart").innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg">
      ${gridLines}
      <line x1="${padL}" y1="${minY}" x2="${w - padR}" y2="${minY}" stroke="#a3311c" stroke-width="1.5" stroke-dasharray="4,3" />
      <text x="${w - padR}" y="${minY - 6}" text-anchor="end" font-size="10.5" fill="#a3311c">minimum balance (${currency} ${Math.round(minBalance).toLocaleString()})</text>
      <polyline points="${linePoints}" fill="none" stroke="#157f76" stroke-width="2" />
      ${yLabels}
      ${xLabels}
    </svg>
  `;
}

loadRequests();
