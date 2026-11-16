interface Expense {
  name: string;
  cents: number;
  desc: string;
}

const STORAGE_KEY = "splitter.expenses.v1";

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
        typeof item.name === "string" &&
        typeof item.cents === "number" && Number.isInteger(item.cents) && item.cents > 0 &&
        typeof item.desc === "string"
      ) {
        out.push({ name: item.name, cents: item.cents, desc: item.desc });
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

function fmt(cents: number): string {
  const neg = cents < 0;
  const abs = Math.abs(cents);
  const s = `${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
  return neg ? `-${s}` : s;
}

function fmtSigned(cents: number): string {
  if (cents === 0) return "0.00";
  return cents > 0 ? `+${fmt(cents)}` : fmt(cents);
}

function parseAmount(text: string): number | null {
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (!m) return null;
  const whole = m[1].replace(/^0+(?=\d)/, "");
  const frac = (m[2] ?? "").padEnd(2, "0");
  if (whole.length > 13) return null;
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

const form = el("div", { class: "form" });
const nameInput = el("input", { id: "name", type: "text", placeholder: "Who paid", autocomplete: "off" });
const amountInput = el("input", { id: "amount", type: "text", placeholder: "Amount", inputmode: "decimal", autocomplete: "off" });
const descInput = el("input", { id: "desc", type: "text", placeholder: "For what", autocomplete: "off" });
const addButton = el("button", { id: "add", type: "button" }, "Add");
form.append(nameInput, amountInput, descInput, addButton);

const errorSlot = el("div", { class: "error-slot" });
const expensesHeading = el("h2", {}, "Expenses");
const expensesList = el("ul", { id: "expenses" });
const totalEl = el("p", { id: "total" });
const balancesHeading = el("h2", {}, "Balances");
const balancesList = el("ul", { id: "balances" });

app.append(form, errorSlot, expensesHeading, expensesList, totalEl, balancesHeading, balancesList);

function showError(msg: string): void {
  let e = document.getElementById("error");
  if (!e) {
    e = el("p", { id: "error", role: "alert" });
    errorSlot.append(e);
  }
  e.textContent = msg;
}

function clearError(): void {
  document.getElementById("error")?.remove();
}

function render(): void {
  expensesList.replaceChildren();
  expenses.forEach((x, i) => {
    const li = el("li");
    const desc = x.desc.trim();
    li.append(document.createTextNode(desc ? `${x.name} paid ${fmt(x.cents)} for ${desc}` : `${x.name} paid ${fmt(x.cents)}`));
    li.append(document.createTextNode(" "));
    const btn = el("button", { type: "button", class: "remove" }, "Remove");
    btn.addEventListener("click", () => {
      expenses.splice(i, 1);
      saveExpenses(expenses);
      render();
    });
    li.append(btn);
    expensesList.append(li);
  });

  let total = 0;
  const paid = new Map<string, number>();
  for (const x of expenses) {
    total += x.cents;
    paid.set(x.name, (paid.get(x.name) ?? 0) + x.cents);
  }
  totalEl.textContent = `Total: ${fmt(total)}`;

  balancesList.replaceChildren();
  const names = [...paid.keys()].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  const n = names.length;
  if (n > 0) {
    const share = Math.floor((2 * total + n) / (2 * n));
    for (const name of names) {
      const bal = (paid.get(name) ?? 0) - share;
      balancesList.append(el("li", {}, `${name}: ${fmtSigned(bal)}`));
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
    showError("Please enter a valid amount greater than zero, like 12.50.");
    return;
  }
  expenses.push({ name, cents, desc: descInput.value.trim() });
  saveExpenses(expenses);
  amountInput.value = "";
  descInput.value = "";
  clearError();
  render();
}

addButton.addEventListener("click", addExpense);
for (const input of [nameInput, amountInput, descInput]) {
  input.addEventListener("keydown", (ev: KeyboardEvent) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      addExpense();
    }
  });
}

render();
