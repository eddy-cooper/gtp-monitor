/* GTP dashboard client. Renders everything from one payload object
   (inlined into the page on first load, re-fetched from /api/dashboard
   on Refresh). All user-entered text (comments, notes) is inserted via
   textContent, never innerHTML. */

"use strict";

const CAP_ROWS = 31; // one month of daily rows before "Show all"

const ICONS = {
  good: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9.5"/><path d="m8.5 12.5 2.5 2.5 4.5-5"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="9.5"/><path d="M12 7.5v5.5"/><path d="M12 16.8v.2"/></svg>',
  serious: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9.5"/><path d="M9.5 9.3a2.5 2.5 0 1 1 3.4 2.9c-.7.35-.9.8-.9 1.8"/><path d="M12 17v.2"/></svg>',
  critical: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2.8 19.5h18.4z"/><path d="M12 9.5v4"/><path d="M12 16.6v.2"/></svg>',
  muted: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="9.5"/><path d="M8 12h8"/></svg>',
};

const state = {
  payload: window.__INITIAL__,
  table: "readings",
  showAll: false,
  chart: null,
};

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* --- header ------------------------------------------------------------ */

function renderHeader() {
  const { status, today } = state.payload;
  document.getElementById("today").textContent = today;
  const badge = document.getElementById("alert-badge");
  badge.className = "badge " + status.badge.role;
  badge.innerHTML = ICONS[status.badge.role] || ICONS.muted;
  const label = status.badge.label + (status.accelerating ? " · accelerating" : "");
  badge.appendChild(document.createTextNode(label));
}

/* --- notices ----------------------------------------------------------- */

function renderNotice() {
  const holder = document.getElementById("notice");
  holder.textContent = "";
  const note = state.payload.status.lag_note;
  if (note) {
    const bar = el("div", "notice", note);
    holder.appendChild(bar);
  }
}

/* --- stat tiles --------------------------------------------------------- */

function countUp(node, target, decimals, suffix) {
  // Honour reduced-motion (also makes static screenshots show the
  // final value rather than a mid-animation frame).
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    node.textContent = target.toFixed(decimals) + suffix;
    return;
  }
  const dur = 650;
  const t0 = performance.now();
  function frame(now) {
    const p = Math.min((now - t0) / dur, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    node.textContent = (target * eased).toFixed(decimals) + suffix;
    if (p < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function heroTile(status) {
  const tile = el("div", "tile hero " + status.badge.role);
  tile.appendChild(el("p", "label", "FIT0101 under-reading"));

  const value = el("div", "value");
  const num = el("span");
  value.appendChild(num);
  if (status.under_pct_value !== null) {
    countUp(num, status.under_pct_value, 3, "%");
  } else {
    num.textContent = "n/a";
  }

  if (status.trend) {
    const dir = status.trend.direction;
    const cls = dir === "rising" ? "up" : dir === "falling" ? "down" : "flat";
    const arrow = dir === "rising" ? "▲" : dir === "falling" ? "▼" : "▬";
    value.appendChild(
      el("span", "delta " + cls, `${arrow} vs ${status.trend.previous_pct} in ${status.trend.previous_period}`)
    );
  }
  tile.appendChild(value);

  const bits = [`Period ${status.period_start} to ${status.period_end}`];
  if (status.contains_estimates) bits.push("contains estimated data");
  if (status.accelerating_note) bits.push("accelerating: " + status.accelerating_note);
  tile.appendChild(el("p", "detail", bits.join(" · ")));
  return tile;
}

function simpleTile(label, valueNode, detail, extraClass) {
  const tile = el("div", "tile" + (extraClass ? " " + extraClass : ""));
  tile.appendChild(el("p", "label", label));
  const value = el("div", "value");
  if (typeof valueNode === "string") value.textContent = valueNode;
  else value.appendChild(valueNode);
  tile.appendChild(value);
  if (detail) tile.appendChild(el("p", "detail", detail));
  return tile;
}

function renderTiles() {
  const holder = document.getElementById("tiles");
  holder.textContent = "";
  const status = state.payload.status;

  if (!status.has_balance) {
    const tile = el("div", "tile hero muted");
    tile.appendChild(el("p", "label", "No balance yet"));
    tile.appendChild(el("div", "value", "—"));
    tile.appendChild(el("p", "detail", "No balance has been computed yet. Run `gtp balance` (or the desktop app's Recompute) first."));
    holder.appendChild(tile);
    return;
  }

  holder.appendChild(heroTile(status));

  const klValue = el("span");
  const klNode = el("span");
  klValue.appendChild(klNode);
  countUp(klNode, status.under_kl_value, 3, "");
  const klUnit = el("small", null, " kL");
  klValue.appendChild(klUnit);
  holder.appendChild(simpleTile("Unaccounted volume", klValue, "meter under-read, latest period"));

  let entryValue, entryDetail;
  if (status.days_since_last_entry === null) {
    entryValue = status.last_entry_date || "—";
    entryDetail = "most recent daily reading";
  } else if (status.days_since_last_entry === 0) {
    entryValue = "today";
    entryDetail = status.last_entry_date;
  } else {
    const d = status.days_since_last_entry;
    entryValue = `${d} day${d === 1 ? "" : "s"} ago`;
    entryDetail = status.last_entry_date;
  }
  holder.appendChild(
    simpleTile("Last entry", entryValue, entryDetail, status.entry_stale ? "stale" : "")
  );

  const valWrap = el("span");
  valWrap.appendChild(el("span", null, String(status.block_count)));
  valWrap.appendChild(el("small", null, " unresolved block" + (status.block_count === 1 ? "" : "s")));
  holder.appendChild(
    simpleTile("Validation", valWrap, `${status.warn_count} warning${status.warn_count === 1 ? "" : "s"} since ${status.period_start}`)
  );

  const chip = el("span", "chip " + (status.contains_estimates ? "warn" : "ok"),
    status.contains_estimates ? "contains estimates" : "all measured");
  holder.appendChild(simpleTile("Data quality", chip, "latest balance period"));
}

/* --- trend chart -------------------------------------------------------- */

const thresholdLabels = {
  id: "thresholdLabels",
  afterDatasetsDraw(chart, _args, opts) {
    const { ctx, chartArea, scales } = chart;
    ctx.save();
    ctx.font = '600 11px "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = "right";
    ctx.textBaseline = "bottom";
    for (const line of opts.lines || []) {
      const y = scales.y.getPixelForValue(line.value);
      if (y < chartArea.top - 2 || y > chartArea.bottom) continue;
      ctx.fillStyle = line.color;
      // If the line sits near the top edge, put the label below it.
      const cramped = y < chartArea.top + 14;
      ctx.textBaseline = cramped ? "top" : "bottom";
      ctx.fillText(line.label, chartArea.right - 4, cramped ? y + 4 : y - 3);
    }
    ctx.restore();
  },
};

function renderChart() {
  const trend = state.payload.trend;
  const surface = cssVar("--surface");
  const series = cssVar("--series");

  document.getElementById("chart-key").classList.toggle("hidden", !trend.estimated.some(Boolean));

  if (state.chart) state.chart.destroy();
  const flat = (v) => trend.labels.map(() => v);

  state.chart = new Chart(document.getElementById("trend-chart"), {
    type: "line",
    data: {
      labels: trend.labels,
      datasets: [
        {
          data: trend.values,
          borderColor: series,
          borderWidth: 2,
          tension: 0,
          spanGaps: false, // a period with no % is a genuine gap, not a line
          pointRadius: 5,
          pointHoverRadius: 7,
          pointBorderWidth: 2,
          pointBorderColor: series,
          // hollow marker = period contains estimated data (same
          // shape-not-color convention as the matplotlib chart)
          pointBackgroundColor: trend.values.map((_v, i) =>
            trend.estimated[i] ? surface : series
          ),
        },
        {
          data: flat(trend.action_pct),
          borderColor: cssVar("--critical"),
          borderWidth: 1.5,
          borderDash: [6, 4],
          pointRadius: 0,
          pointHitRadius: 0,
        },
        {
          data: flat(trend.watch_pct),
          borderColor: "#fab219",
          borderWidth: 1.5,
          borderDash: [6, 4],
          pointRadius: 0,
          pointHitRadius: 0,
        },
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        thresholdLabels: {
          lines: [
            { value: trend.action_pct, label: trend.action_pct.toFixed(1) + "% action", color: cssVar("--critical") },
            { value: trend.watch_pct, label: trend.watch_pct.toFixed(1) + "% watch", color: cssVar("--warn") },
          ],
        },
        tooltip: {
          filter: (item) => item.datasetIndex === 0,
          displayColors: false,
          backgroundColor: cssVar("--ink"),
          padding: 10,
          cornerRadius: 8,
          titleFont: { weight: "650" },
          callbacks: {
            label: (ctx) => {
              if (ctx.parsed.y === null) return "no data";
              let text = ctx.parsed.y.toFixed(3) + "% under-reading";
              if (trend.estimated[ctx.dataIndex]) text += " · contains estimated data";
              return text;
            },
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { color: cssVar("--axis") },
          ticks: { color: cssVar("--ink-2"), maxRotation: 35, font: { size: 11 } },
        },
        y: {
          grace: "8%", // headroom so a threshold line never hugs the edge
          title: { display: true, text: "FIT0101 under-reading (%)", color: cssVar("--ink-2"), font: { size: 12 } },
          grid: { color: cssVar("--grid") },
          border: { color: cssVar("--axis"), display: false },
          ticks: { color: cssVar("--ink-2"), font: { size: 11 } },
        },
      },
    },
    plugins: [thresholdLabels],
  });
}

/* --- records table ------------------------------------------------------ */

function isNumericColumn(label) {
  return /\(kL\)|\(%\)|fraction|error/i.test(label) || label === "Under (%)";
}

function renderTable() {
  const dataset = state.payload.tables[state.table];
  const table = document.getElementById("data-table");
  table.textContent = "";

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const col of dataset.columns) {
    const th = el("th", isNumericColumn(col) ? "num" : "", col);
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  const capped = !state.showAll && dataset.rows.length > CAP_ROWS;
  const rows = capped ? dataset.rows.slice(0, CAP_ROWS) : dataset.rows;

  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = el("td", "empty", "Nothing recorded yet.");
    td.colSpan = dataset.columns.length;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  for (const row of rows) {
    const tr = document.createElement("tr");
    row.forEach((cell, i) => {
      const col = dataset.columns[i];
      const td = el("td", isNumericColumn(col) ? "num" : "");
      if ((col === "Estimated" || col === "Has estimates") && cell) {
        const flag = el("span", "flag", cell);
        flag.title = cell; // full reason on hover; the pill ellipsizes
        td.appendChild(flag);
      } else {
        td.textContent = cell;
        if (cell && cell.length > 40) td.title = cell;
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);

  const count = document.getElementById("table-count");
  count.textContent = capped
    ? `Showing latest ${CAP_ROWS} of ${dataset.rows.length} rows`
    : `${dataset.rows.length} row${dataset.rows.length === 1 ? "" : "s"}`;

  const showAllBtn = document.getElementById("show-all");
  showAllBtn.classList.toggle("hidden", dataset.rows.length <= CAP_ROWS);
  showAllBtn.textContent = state.showAll ? "Show fewer" : "Show all";
}

/* --- wiring ------------------------------------------------------------- */

function renderAll() {
  renderHeader();
  renderNotice();
  renderTiles();
  renderChart();
  renderTable();
}

document.getElementById("table-picker").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-table]");
  if (!button) return;
  for (const b of event.currentTarget.querySelectorAll("button")) {
    b.classList.toggle("active", b === button);
  }
  state.table = button.dataset.table;
  state.showAll = false;
  renderTable();
});

document.getElementById("show-all").addEventListener("click", () => {
  state.showAll = !state.showAll;
  renderTable();
});

document.getElementById("refresh").addEventListener("click", async () => {
  const response = await fetch("/api/dashboard");
  state.payload = await response.json();
  renderAll();
});

document.getElementById("quit").addEventListener("click", async () => {
  try {
    await fetch("/shutdown", { method: "POST" });
  } catch (_e) {
    /* server may die before responding — that's fine */
  }
  document.body.innerHTML =
    '<div class="farewell"><h2>Dashboard stopped</h2><p>The local server has shut down. You can close this tab.</p></div>';
});

renderAll();
