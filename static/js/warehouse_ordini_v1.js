(function(){
  const $ = (sel) => document.querySelector(sel);

  const fmt = (x) => {
    if (x === null || x === undefined || x === "") return "";
    const n = Number(x);
    if (Number.isNaN(n)) return String(x);
    return n.toLocaleString("it-IT", { maximumFractionDigits: 2 });
  };

  const parseNum = (v) => {
    const raw = String(v ?? "").trim();
    if (!raw) return NaN;

    // Supporta input italiano: 1.234,56
    const hasComma = raw.includes(",");
    let norm = raw;
    if (hasComma) {
      norm = norm.replace(/\./g, "").replace(/,/g, ".");
    } else {
      norm = norm.replace(/,/g, ".");
    }
    norm = norm.replace(/\s+/g, "");
    const n = Number(norm);
    return Number.isFinite(n) ? n : NaN;
  };

  const todayISO = () => {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth()+1).padStart(2,"0");
    const da = String(d.getDate()).padStart(2,"0");
    return `${y}-${m}-${da}`;
  };

  const addDaysISO = (iso, days) => {
    if (!iso) return "";
    const [y,m,d] = iso.split("-").map((x)=>parseInt(x,10));
    if (!y || !m || !d) return "";
    const dt = new Date(Date.UTC(y, m-1, d));
    dt.setUTCDate(dt.getUTCDate() + Number(days||0));
    const yy = dt.getUTCFullYear();
    const mm = String(dt.getUTCMonth()+1).padStart(2,"0");
    const dd = String(dt.getUTCDate()).padStart(2,"0");
    return `${yy}-${mm}-${dd}`;
  };

  const page = document.querySelector(".warehouse-ordini");
  const storeCode = (page && page.dataset && page.dataset.storeCode) ? String(page.dataset.storeCode) : "";

  const elSupplier = $("#ordSupplier");
  const elListino = $("#ordListino");
  const elOrderDay = $("#ordOrderDay");
  const elDate = $("#ordDate");
  const elNext = $("#ordNext");
  const btnRun = $("#ordRun");
  const btnExport = $("#ordExport");
  const elCount = $("#ordCount");
  const elMeta = $("#ordMeta");
  const tblBody = $("#ordBody");
  const elSearch = $("#ordSearchInput");
  const elFilterInfo = $("#ordFilterInfo");
  const toastContainer = $("#ordToastContainer");
  const mobileList = $("#ordMobileList");
  const mobileEmpty = $("#ordMobileEmpty");

  let lastRows = [];
  let lastMeta = null;
  let baseByCode = new Map();
  let rowByCode = new Map();

  function toast(messages, variant){
    if (!toastContainer) return;
    toastContainer.innerHTML = "";

    const list = (messages || []).map(x => String(x || "").trim()).filter(Boolean);
    if (!list.length) return;

    const v = String(variant || "warning").toLowerCase();
    const isError = v === "danger" || v === "error";
    const bg = isError ? "danger" : (v === "success" ? "success" : "warning");
    const title = isError ? "Errore" : (bg === "success" ? "OK" : "Avvisi");

    const toastEl = document.createElement("div");
    toastEl.className = `toast align-items-start text-bg-${bg} border-0`;
    toastEl.setAttribute("role", "alert");
    toastEl.setAttribute("aria-live", "assertive");
    toastEl.setAttribute("aria-atomic", "true");

    toastEl.innerHTML = `
      <div class="d-flex">
        <div class="toast-body">
          <div class="fw-semibold mb-1">${title}</div>
          <ul class="mb-0 ps-3">
            ${list.map(x => `<li>${x.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</li>`).join("")}
          </ul>
        </div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Chiudi"></button>
      </div>
    `;

    toastContainer.appendChild(toastEl);

    try {
      // bootstrap è definito dal bundle incluso in base.html
      const t = bootstrap.Toast.getOrCreateInstance(toastEl, { autohide: false });
      t.show();
    } catch (e) {
      // fallback senza JS bootstrap
      toastEl.classList.add("show");
    }
  }

  function deepCopy(obj){
    try { return JSON.parse(JSON.stringify(obj)); } catch (e) { return obj; }
  }

// Normalizza ordine in colli: colli = ceil(pz / qtacar)
function normalizeOrder(row){
  const r = row || {};
  const qtacar = Number(r.qtacar ?? 0);
  let pz = Number(r.order_pz ?? 0);
  if (!Number.isFinite(pz) || pz < 0) pz = 0;

  if (qtacar > 0) {
    const car = Math.ceil(pz / qtacar);
    const safeCar = (Number.isFinite(car) && car > 0) ? car : 0;
    r.order_car = safeCar;
    r.order_pz = safeCar * qtacar;

    const conv = Number(r.conv_kg_per_pz ?? 0);
    if (conv && Number.isFinite(conv)) {
      r.order_kg = r.order_pz * conv;
    }
  } else {
    const car0 = Number(r.order_car ?? 0);
    r.order_car = (Number.isFinite(car0) && car0 > 0) ? Math.ceil(car0) : 0;
    r.order_pz = pz;

    const conv = Number(r.conv_kg_per_pz ?? 0);
    if (conv && Number.isFinite(conv)) {
      r.order_kg = pz * conv;
    }
  }
  return r;
}


  function overridesKey(){
    const sup = String(elSupplier?.value || "").trim();
    const listino = String(elListino?.value || "").trim();
    const day = String(elOrderDay?.value || todayISO()).trim();
    return `ord_stock_override_v1:${storeCode}:${sup}:${listino}:${day}`;
  }

  function readOverrides(){
    try {
      const raw = localStorage.getItem(overridesKey());
      const obj = raw ? JSON.parse(raw) : {};
      return (obj && typeof obj === "object") ? obj : {};
    } catch (e) {
      return {};
    }
  }

  function writeOverrides(map){
    try {
      localStorage.setItem(overridesKey(), JSON.stringify(map || {}));
    } catch (e) {
      // ignore
    }
  }

  function setOverride(code, stockReal){
    const m = readOverrides();
    m[String(code)] = stockReal;
    writeOverrides(m);
  }

  function clearOverride(code){
    const m = readOverrides();
    delete m[String(code)];
    writeOverrides(m);
  }

  function computeWithRealStock(row, stockReal){
    const r = row;

    const L = Number(lastMeta?.coverage_days ?? 1);
    const z = Number(lastMeta?.z ?? 1.64);

    const invQty = Number(r.inv_qty ?? 0);
    const deliv = Number(r.delivery_pz_since_inv ?? 0);
    const txin = Number(r.txin_pz_since_inv ?? 0);
    const txout = Number(r.txout_pz_since_inv ?? 0);
    const revPast = Number(r.past_revenues_used ?? 0);

    const sigmaRev = Number(r.sigma_revenues_std ?? lastMeta?.sigma_revenues_std ?? 0);
    const forecastRev = Number(r.forecast_revenues_total ?? 0);
    const qtacar = Number(r.qtacar ?? 0);

    const consActual = invQty + deliv + txin - txout - stockReal;

    // Se possibile, ricalcola u/1000 su base inventario oggi
    let u1000 = Number(r.unit_per_1000 ?? 0);
    let consUsed = Number(r.past_consumption_est ?? 0);

    if (revPast > 0 && consActual >= 0) {
      u1000 = (consActual / revPast) * 1000.0;
      consUsed = consActual;
    }

    if (!Number.isFinite(u1000) || u1000 < 0) u1000 = 0;

    const demand = (forecastRev / 1000.0) * u1000;
    const sigmaUnits = (sigmaRev / 1000.0) * u1000;
    const safety = z * sigmaUnits * Math.sqrt(Math.max(L, 1));
    const target = demand + safety;

    const needed = Math.max(0, target - stockReal);
    let orderCar = 0;
    let orderPz = 0;
    if (qtacar > 0) {
      orderCar = Math.ceil(needed / qtacar);
      orderPz = orderCar * qtacar;
    } else {
      orderCar = 0;
      orderPz = needed;
    }

    r._stock_override = true;
    r.stock_real = stockReal;

    // Stock usato nel calcolo
    r.stock_theoretical = stockReal;

    r.unit_per_1000 = u1000;
    r.past_consumption_est = consUsed;
    r.forecast_consumption = demand;
    r.safety_stock = safety;
    r.target_stock = target;
    r.order_car = orderCar;
    r.order_pz = orderPz;

    // KG (se presente conversione)
    const conv = Number(r.conv_kg_per_pz ?? 0);
    if (conv) {
      r.stock_kg = stockReal * conv;
      r.order_kg = orderPz * conv;
    }

    normalizeOrder(r);

    return r;
  }

  function applyOverrideToRow(code, stockReal, persist){
    const c = String(code || "").trim();
    if (!c) return false;

    const base = baseByCode.get(c);
    if (!base) return false;

    const stock = Number(stockReal);
    if (!Number.isFinite(stock) || stock < 0) return false;

    // valida consumo non negativo (se abbiamo i dettagli del periodo)
    const invQty = Number(base.inv_qty ?? 0);
    const deliv = Number(base.delivery_pz_since_inv ?? 0);
    const txin = Number(base.txin_pz_since_inv ?? 0);
    const txout = Number(base.txout_pz_since_inv ?? 0);
    const consActual = invQty + deliv + txin - txout - stock;
    if (consActual < 0) {
      toast([
        `Stock reale non valido: risulterebbe consumo negativo (${fmt(consActual)}).`,
        "Verifica il valore inserito."
      ], "danger");
      return false;
    }

    const next = deepCopy(base);
    computeWithRealStock(next, stock);
    normalizeOrder(next);

    // sostituisci in lastRows
    const idx = lastRows.findIndex(r => String(r.code || "") === c);
    if (idx >= 0) {
      lastRows[idx] = next;
    }
    rowByCode.set(c, next);

    if (persist) setOverride(c, stock);
    return true;
  }

  function clearOverrideForCode(code, persist){
    const c = String(code || "").trim();
    if (!c) return false;

    const base = baseByCode.get(c);
    if (!base) return false;

    const next = deepCopy(base);
    next._stock_override = false;
    delete next.stock_real;
    normalizeOrder(next);

    const idx = lastRows.findIndex(r => String(r.code || "") === c);
    if (idx >= 0) {
      lastRows[idx] = next;
    }
    rowByCode.set(c, next);

    if (persist) clearOverride(c);
    return true;
  }

  function rebuildMaps(){
    rowByCode = new Map();
    lastRows.forEach(r => {
      const c = String(r.code || "");
      if (c) rowByCode.set(c, r);
    });
  }

  function getFilteredRows(){
    const q = String(elSearch?.value || "").trim().toLowerCase();
    if (!q) return { rows: lastRows, q: "" };

    const out = lastRows.filter(r => {
      const code = String(r.code || "");
      const desc = String(r.desc || "");
      return (code + " " + desc).toLowerCase().includes(q);
    });
    return { rows: out, q };
  }

  function stockHtml(r){
    const isReal = !!r._stock_override;
    const v = fmt(r.stock_theoretical);
    const cls = isReal ? "ord-stock-real" : "";
    const badge = isReal ? " <span class=\"badge text-bg-warning ord-badge-mini\">REAL</span>" : "";
    return `<span class=\"${cls}\">${v}</span>${badge}`;
  }

  function renderMeta(meta){
    if (!meta){
      elMeta.innerHTML = "";
      return;
    }
    const sigmaDays = meta.sigma_days_used || 0;
    const sigmaStd = meta.sigma_revenues_std || 0;
    elMeta.innerHTML = `
      <div class="d-flex flex-wrap gap-3 align-items-start">
        <div>
          <div class="fw-semibold">Periodo copertura</div>
          <div class="small text-muted">${meta.coverage_start} → ${meta.coverage_end} (${meta.coverage_days} gg)</div>
        </div>
        <div>
          <div class="fw-semibold">Forecast vendite (totale)</div>
          <div class="small"><span class="badge text-bg-light">€</span> ${fmt(meta.forecast_revenues_total)}</div>
        </div>
        <div>
          <div class="fw-semibold">Vendite mese precedente (totale)</div>
          <div class="small"><span class="badge text-bg-light">€</span> ${fmt(meta.prev_month_revenues_total)}
            <div class="text-muted">${meta.prev_month_start} → ${meta.prev_month_end}</div>
          </div>
        </div>
        <div>
          <div class="fw-semibold">Sigma (variabilità)</div>
          <div class="small">${sigmaDays ? `${sigmaDays} gg (${meta.sigma_start} → ${meta.sigma_end})` : `non calcolabile`}</div>
          <div class="small text-muted">std revenues: ${fmt(sigmaStd)} | z=${meta.z}</div>
        </div>
      </div>
    `;
  }

  function renderRows(){
    const f = getFilteredRows();
    const rows = f.rows || [];

    // badge righe
    if (elCount){
      const tot = (lastRows || []).length;
      if (f.q) elCount.textContent = `Righe: ${rows.length}/${tot}`;
      else elCount.textContent = `Righe: ${tot}`;
    }
    if (elFilterInfo){
      elFilterInfo.textContent = f.q ? `Filtrate: ${rows.length}` : "";
    }

    // desktop table
    if (tblBody){
      tblBody.innerHTML = "";
      rows.forEach((r) => {
        const code = String(r.code || "");
        const tr = document.createElement("tr");
        tr.dataset.code = code;
        tr.style.cursor = "pointer";
        tr.innerHTML = `
          <td>${code}</td>
          <td>${String(r.desc || "")}</td>
          <td class="text-end">${fmt(r.unit_per_1000)}</td>
          <td class="text-end">${stockHtml(r)}</td>
          <td class="text-end">${fmt(r.forecast_consumption)}</td>
          <td class="text-end">${fmt(r.safety_stock)}</td>
          <td class="text-end">${fmt(r.target_stock)}</td>
          <td class="text-end">${fmt(r.order_pz)}</td>
          <td class="text-end">${fmt(r.order_car)}</td>
          <td>${String(r.last_inv_date || "")}</td>
          <td class="text-end"><button class="btn btn-sm btn-outline-primary ord-btn-info" data-code="${code}" type="button">Info</button></td>
        `;
        tblBody.appendChild(tr);
      });
    }

    // mobile list
    if (mobileList){
      mobileList.innerHTML = "";
      rows.forEach((r) => {
        const code = String(r.code || "");
        const desc = String(r.desc || "");
        const a = document.createElement("button");
        a.type = "button";
        a.className = "list-group-item list-group-item-action";
        a.dataset.code = code;

        const ordCar = fmt(r.order_car);
        const ordPz = fmt(r.order_pz);
        const u1000 = fmt(r.unit_per_1000);
        const stock = stockHtml(r);

        a.innerHTML = `
          <div class="d-flex w-100 justify-content-between gap-2">
            <div class="text-truncate">
              <div class="fw-semibold text-truncate">${code} <span class="ord-mobile-desc text-muted">${desc}</span></div>
              <div class="small text-muted">U/1000€: ${u1000} | Stock: ${stock}</div>
            </div>
            <div class="text-end" style="min-width: 92px;">
              <div class="fw-semibold">${ordCar} colli</div>
              <div class="small text-muted">${ordPz} pz</div>
            </div>
          </div>
        `;
        mobileList.appendChild(a);
      });

      if (mobileEmpty){
        if (!rows.length) mobileEmpty.classList.remove("d-none");
        else mobileEmpty.classList.add("d-none");
      }
    }
  }

  const modal = {
    backdrop: $("#ordDetailBackdrop"),
    modal: $("#ordDetailModal"),
    title: $("#ordDetailTitle"),
    subtitle: $("#ordDetailSubtitle"),
    body: $("#ordDetailBody"),
    close: $("#ordDetailClose"),
    currentCode: "",

    open(code){
      const c = String(code || "").trim();
      const row = rowByCode.get(c);
      if (!row) return;

      this.currentCode = c;

      const desc = String(row.desc || "");
      this.title.textContent = `${c} - ${desc}`;

      const sup = (elSupplier?.value || "");
      const oggi = (elOrderDay?.value || "");
      const d1 = (elDate?.value || "");
      const d2 = (elNext?.value || "");
      this.subtitle.textContent = `Fornitore: ${sup} | OGGI: ${oggi} | Consegna 1: ${d1} | Consegna 2: ${d2}`;

      const isOverride = !!row._stock_override;
      const stockUsed = Number(row.stock_theoretical ?? 0);
      const stockReal = isOverride ? Number(row.stock_real ?? stockUsed) : stockUsed;

      const lines = [];

      lines.push(`<div class="ord-section-title">Correzione stock reale (OGGI)</div>`);
      lines.push(`
        <div class="mb-2" style="max-width: 520px;">
          <div class="input-group input-group-sm">
            <span class="input-group-text">Stock reale (pz)</span>
            <input type="text" class="form-control" id="ordRealStockInput" inputmode="decimal" value="${String(stockReal).replace(/\./g, ',')}">
            <button class="btn btn-primary" type="button" id="ordRealStockSave">Applica</button>
            <button class="btn btn-outline-secondary" type="button" id="ordRealStockReset" ${isOverride ? "" : "disabled"}>Reset</button>
          </div>
          <div class="small text-muted mt-1">Usa lo stock reale per ricalcolare u/1000 e suggerimento ordine per questo prodotto.</div>
        </div>
      `);

      lines.push(`<div class="ord-section-title">Stock teorico (al giorno dell'ordine)</div>`);
      lines.push(`<div class="ord-kv">
        <div class="k">INV iniziale</div><div class="v">${fmt(row.inv_qty)} (${row.inv_date||""})</div>
        <div class="k">Periodo considerato</div><div class="v">${row.stock_period_start||""} → ${row.stock_period_end||""}</div>
        <div class="k">Delivery</div><div class="v">${fmt(row.delivery_pz_since_inv)}</div>
        <div class="k">TXIN</div><div class="v">${fmt(row.txin_pz_since_inv)}</div>
        <div class="k">TXOUT</div><div class="v">${fmt(row.txout_pz_since_inv)}</div>
        <div class="k">Vendite usate (reali)</div><div class="v">€ ${fmt(row.past_revenues_used)}</div>
        <div class="k">Consumo stimato</div><div class="v">${fmt(row.past_consumption_est)} (u/1000: ${fmt(row.unit_per_1000)})</div>
        <div class="k">Stock usato</div><div class="v">${fmt(row.stock_theoretical)}${isOverride ? " <span class=\"badge text-bg-warning ord-badge-mini\">REAL</span>" : ""}</div>
      </div>`);

      lines.push(`<div class="ord-section-title">Domanda prevista (periodo copertura)</div>`);
      lines.push(`<div class="ord-kv">
        <div class="k">Forecast vendite (totale)</div><div class="v">€ ${fmt(row.forecast_revenues_total)}</div>
        <div class="k">Consumo previsto</div><div class="v">${fmt(row.forecast_consumption)} (u/1000: ${fmt(row.unit_per_1000)})</div>
      </div>`);

      lines.push(`<div class="ord-section-title">Safety stock e target</div>`);
      lines.push(`<div class="ord-kv">
        <div class="k">Sigma giorni usati</div><div class="v">${row.sigma_days_used||0}</div>
        <div class="k">Std revenues</div><div class="v">${fmt(row.sigma_revenues_std)}</div>
        <div class="k">Safety stock</div><div class="v">${fmt(row.safety_stock)}</div>
        <div class="k">Target</div><div class="v">${fmt(row.target_stock)}</div>
      </div>`);

      lines.push(`<div class="ord-section-title">Ordine</div>`);
      lines.push(`<div class="ord-kv">
        <div class="k">Ordine (pz)</div><div class="v">${fmt(row.order_pz)}</div>
        <div class="k">Ordine (colli)</div><div class="v">${fmt(row.order_car)} (qta/collo: ${fmt(row.qtacar)})</div>
      </div>`);

      if (row.stock_kg !== null && row.stock_kg !== undefined) {
        lines.push(`<div class="ord-section-title">Conversione KG</div>`);
        lines.push(`<div class="ord-kv">
          <div class="k">Fattore kg/pezzo</div><div class="v">${fmt(row.conv_kg_per_pz)}</div>
          <div class="k">Stock (kg)</div><div class="v">${fmt(row.stock_kg)}</div>
          <div class="k">Ordine (kg)</div><div class="v">${fmt(row.order_kg)}</div>
        </div>`);
      }

      this.body.innerHTML = lines.join("");

      // listeners correzione stock
      const inp = this.body.querySelector("#ordRealStockInput");
      const btnSave = this.body.querySelector("#ordRealStockSave");
      const btnReset = this.body.querySelector("#ordRealStockReset");

      if (btnSave) {
        btnSave.addEventListener("click", () => {
          const v = parseNum(inp?.value);
          if (!Number.isFinite(v) || v < 0) {
            toast(["Inserisci un valore valido per lo stock reale (numero >= 0)."], "danger");
            return;
          }

          const ok = applyOverrideToRow(c, v, true);
          if (!ok) return;

          renderRows();
          this.open(c);
        });
      }

      if (btnReset) {
        btnReset.addEventListener("click", () => {
          const ok = clearOverrideForCode(c, true);
          if (!ok) return;
          renderRows();
          this.open(c);
        });
      }

      if (this.backdrop) this.backdrop.style.display = "block";
      if (this.modal) this.modal.style.display = "flex";
    },

    hide(){
      if (this.backdrop) this.backdrop.style.display = "none";
      if (this.modal) this.modal.style.display = "none";
    }
  };

  if (modal.close) modal.close.addEventListener("click", () => modal.hide());
  if (modal.backdrop) modal.backdrop.addEventListener("click", () => modal.hide());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") modal.hide(); });

  // Delegation: table clicks
  if (tblBody) {
    tblBody.addEventListener("click", (e) => {
      const btn = e.target && e.target.closest && e.target.closest("button.ord-btn-info");
      const tr = e.target && e.target.closest && e.target.closest("tr[data-code]");
      const code = (btn && btn.dataset && btn.dataset.code) ? btn.dataset.code : (tr && tr.dataset ? tr.dataset.code : "");
      if (!code) return;
      e.preventDefault();
      modal.open(code);
    });
  }

  // Delegation: mobile list clicks
  if (mobileList) {
    mobileList.addEventListener("click", (e) => {
      const item = e.target && e.target.closest && e.target.closest("[data-code]");
      const code = (item && item.dataset) ? item.dataset.code : "";
      if (!code) return;
      e.preventDefault();
      modal.open(code);
    });
  }

  async function run(){
    if (btnRun) btnRun.disabled = true;
    if (btnExport) btnExport.disabled = true;
    if (elCount) elCount.textContent = "Righe: ...";
    toast([], "warning");
    renderMeta(null);

    lastRows = [];
    baseByCode = new Map();
    rowByCode = new Map();
    renderRows();

    const supplier = String(elSupplier?.value || "").trim();
    const listino = String(elListino?.value || "").trim();
    const order_date = (elOrderDay && elOrderDay.value) ? elOrderDay.value : todayISO();
    const next_delivery = String(elNext?.value || "");

    try {
      const qs = new URLSearchParams({
        supplier: supplier,
        listino: listino,
        order_date: order_date,
        next_delivery: next_delivery
      });
      const resp = await fetch(`/magazzino/api/ordini?${qs.toString()}`);
      const data = await resp.json();
      if (!resp.ok){
        throw new Error(data && data.error ? data.error : `Errore ${resp.status}`);
      }

      lastMeta = data.meta || null;
      const rows = Array.isArray(data.rows) ? data.rows : [];

      // base
      baseByCode = new Map();
      rows.forEach(r => {
        const c = String(r && r.code ? r.code : "");
        if (!c) return;
        const base = deepCopy(r);
        base._stock_override = false;
        normalizeOrder(base);
        baseByCode.set(c, base);
      });

      // working copy
      lastRows = rows.map(r => {
        const rr = deepCopy(r);
        rr._stock_override = false;
        normalizeOrder(rr);
        return rr;
      });

      rebuildMaps();

      // applica override salvati
      const ov = readOverrides();
      Object.keys(ov || {}).forEach(code => {
        const v = Number(ov[code]);
        if (Number.isFinite(v)) {
          applyOverrideToRow(code, v, false);
        }
      });

      renderMeta(lastMeta);
      renderRows();

      const warns = Array.isArray(data.warnings) ? data.warnings : [];
      if (warns.length) toast(warns, "warning");

      if (btnExport) btnExport.disabled = !lastRows.length;

    } catch (e) {
      if (elCount) elCount.textContent = "Righe: 0";
      toast([String(e && e.message ? e.message : e)], "danger");

    } finally {
      if (btnRun) btnRun.disabled = false;
    }
  }

  function exportCSV(){
    if (!lastRows || !lastRows.length) return;

    const header = [
      "Codice","Descrizione","U_per_1000","Stock_usato","Consumo_previsto","Safety_stock","Target",
      "Ordine_pz","Ordine_colli","Qta_car","Ultimo_INV_Data",
      "INV_Qta","Delivery_pz","TXIN_pz","TXOUT_pz",
      "Revenues_past_used","Consumo_past_used","Forecast_revenues_total","Sigma_days_used","Sigma_revenues_std",
      "Stock_override","Stock_reale"
    ];

    const rows = lastRows.map(r => ([
      r.code||"", (String(r.desc||"")).replaceAll('"','""'),
      fmt(r.unit_per_1000), fmt(r.stock_theoretical), fmt(r.forecast_consumption),
      fmt(r.safety_stock), fmt(r.target_stock),
      fmt(r.order_pz), r.order_car||0, fmt(r.qtacar),
      r.last_inv_date||"",
      fmt(r.inv_qty),
      fmt(r.delivery_pz_since_inv), fmt(r.txin_pz_since_inv), fmt(r.txout_pz_since_inv),
      fmt(r.past_revenues_used), fmt(r.past_consumption_est),
      fmt(r.forecast_revenues_total), r.sigma_days_used||0, fmt(r.sigma_revenues_std),
      r._stock_override ? 1 : 0,
      r._stock_override ? fmt(r.stock_real) : ""
    ]));

    const csv = [header, ...rows].map(arr => arr.map(v => `"${String(v??"")}"`).join(";")).join("\n");
    const blob = new Blob([csv], {type:"text/csv;charset=utf-8"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    const sup = (elSupplier?.value || "fornitore").replaceAll(" ","_");
    const d1 = String(elDate?.value || "");
    const d2 = String(elNext?.value || "");
    a.download = `ordini_${sup}_${d1}_${d2}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // Defaults: se Consegna 2 è vuota, mettila a Consegna 1 + 7 giorni
  const ensureDelivery2 = () => {
    if (!elNext || !elDate) return;
    if (!elDate.value) return;
    if (!elNext.value){
      elNext.value = addDaysISO(elDate.value, 7);
      return;
    }
    if (elNext.value <= elDate.value){
      elNext.value = addDaysISO(elDate.value, 7);
    }
  };

  if (elDate) elDate.addEventListener("change", ensureDelivery2);
  ensureDelivery2();

  if (btnRun) btnRun.addEventListener("click", run);
  if (btnExport) btnExport.addEventListener("click", exportCSV);
  if (elSearch) elSearch.addEventListener("input", () => renderRows());

})();
