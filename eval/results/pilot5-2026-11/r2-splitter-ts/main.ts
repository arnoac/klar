interface Expense {
  name: string;
  cents: number;
  desc: string;
}

const STORAGE_KEY = "splitter-expenses";

function loadExpenses(): Expense[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const data: unknown = JSON.parse(raw);
    if (!Array.isArray(data)) return [];
    const out: Expense[] = [];
    for (const item of data) {
      if (
        item && typeof item === "object" &&
        typeof (item as Expense).name === "string" &&
        typeof (item as Expense).cents === "number" &&
        Number.isInteger((item as Expense).cents) &&
        (item as Expense).cents > 0
      ) {
        const e = item as Expense;
        out.push({ name: e.name, cents: e.cents, desc: typeof e.desc === "string" ? e.desc : "" });
      }
    }
    return out;
  } catch {
    return [];
  }
}

function saveExpenses(list: Expense[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {
    // ignore storage failures
  }
}

function formatCents(cents: number): string {
  const neg = cents < 0;
  const abs = Math.abs(cents);
  const s = `${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
  return neg ? `-${s}` : s;
}

function formatSigned(cents: number): string {
  if (cents === 0) return "0.00";
  return cents > 0 ? `+${formatCents(cents)}` : formatCents(cents);
}

function parseAmount(text: string): number | null {
  const t = text.trim();
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(t);
  if (!m) return null;
  const whole = m[1].replace(/^0+(?=\d)/, "");
  if (whole.length > 13) return null;
  const frac = (m[2] ?? "").padEnd(2, "0");
  const cents = Number(whole) * 100 + Number(frac);
  if (!Number.isSafeInteger(cents) || cents <= 0) return null;
  return cents;
}

let expenses: Expense[] = loadExpenses();

const app = document.getElementById("app") ?? document.body;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string> = {}, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}

const title = el("h1", {}, "Splitter");
const form = el("div", { class: "form" });
const nameInput = el("input", { id: "name", type: "text", placeholder: "Who paid" });
const amountInput = el("input", { id: "amount", type: "text", placeholder: "Amount", inputmode: "decimal" });
const descInput = el("input", { id: "desc", type: "text", placeholder: "Description" });
const addBtn = el("button", { id: "add", type: "button" }, "Add");
form.append(nameInput, amountInput, descInput, addBtn);

const errorSlot = el("div", { class: "error-slot" });
const expensesList = el("ul", { id: "expenses" });
const totalEl = el("p", { id: "total" });
const balancesList = el("ul", { id: "balances" });

app.append(
  title,
  form,
  errorSlot,
  el("h2", {}, "Expenses"),
  expensesList,
  totalEl,
  el("h2", {}, "Balances"),
  balancesList,
);

function showError(msg: string): void {
  let e = document.getElementById("error");
  if (!e) {
    e = el("p", { id: "error", role: "alert" });
    e.style.color = "#b00020";
    errorSlot.append(e);
  }
  e.textContent = msg;
}

function clearError(): void {
  document.getElementById("error")?.remove();
}

function render(): void {
  expensesList.replaceChildren();
  expenses.forEach((exp, index) => {
    const li = el("li");
    const text = `${exp.name} paid ${formatCents(exp.cents)}` + (exp.desc ? ` for ${exp.desc}` : "");
    li.append(document.createTextNode(text + " "));
    const btn = el("button", { class: "remove", type: "button" }, "Remove");
    btn.addEventListener("click", () => {
      expenses.splice(index, 1);
      saveExpenses(expenses);
      render();
    });
    li.append(btn);
    expensesList.append(li);
  });

  const total = expenses.reduce((s, e) => s + e.cents, 0);
  totalEl.textContent = `Total: ${formatCents(total)}`;

  const paid = new Map<string, number>();
  for (const e of expenses) paid.set(e.name, (paid.get(e.name) ?? 0) + e.cents);
  const names = [...paid.keys()].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  balancesList.replaceChildren();
  if (names.length > 0) {
    const n = names.length;
    const share = Math.floor((2 * total + n) / (2 * n));
    for (const name of names) {
      balancesList.append(el("li", {}, `${name}: ${formatSigned((paid.get(name) ?? 0) - share)}`));
    }
  }
}

function addExpense(): void {
  const name = nameInput.value.trim();
  const cents = parseAmount(amountInput.value);
  if (!name) {
    showError("Please enter who paid.");
    return;
  }
  if (cents === null) {
    showError("Please enter a valid amount greater than zero (e.g. 12.50).");
    return;
  }
  expenses.push({ name, cents, desc: descInput.value.trim() });
  saveExpenses(expenses);
  amountInput.value = "";
  descInput.value = "";
  clearError();
  render();
}

addBtn.addEventListener("click", addExpense);
for (const input of [nameInput, amountInput, descInput]) {
  input.addEventListener("keydown", (ev: KeyboardEvent) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      addExpense();
    }
  });
}

render();
