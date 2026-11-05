const $ = (id) => document.getElementById(id);
const post = (url, body) => fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) }).then((r) => r.json());

let lastLogLength = 0;

function renderStats(state) {
  const done = state.accounts.filter((a) => a.status === "done").length;
  const running = state.accounts.filter((a) => a.is_running).length;
  const errors = state.accounts.filter((a) => a.status === "error").length;
  $("summaryStats").textContent = `mode ${state.mode} | accounts ${state.accounts.length} | done ${done} | running ${running} | errors ${errors} | max stack ${state.max_stack_months} months`;
}

function renderConfig(state) {
  if (document.activeElement && document.activeElement.tagName === "INPUT") return;
  $("cfgMode").value = state.mode;
  $("cfgTargetRegion").value = state.target_region;
  $("cfgTargetPlan").value = state.target_plan;
  $("cfgAutoCancel").checked = !!state.auto_cancel;
  $("cfgHeadless").checked = !!state.headless;
  $("cfgProxyTemplate").value = state.proxy_template || "";
  $("cfgCardNumber").value = state.card_last4 ? "************" + state.card_last4 : "";
  $("cfgExpMonth").value = state.card.exp_month || "";
  $("cfgExpYear").value = state.card.exp_year || "";
  $("cfgHolder").value = state.card.holder || "";
  $("cfgAddr").value = state.address.address_line1 || "";
  $("cfgCity").value = state.address.locality || "";
  $("cfgProvince").value = state.address.province || "";
  $("cfgZip").value = state.address.postal_code || "";
}

function renderAccounts(state) {
  const tbody = $("accountsTbody");
  tbody.innerHTML = "";
  for (const acc of state.accounts) {
    const tr = document.createElement("tr");
    const shot = acc.last_screenshot ? `<button data-shot="${acc.id}">proof</button>` : "";
    tr.innerHTML = `<td title="${acc.email_masked}">${acc.email_masked}${acc.proxy ? " (proxy)" : ""}</td>
      <td class="status-${acc.status}">${acc.status}</td>
      <td>${acc.current_step}/5 ${acc.step_message || ""}</td>
      <td>${acc.expiry_date || "-"}</td>
      <td>${acc.has_totp ? acc.totp_now + " (" + acc.totp_rem + "s)" : "no totp"}</td>
      <td>
        <button data-run="${acc.id}" ${acc.is_running ? "disabled" : ""}>Execute</button>
        <button data-reset="${acc.id}">Reset</button>
        <button data-delete="${acc.id}">Delete</button>
        ${shot}
      </td>`;
    tbody.appendChild(tr);
  }
}

function renderLogs(state) {
  const term = $("logTerminal");
  if (state.logs.length === lastLogLength) return;
  lastLogLength = state.logs.length;
  term.textContent = state.logs.join("\n");
  term.scrollTop = term.scrollHeight;
}

async function refresh() {
  try {
    const state = await fetch("/api/state").then((r) => r.json());
    renderStats(state);
    renderConfig(state);
    renderAccounts(state);
    renderLogs(state);
  } catch (err) {
    console.error(err);
  }
}

$("btnSaveConfig").addEventListener("click", async () => {
  const card = {
    exp_month: $("cfgExpMonth").value,
    exp_year: $("cfgExpYear").value,
    cvc: $("cfgCvc").value,
    holder: $("cfgHolder").value,
  };
  const typed = $("cfgCardNumber").value.replace(/\D/g, "");
  if (typed.length >= 13) card.number = typed;
  await post("/api/config", {
    mode: $("cfgMode").value,
    target_region: $("cfgTargetRegion").value,
    target_plan: $("cfgTargetPlan").value,
    auto_cancel: $("cfgAutoCancel").checked,
    headless: $("cfgHeadless").checked,
    proxy_template: $("cfgProxyTemplate").value,
    card,
    address: {
      address_line1: $("cfgAddr").value,
      locality: $("cfgCity").value,
      province: $("cfgProvince").value,
      postal_code: $("cfgZip").value,
      country: "US",
    },
  });
  $("cfgCvc").value = "";
  lastLogLength = -1;
  refresh();
});

$("btnTestProxy").addEventListener("click", async () => {
  const result = await post("/api/proxy/test", { proxy: $("cfgProxyTemplate").value });
  $("proxyTestResult").textContent = result.ok ? "proxy reachable" : "proxy problem: " + result.detail;
});

$("btnImport").addEventListener("click", async () => {
  const result = await post("/api/accounts/import", { raw_text: $("importText").value });
  $("importText").value = "";
  lastLogLength = -1;
  refresh();
  if (result.rejected && result.rejected.length) {
    $("proxyTestResult").textContent = result.rejected.length + " line(s) rejected";
  }
});

$("btnBatchRun").addEventListener("click", async () => {
  await post("/api/run/batch", { start_step: 1 });
  lastLogLength = -1;
  refresh();
});

$("btnStop").addEventListener("click", async () => {
  await post("/api/stop", {});
  refresh();
});

$("accountsTbody").addEventListener("click", async (event) => {
  const target = event.target;
  if (target.dataset.run) await post("/api/run/single", { id: target.dataset.run });
  if (target.dataset.reset) await post("/api/accounts/reset", { id: target.dataset.reset });
  if (target.dataset.delete) await post("/api/accounts/delete", { id: target.dataset.delete });
  if (target.dataset.shot) {
    const state = await fetch("/api/state").then((r) => r.json());
    const acc = state.accounts.find((a) => a.id === target.dataset.shot);
    if (acc && acc.last_screenshot) {
      $("modalTitle").textContent = acc.email_masked;
      $("imgModal").src = "/screenshots/" + acc.id + "/final-proof.png?" + Date.now();
      $("modalImg").hidden = false;
    }
  }
  lastLogLength = -1;
  refresh();
});

$("modalClose").addEventListener("click", () => { $("modalImg").hidden = true; });

refresh();
setInterval(refresh, 2000);
