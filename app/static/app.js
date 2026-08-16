const state = { apiKey: sessionStorage.getItem("ledger-api-key") || "", accounts: [] };
const notice = document.querySelector("#notice");
const keyInput = document.querySelector("#api-key");
const accountDialog = document.querySelector("#account-dialog");
keyInput.value = state.apiKey;

function showNotice(message, isError = false) { notice.textContent = message; notice.classList.toggle("error", isError); }
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", "X-API-Key": state.apiKey, ...(options.headers || {}) } });
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.error?.message || body.detail?.message || "The request could not be completed."); }
  return response.status === 204 ? null : response.json();
}
function formatMoney(minor, currency) { return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(minor / 100); }
function setAccountSelect(select, includePlaceholder = true) {
  const previous = select.value; select.replaceChildren(); if (includePlaceholder) select.add(new Option("Choose an account", ""));
  state.accounts.forEach((account) => select.add(new Option(account.name, account.id)));
  if ([...select.options].some((option) => option.value === previous)) select.value = previous;
}
function renderAccounts() {
  const container = document.querySelector("#accounts"); container.replaceChildren();
  if (!state.accounts.length) { container.innerHTML = '<p class="empty-state">No accounts yet. Create an external funding account, then an operating account.</p>'; return; }
  state.accounts.forEach((account) => { const card = document.createElement("article"); card.className = "account-card"; card.innerHTML = `<p>${account.allow_negative ? "EXTERNAL FUNDING" : "LEDGER ACCOUNT"}</p><h3></h3><strong>${formatMoney(account.balance_minor, account.currency)}</strong><span>${account.currency}</span>`; card.querySelector("h3").textContent = account.name; container.append(card); });
}
async function loadEntries() {
  const accountId = document.querySelector("#activity-account").value; const container = document.querySelector("#entries");
  if (!accountId) { container.innerHTML = '<p class="empty-state">Select an account to view its immutable entries.</p>'; return; }
  try { const page = await api(`/accounts/${accountId}/entries`); container.replaceChildren(); if (!page.items.length) { container.innerHTML = '<p class="empty-state">No activity yet.</p>'; return; }
    page.items.forEach((entry) => { const row = document.createElement("div"); row.className = "entry"; const sign = entry.amount_minor >= 0 ? "+" : "−"; row.innerHTML = `<div><strong>${sign}${formatMoney(Math.abs(entry.amount_minor), entry.currency)}</strong><span></span></div><time></time>`; row.querySelector("span").textContent = `Transaction ${entry.transaction_id.slice(0, 8)}`; row.querySelector("time").textContent = new Date(entry.created_at).toLocaleString(); container.append(row); });
  } catch (error) { showNotice(error.message, true); }
}
async function loadAccounts() {
  if (!state.apiKey) return;
  try { state.accounts = await api("/accounts"); renderAccounts(); ["#source-account", "#destination-account", "#activity-account"].forEach((id) => setAccountSelect(document.querySelector(id))); showNotice(`${state.accounts.length} account${state.accounts.length === 1 ? "" : "s"} loaded.`); await loadEntries(); } catch (error) { showNotice(error.message, true); }
}
document.querySelector("#api-key-form").addEventListener("submit", async (event) => { event.preventDefault(); state.apiKey = keyInput.value.trim(); sessionStorage.setItem("ledger-api-key", state.apiKey); await loadAccounts(); });
document.querySelector("#refresh-button").addEventListener("click", loadAccounts);
document.querySelector("#new-account-button").addEventListener("click", () => accountDialog.showModal());
document.querySelector("#close-dialog").addEventListener("click", () => accountDialog.close());
document.querySelector("#activity-account").addEventListener("change", loadEntries);
document.querySelector("#account-form").addEventListener("submit", async (event) => { event.preventDefault(); try { await api("/accounts", { method: "POST", body: JSON.stringify({ name: document.querySelector("#account-name").value.trim(), currency: document.querySelector("#account-currency").value.trim(), allow_negative: document.querySelector("#allow-negative").checked }) }); accountDialog.close(); event.target.reset(); document.querySelector("#account-currency").value = "USD"; showNotice("Account created."); await loadAccounts(); } catch (error) { showNotice(error.message, true); } });
document.querySelector("#transfer-form").addEventListener("submit", async (event) => { event.preventDefault(); const source = document.querySelector("#source-account").value; const destination = document.querySelector("#destination-account").value; if (source === destination) { showNotice("Choose two different accounts.", true); return; } const amountMinor = Math.round(Number(document.querySelector("#amount").value) * 100); try { await api("/transfers", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ source_account_id: source, destination_account_id: destination, amount_minor: amountMinor, reference: document.querySelector("#reference").value.trim() || null }) }); event.target.reset(); showNotice("Transfer posted exactly once."); await loadAccounts(); } catch (error) { showNotice(error.message, true); } });
if (state.apiKey) loadAccounts();
