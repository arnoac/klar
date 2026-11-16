// Shared expense splitter. Runs entirely in the page; expenses live in localStorage.
// Money is handled as integer cents to avoid floating point errors.

const STORAGE_KEY = "splitter.expenses";

interface Expense {
  payer: string;
  cents: number;
  desc: string;
}

// Digits, optionally followed by "." and one or two digits.
const AMOUNT_RE = /^\d+(\.\d{1,2})?$/;

function parseAmount(raw: string): number | null {
  const text = raw.trim();
  if (!AMOUNT_RE.test(text)) return null;
  const [whole, frac = ""] = text.split(".");
  const cents = Number(whole) * 100 + Number((frac + "00").slice(0, 2));
  if (!Number.isSafeInteger(cents) || cents <= 0) return null;
  return cents;
}

function money(cents: number): string {
  const whole = Math.floor(cents / 100);
  const frac = String(cents % 100).padStart(2, "0");
  return `${whole}.${frac}`;
}

function signedMoney(cents: number): string {
  if (cents === 0) return "0.00";
  return (cents > 0 ? "+" : "-") + money(Math.abs(cents));
}

// Compare strings by code point (uppercase letters sort before lowercase).
function compareCodePoints(a: string, b: string): number {
  const x = Array.from(a);
  const y = Array.from(b);
  const n = Math.min(x.length, y.length);
  for (let i = 0; i < n; i++) {
    const p = x[i].codePointAt(0) ?? 0;
    const q = y[i].codePointAt(0) ?? 0;
    if (p !== q) return p - q;
  }
  return x.length - y.length;
}

function isExpense(value: unknown): value is Expense {
  if (typeof value !== "object" || value === null) return false;
  const e = value as Record<string, unknown>;
  return (
    typeof e.payer === "string" &&
    typeof e.cents === "number" &&
    Number.isSafeInteger(e.cents) &&
    e.cents > 0 &&
    typeof e.desc === "string"
  );
}

function loadExpenses(): Expense[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const data: unknown = JSON.parse(raw);
    if (!Array.isArray(data)) return [];
    return data.filter(isExpense);
  } catch {
    return [];
  }
}

function saveExpenses(list: Expense[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {
    // Storage may be unavailable (private mode, blocked); the page still works.
  }
}

// Balance per person: paid minus an equal share of the total (rounded half up to a cent).
function computeBalances(list: Expense[]): Map<string, number> {
  const paid = new Map<string, number>();
  let total = 0;
  for (const e of list) {
    paid.set(e.payer, (paid.get(e.payer) ?? 0) + e.cents);
    total += e.cents;
  }
  const people = paid.size;
  const balances = new Map<string, number>();
  if (people === 0) return balances;
  // floor(total / people + 1/2), computed exactly with integers.
  const share = Math.floor((2 * total + people) / (2 * people));
  for (const [name, amount] of paid) {
    balances.set(name, amount - share);
  }
  return balances;
}

function make<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  id?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (id !== undefined) node.id = id;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setup(): void {
  const app = document.getElementById("app");
  if (app === null) return;

  let expenses = loadExpenses();
  let errorEl: HTMLElement | null = null;

  const heading = make("h1", undefined, "Splitter");

  const nameInput = make("input", "name");
  nameInput.type = "text";
  nameInput.placeholder = "Who paid";
  nameInput.autocomplete = "off";

  const amountInput = make("input", "amount");
  amountInput.type = "text";
  amountInput.placeholder = "Amount, e.g. 12.50";
  amountInput.autocomplete = "off";

  const descInput = make("input", "desc");
  descInput.type = "text";
  descInput.placeholder = "What for (optional)";
  descInput.autocomplete = "off";

  const addButton = make("button", "add", "Add expense");
  addButton.type = "button";

  const form = make("div");
  form.className = "form";
  form.append(nameInput, amountInput, descInput, addButton);

  const formSlot = make("div");
  formSlot.className = "slot";

  const expensesHeading = make("h2", undefined, "Expenses");
  const expensesList = make("ul", "expenses");
  const balancesHeading = make("h2", undefined, "Balances");
  const balancesList = make("ul", "balances");
  const total = make("p", "total");

  app.append(heading, form, formSlot, expensesHeading, expensesList, total, balancesHeading, balancesList);

  function showError(message: string): void {
    if (errorEl === null) {
      errorEl = make("p", "error");
      formSlot.append(errorEl);
    }
    errorEl.textContent = message;
  }

  function clearError(): void {
    if (errorEl !== null) {
      errorEl.remove();
      errorEl = null;
    }
  }

  function render(): void {
    expensesList.replaceChildren();
    expenses.forEach((e, index) => {
      const li = make("li");
      const label = e.desc === "" ? `${e.payer} paid ${money(e.cents)}` : `${e.payer} paid ${money(e.cents)} for ${e.desc}`;
      li.append(make("span", undefined, label));
      li.append(" ");
      const remove = make("button", undefined, "Remove");
      remove.type = "button";
      remove.className = "remove";
      remove.addEventListener("click", () => {
        expenses = expenses.filter((_, i) => i !== index);
        saveExpenses(expenses);
        render();
      });
      li.append(remove);
      expensesList.append(li);
    });

    const sum = expenses.reduce((acc, e) => acc + e.cents, 0);
    total.textContent = `Total: ${money(sum)}`;

    balancesList.replaceChildren();
    const balances = computeBalances(expenses);
    const names = Array.from(balances.keys()).sort(compareCodePoints);
    for (const name of names) {
      const li = make("li", undefined, `${name}: ${signedMoney(balances.get(name) ?? 0)}`);
      balancesList.append(li);
    }
  }

  function addExpense(): void {
    const payer = nameInput.value.trim();
    if (payer === "") {
      showError("Enter the name of the person who paid.");
      return;
    }
    const cents = parseAmount(amountInput.value);
    if (cents === null) {
      showError("Enter an amount greater than zero, such as 12.50.");
      return;
    }
    expenses.push({ payer, cents, desc: descInput.value.trim() });
    saveExpenses(expenses);
    amountInput.value = "";
    descInput.value = "";
    clearError();
    render();
  }

  addButton.addEventListener("click", addExpense);
  for (const input of [nameInput, amountInput, descInput]) {
    input.addEventListener("keydown", (event: KeyboardEvent) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addExpense();
      }
    });
  }

  render();
}

setup();
