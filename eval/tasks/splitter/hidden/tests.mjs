// Hidden browser tests for the splitter task (run by harness/browser.mjs).

// Waits up to 2 s for `read()` to give `want` (pages may render on the next animation frame), then fails.
async function eq(read, want, what) {
  const deadline = Date.now() + 2000;
  let got;
  for (;;) {
    got = typeof read === "function" ? await read() : read;
    if (JSON.stringify(got) === JSON.stringify(want)) return;
    if (Date.now() > deadline) break;
    await new Promise(r => setTimeout(r, 50));
  }
  throw new Error(`${what}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
}

async function add(page, name, amount, desc = "") {
  await page.fill("#name", name);
  await page.fill("#amount", amount);
  await page.fill("#desc", desc);
  await page.click("#add");
}

const items = (page, id) =>
  page.$$eval(`#${id} li`, lis => lis.map(li => {
    const copy = li.cloneNode(true);
    copy.querySelectorAll("button").forEach(b => b.remove());
    return copy.textContent.replace(/\s+/g, " ").trim();
  }));
const total = page => page.$eval("#total", el => el.textContent.replace(/\s+/g, " ").trim());
const hasError = async page => {
  const el = await page.$("#error");
  if (!el) return false;
  return (await el.textContent()).trim() !== "" && await el.isVisible();
};

export const tests = {
  async starts_empty(page) {
    await eq(() => items(page, "expenses"), [], "expenses");
    await eq(() => items(page, "balances"), [], "balances");
    await eq(() => total(page), "Total: 0.00", "total");
    await eq(() => hasError(page), false, "error shown at start");
  },

  async adds_an_expense(page) {
    await add(page, "Ann", "12.5", "pizza");
    await eq(() => items(page, "expenses"), ["Ann paid 12.50 for pizza"], "expenses");
    await eq(() => total(page), "Total: 12.50", "total");
    await eq(() => items(page, "balances"), ["Ann: 0.00"], "balances");
  },

  async clears_amount_and_desc_keeps_name(page) {
    await add(page, "Ann", "3", "tea");
    await eq(() => page.inputValue("#name"), "Ann", "name input");
    await eq(() => page.inputValue("#amount"), "", "amount input");
    await eq(() => page.inputValue("#desc"), "", "desc input");
    await page.fill("#amount", "2");
    await page.click("#add");
    await eq(() => items(page, "expenses"), ["Ann paid 3.00 for tea", "Ann paid 2.00"], "expenses");
  },

  async enter_key_adds(page) {
    await page.fill("#name", "Bo");
    await page.fill("#desc", "bus");
    await page.fill("#amount", "4.20");
    await page.press("#amount", "Enter");
    await eq(() => items(page, "expenses"), ["Bo paid 4.20 for bus"], "expenses after Enter in amount");
    await page.fill("#amount", "1");
    await page.press("#desc", "Enter");
    await eq(async () => (await items(page, "expenses")).length, 2, "expenses after Enter in desc");
  },

  async balances_example(page) {
    await add(page, "Ann", "30", "");
    await add(page, "Bob", "10.00", "snacks");
    await eq(() => items(page, "balances"), ["Ann: +10.00", "Bob: -10.00"], "balances");
    await eq(() => total(page), "Total: 40.00", "total");
  },

  async share_rounds_half_up(page) {
    await add(page, "a", "10");
    await add(page, "b", "0.01");
    await add(page, "c", "0.01");
    // total 10.02 / 3 = 3.34
    await eq(() => items(page, "balances"), ["a: +6.66", "b: -3.33", "c: -3.33"], "balances");
    await add(page, "d", "0.01");
    // total 10.03 / 4 = 2.5075 -> 2.51
    await eq(() => items(page, "balances"), ["a: +7.49", "b: -2.50", "c: -2.50", "d: -2.50"], "balances with 4");
  },

  async sorted_names_case_sensitive(page) {
    await add(page, "bob", "1");
    await add(page, "Bob", "1");
    await add(page, "alice", "1");
    await add(page, "Zed", "1");
    await eq(async () => (await items(page, "balances")).map(s => s.split(":")[0]), ["Bob", "Zed", "alice", "bob"], "balance order");
  },

  async trims_name_and_merges_people(page) {
    await add(page, "  Ann ", "5");
    await add(page, "Ann", "5", "  ");
    await eq(() => items(page, "expenses"), ["Ann paid 5.00", "Ann paid 5.00"], "expenses");
    await eq(() => items(page, "balances"), ["Ann: 0.00"], "balances");
  },

  async invalid_amounts_show_error(page) {
    for (const bad of ["0", "-1", "1.234", "abc", "", "1.", ".5", "1e3", "0.00"]) {
      await add(page, "Ann", bad, "x");
      await eq(() => hasError(page), true, `error shown for amount ${JSON.stringify(bad)}`);
      await eq(() => page.inputValue("#amount"), bad, `amount input kept for ${JSON.stringify(bad)}`);
    }
    await eq(() => items(page, "expenses"), [], "expenses after invalid input");
  },

  async empty_name_shows_error_then_clears(page) {
    await add(page, "   ", "5", "x");
    await eq(() => hasError(page), true, "error for empty name");
    await eq(() => items(page, "expenses"), [], "expenses");
    await add(page, "Cy", " 5 ", "x");
    await eq(() => hasError(page), false, "error after a valid add");
    await eq(() => items(page, "expenses"), ["Cy paid 5.00 for x"], "expenses");
  },

  async remove_expense(page) {
    await add(page, "Ann", "1", "a");
    await add(page, "Bob", "2", "b");
    await add(page, "Ann", "3", "c");
    await page.click("#expenses li:nth-child(2) .remove");
    await eq(() => items(page, "expenses"), ["Ann paid 1.00 for a", "Ann paid 3.00 for c"], "expenses after remove");
    await eq(() => items(page, "balances"), ["Ann: 0.00"], "Bob gone from balances");
    await eq(() => total(page), "Total: 4.00", "total");
    await page.click("#expenses li:nth-child(1) .remove");
    await page.click("#expenses li:nth-child(1) .remove");
    await eq(() => items(page, "balances"), [], "balances when empty");
    await eq(() => total(page), "Total: 0.00", "total when empty");
  },

  async survives_reload(page) {
    await add(page, "Ann", "30", "rent");
    await add(page, "Bob", "10");
    await page.reload();
    await eq(() => items(page, "expenses"), ["Ann paid 30.00 for rent", "Bob paid 10.00"], "expenses after reload");
    await eq(() => items(page, "balances"), ["Ann: +10.00", "Bob: -10.00"], "balances after reload");
    await eq(() => page.inputValue("#name"), "", "name input after reload");
    await page.click("#expenses li:nth-child(1) .remove");
    await page.reload();
    await eq(() => items(page, "expenses"), ["Bob paid 10.00"], "expenses after remove and reload");
  },

  async text_is_not_markup(page, { errors }) {
    await add(page, "<b>x</b>", "1", "<img src=x onerror=alert(1)>");
    await eq(() => items(page, "expenses"), ["<b>x</b> paid 1.00 for <img src=x onerror=alert(1)>"], "expenses");
    await eq(() => page.$$eval("#expenses img, #expenses b", els => els.length), 0, "markup elements created");
    await eq(errors, [], "page errors");
  },

  async many_expenses_and_large_amounts(page) {
    for (let i = 0; i < 12; i++) await add(page, `p${i % 4}`, `${1000000 + i}.0${i % 10}`);
    await eq(() => total(page), "Total: 12000066.46", "total");
    await eq(async () => (await items(page, "balances")).length, 4, "people");
  },
};
