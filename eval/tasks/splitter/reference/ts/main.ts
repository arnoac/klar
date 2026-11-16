interface Expense { name: string; cents: number; desc: string }

const KEY = "splitter.expenses";
let expenses: Expense[] = load();
let error = "";

function load(): Expense[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(v) ? (v as Expense[]) : [];
  } catch {
    return [];
  }
}

const money = (c: number) => `${Math.floor(Math.abs(c) / 100)}.${String(Math.abs(c) % 100).padStart(2, "0")}`;
const signed = (c: number) => (c > 0 ? "+" : c < 0 ? "-" : "") + money(c);

function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Record<string, string> = {}, ...kids: (Node | string)[]) {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...kids);
  return e;
}

const app = document.getElementById("app")!;
const name = el("input", { id: "name", placeholder: "Who paid" });
const amount = el("input", { id: "amount", placeholder: "Amount" });
const desc = el("input", { id: "desc", placeholder: "For what" });
const form = el("form", {}, name, amount, desc, el("button", { id: "add", type: "submit" }, "Add"));
const errorBox = el("div");
const list = el("ul", { id: "expenses" });
const totalBox = el("p", { id: "total" });
const balances = el("ul", { id: "balances" });
app.append(form, errorBox, list, totalBox, balances);

form.addEventListener("submit", ev => {
  ev.preventDefault();
  const who = name.value.trim();
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(amount.value.trim());
  const cents = m ? Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0")) : 0;
  if (who === "") error = "Enter who paid.";
  else if (cents <= 0) error = "Enter an amount such as 12.50.";
  else {
    error = "";
    expenses.push({ name: who, cents, desc: desc.value.trim() });
    amount.value = "";
    desc.value = "";
  }
  render();
});

function render(): void {
  localStorage.setItem(KEY, JSON.stringify(expenses));
  errorBox.replaceChildren(...(error ? [el("p", { id: "error" }, error)] : []));
  list.replaceChildren(...expenses.map((x, i) => {
    const remove = el("button", { className: "remove", type: "button" }, "Remove");
    remove.addEventListener("click", () => { expenses.splice(i, 1); render(); });
    return el("li", {}, `${x.name} paid ${money(x.cents)}${x.desc ? ` for ${x.desc}` : ""} `, remove);
  }));
  const total = expenses.reduce((s, x) => s + x.cents, 0);
  totalBox.textContent = `Total: ${money(total)}`;
  const paid = new Map<string, number>();
  for (const x of expenses) paid.set(x.name, (paid.get(x.name) ?? 0) + x.cents);
  const people = [...paid.keys()].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  const share = people.length ? Math.floor((2 * total + people.length) / (2 * people.length)) : 0;
  balances.replaceChildren(...people.map(p => el("li", {}, `${p}: ${signed(paid.get(p)! - share)}`)));
}

render();
