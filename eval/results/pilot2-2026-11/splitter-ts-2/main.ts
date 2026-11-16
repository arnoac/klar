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
  const whole = Number(m[1]);
  const frac = m[2] === undefined ? 0 : Number(m[2].padEnd(2, "0"));
  const cents = whole * 100 + frac;
  if (!Number.isSafeInteger(cents) || cents <= 0) return null;
  return cents;
}

function compareCodePoints(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

let expenses: Expense[] = loadExpenses();

const app = document.getElementById("app") ?? document.body;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> = {}): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  Object.assign(e, props);
  return e;
}

const form = el("div");
const nameInput = el("input", { id: "name", type: "text", placeholder: "Who paid?" });
const amountInput = el("input", { id: "amount", type: "text", placeholder: "Amount (e.g. 12.50)" });
const descInput = el("input", { id: "desc", type: "text", placeholder: "Description (optional)" });
const addButton = el("button", { id: "add", type: "button", textContent: "Add" });
form.append(nameInput, amountInput, descInput, addButton);

const errorSlot = el("div");
const expensesList = el("ul", { id: "expenses" });
const totalEl = el("p", { id: "total" });
const balancesList = el("ul", { id: "balances" });

const expHeading = el("h2", { textContent: "Expenses" });
const balHeading = el("h2", { textContent: "Balances" });
app.append(el("h1", { textContent: "Splitter" }), form, errorSlot, expHeading, expensesList, totalEl, balHeading, balancesList);

function showError(msg: string): void {
  let err = document.getElementById("error");
  if (!err) {
    err = el("p", { id: "error" });
    err.style.color = "#b00020";
    errorSlot.append(err);
  }
  err.textContent = msg;
}

function clearError(): void {
  document.getElementById("error")?.remove();
}

function render(): void {
  expensesList.replaceChildren();
  expenses.forEach((exp, index) => {
    const li = el("li");
    const text = exp.desc
      ? `${exp.name} paid ${formatCents(exp.cents)} for ${exp.desc}`
      : `${exp.name} paid ${formatCents(exp.cents)}`;
    li.append(document.createTextNode(text + " "));
    const rm = el("button", { type: "button", className: "remove", textContent: "Remove" });
    rm.addEventListener("click", () => {
      expenses.splice(index, 1);
      saveExpenses(expenses);
      render();
    });
    li.append(rm);
    expensesList.append(li);
  });

  let total = 0;
  const paid = new Map<string, number>();
  for (const exp of expenses) {
    total += exp.cents;
    paid.set(exp.name, (paid.get(exp.name) ?? 0) + exp.cents);
  }
  totalEl.textContent = `Total: ${formatCents(total)}`;

  balancesList.replaceChildren();
  const names = [...paid.keys()].sort(compareCodePoints);
  const n = names.length;
  if (n > 0) {
    // round(total / n), half up, using integer arithmetic
    const share = Math.floor((2 * total + n) / (2 * n));
    for (const name of names) {
      const bal = (paid.get(name) ?? 0) - share;
      balancesList.append(el("li", { textContent: `${name}: ${formatSigned(bal)}` }));
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
  const desc = descInput.value.trim();
  expenses.push({ name, cents, desc });
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
