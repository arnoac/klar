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
        item !== null &&
        typeof item === "object" &&
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

/** Parse an amount string into cents, or null when invalid. */
export function parseAmount(input: string): number | null {
  const s = input.trim();
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(s);
  if (!m) return null;
  const whole = Number(m[1]);
  const frac = m[2] === undefined ? 0 : Number(m[2].padEnd(2, "0"));
  const cents = whole * 100 + frac;
  if (!Number.isSafeInteger(cents) || cents <= 0) return null;
  return cents;
}

function fmt(cents: number): string {
  const neg = cents < 0;
  const abs = Math.abs(cents);
  const whole = Math.floor(abs / 100);
  const frac = abs % 100;
  return (neg ? "-" : "") + String(whole) + "." + String(frac).padStart(2, "0");
}

function fmtSigned(cents: number): string {
  if (cents > 0) return "+" + fmt(cents);
  if (cents < 0) return fmt(cents);
  return "0.00";
}

function compareCodePoints(a: string, b: string): number {
  const ia = a[Symbol.iterator]();
  const ib = b[Symbol.iterator]();
  for (;;) {
    const x = ia.next();
    const y = ib.next();
    if (x.done) return y.done ? 0 : -1;
    if (y.done) return 1;
    const cx = x.value.codePointAt(0) ?? 0;
    const cy = y.value.codePointAt(0) ?? 0;
    if (cx !== cy) return cx < cy ? -1 : 1;
  }
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string> = {}, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}

function main(): void {
  const app = document.getElementById("app") ?? document.body;
  let expenses = loadExpenses();

  const style = el("style");
  style.textContent = `
    body { font-family: system-ui, sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; }
    .row { display: flex; gap: .5rem; flex-wrap: wrap; margin-bottom: .75rem; }
    .row input { padding: .4rem; }
    #error { color: #b00020; margin: .5rem 0; }
    ul { padding-left: 1.2rem; }
    li { margin: .25rem 0; }
    .remove { margin-left: .5rem; }
  `;
  document.head.appendChild(style);

  const title = el("h1", {}, "Splitter");
  const form = el("div", { class: "row" });
  const nameIn = el("input", { id: "name", type: "text", placeholder: "Who paid", autocomplete: "off" });
  const amountIn = el("input", { id: "amount", type: "text", placeholder: "Amount", inputmode: "decimal", autocomplete: "off" });
  const descIn = el("input", { id: "desc", type: "text", placeholder: "Description", autocomplete: "off" });
  const addBtn = el("button", { id: "add", type: "button" }, "Add");
  form.append(nameIn, amountIn, descIn, addBtn);

  const errorSlot = el("div");
  const expTitle = el("h2", {}, "Expenses");
  const expList = el("ul", { id: "expenses" });
  const totalEl = el("p", { id: "total" });
  const balTitle = el("h2", {}, "Balances");
  const balList = el("ul", { id: "balances" });

  app.append(title, form, errorSlot, expTitle, expList, totalEl, balTitle, balList);

  function showError(msg: string): void {
    let e = document.getElementById("error");
    if (!e) {
      e = el("div", { id: "error", role: "alert" });
      errorSlot.appendChild(e);
    }
    e.textContent = msg;
  }

  function clearError(): void {
    const e = document.getElementById("error");
    if (e) e.remove();
  }

  function render(): void {
    expList.replaceChildren();
    expenses.forEach((exp, idx) => {
      const li = el("li");
      const text = exp.name + " paid " + fmt(exp.cents) + (exp.desc !== "" ? " for " + exp.desc : "");
      li.appendChild(document.createTextNode(text + " "));
      const btn = el("button", { class: "remove", type: "button", "aria-label": "Remove expense" }, "\u2715");
      btn.addEventListener("click", () => {
        expenses.splice(idx, 1);
        saveExpenses(expenses);
        render();
      });
      li.appendChild(btn);
      expList.appendChild(li);
    });

    let total = 0;
    const paid = new Map<string, number>();
    for (const exp of expenses) {
      total += exp.cents;
      paid.set(exp.name, (paid.get(exp.name) ?? 0) + exp.cents);
    }
    totalEl.textContent = "Total: " + fmt(total);

    balList.replaceChildren();
    const names = [...paid.keys()].sort(compareCodePoints);
    if (names.length > 0) {
      const n = names.length;
      // round half up to the nearest cent (total is non-negative)
      const share = Math.floor((2 * total + n) / (2 * n));
      for (const name of names) {
        const bal = (paid.get(name) ?? 0) - share;
        balList.appendChild(el("li", {}, name + ": " + fmtSigned(bal)));
      }
    }
  }

  function add(): void {
    const name = nameIn.value.trim();
    const cents = parseAmount(amountIn.value);
    if (name === "") {
      showError("Please enter who paid.");
      return;
    }
    if (cents === null) {
      showError("Please enter a valid amount greater than zero (e.g. 12.50).");
      return;
    }
    expenses.push({ name, cents, desc: descIn.value.trim() });
    saveExpenses(expenses);
    amountIn.value = "";
    descIn.value = "";
    clearError();
    render();
  }

  addBtn.addEventListener("click", add);
  for (const input of [nameIn, amountIn, descIn]) {
    input.addEventListener("keydown", (ev: KeyboardEvent) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        add();
      }
    });
  }

  render();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", main);
} else {
  main();
}
