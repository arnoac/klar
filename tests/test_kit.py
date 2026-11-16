"""std.kit: components render the same on every backend, carry their accessibility, and behave in a browser."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_lessons import runners, write  # noqa: E402
from test_web import EXAMPLES, NODE, ROOT, klar  # noqa: E402

SHOWCASE = os.path.join(EXAMPLES, "apps", "showcase", "app.klar")

RENDER = '''use std.html { Html, input, button, text, html_render, on_click }
use std.kit { Outline, Small, Info, Danger, Hint, Problem, Choice, Tab, Toast, Crumb, Dark, Square, Theme, default_theme,
  kit_theme, variant, size, icon_button, field, field_note, select_box, checkbox, toggle, badge, alert, progress_bar,
  dialog, tabs, breadcrumbs, avatar, toasts }

enum Msg { Go, Pick(id: str), Shut, Gone(id: int), Typed(s: str), On(b: bool) }

fn show(h: Html[Msg]) uses io { print(html_render(h)) }

fn main() uses io {
  show(button([variant(Outline), size(Small), on_click(Go)], [text("Cancel")]))
  show(icon_button([], "Close", "×"))
  show(field("Email address", input([])))
  show(field_note("Email", input([]), Problem("Enter an address")))
  show(field_note("Name", input([]), Hint("Your full name")))
  show(select_box([], [Choice("a", "Ada"), Choice("b", "Bob")], "b", fn(v) => Typed(v)))
  show(toggle("Emails", "", true, fn(b) => On(b)))
  show(badge([], Info, "New"))
  show(alert([], Danger, "Failed", [text("Try again")]))
  show(progress_bar("Upload", 140))
  show(dialog(false, "Hidden", "", Shut, [], []))
  show(tabs([], "two", [Tab("one", "One", text("1")), Tab("two", "Two", text("2"))], fn(id) => Pick(id)))
  show(breadcrumbs([Crumb("Home", "/"), Crumb("Boards", "")]))
  show(avatar("ada  lovelace byron"))
  show(toasts([Toast(3, Info, "Saved", "")], fn(id) => Gone(id)))
  print(kit_theme(default_theme() with (accent: "#facc15", corners: Square)))
  print(kit_theme(default_theme() with (accent: "#1d4ed8")))
  print(len(kit_theme(default_theme() with (scheme: Dark))) > 500)
}
'''

EXPECTED = '''<button class="v-outline s-small">Cancel</button>
<button aria-label="Close" data-tip="Close" type="button" class="v-ghost icon-button">×</button>
<div class="field"><label for="field-email-address">Email address</label><input id="field-email-address"></div>
<div class="field"><label for="field-email">Email</label><input aria-describedby="field-email-note" aria-invalid="true" id="field-email"><p id="field-email-note" role="alert" class="field-note problem">Enter an address</p></div>
<div class="field"><label for="field-name">Name</label><input aria-describedby="field-name-note" id="field-name"><p id="field-name-note" class="field-note">Your full name</p></div>
<select><option value="a">Ada</option><option value="b" selected>Bob</option></select>
<label class="check"><input type="checkbox" checked role="switch">Emails</label>
<span class="badge t-info">New</span>
<div role="alert" class="alert t-danger"><div class="alert-title">Failed</div><div class="alert-body">Try again</div></div>
<div role="progressbar" aria-label="Upload" aria-valuemin="0" aria-valuemax="100" aria-valuenow="100" class="progress"><div style="width: 100%"></div></div>

<div class="tabs"><div role="tablist" class="tab-list"><button role="tab" type="button" id="tab-one" aria-selected="false" aria-controls="tab-panel-one" tabindex="-1">One</button><button role="tab" type="button" id="tab-two" aria-selected="true" aria-controls="tab-panel-two" tabindex="0">Two</button></div><div role="tabpanel" id="tab-panel-two" aria-labelledby="tab-two">2</div></div>
<nav aria-label="Breadcrumb"><ol class="crumbs"><li><a href="/">Home</a></li><li><span aria-current="page">Boards</span></li></ol></nav>
<span aria-label="ada  lovelace byron" title="ada  lovelace byron" class="avatar">AL</span>
<div aria-live="polite" class="toasts"><div role="status" class="alert t-info"><div class="alert-title">Saved</div><button type="button" aria-label="Dismiss" class="v-ghost s-small icon-button dismiss">×</button></div></div>
:root { --radius: 0px; --accent: #facc15; --accent-fg: #09090b; }
@media (prefers-color-scheme: dark) { :root { --accent: #facc15; --accent-fg: #09090b; } }

:root { --radius: 0.5rem; --accent: #1d4ed8; --accent-fg: #fafafa; }
@media (prefers-color-scheme: dark) { :root { --accent: #1d4ed8; --accent-fg: #fafafa; } }

true
'''

BROWSER = r'''
const { chromium } = require("playwright");
const assert = (ok, what) => { if (!ok) { console.error("FAILED: " + what); process.exit(1); } };
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  await page.goto("file://" + process.argv[2]);
  // tabs: click, then arrow keys move the selection
  await page.click("[role=tab]:has-text('Billing')"); await page.waitForTimeout(60);
  assert(await page.isVisible("text=Business: SSO and audit logs"), "billing tab shows its panel");
  await page.focus("[role=tab][aria-selected=true]"); await page.waitForTimeout(60);
  await page.keyboard.press("ArrowRight"); await page.waitForTimeout(60);
  assert(await page.getAttribute("#tab-account", "aria-selected") === "true", "arrow right wraps to the first tab");
  // field with a problem: typing a bad email marks it invalid
  await page.fill("#field-email", "nope"); await page.waitForTimeout(60);
  assert(await page.getAttribute("#field-email", "aria-invalid") === "true", "bad email is invalid");
  assert(await page.isVisible("text=Enter an address like name@example.com"), "problem text shows");
  await page.fill("#field-email", "ada@example.com"); await page.waitForTimeout(60);
  assert(await page.getAttribute("#field-email", "aria-invalid") === null, "good email is valid again");
  // busy button, then a toast that can be dismissed
  await page.click("text=Save changes"); await page.waitForTimeout(60);
  assert(await page.getAttribute("button[type=submit]", "aria-busy") === "true", "saving shows busy");
  await page.waitForSelector(".toasts >> text=Profile saved");
  await page.click(".toasts [aria-label=Dismiss]"); await page.waitForTimeout(60);
  assert(await page.locator(".toasts .alert").count() === 0, "toast dismissed");
  // menu: opens, closes on Escape, closes after a pick
  await page.click("summary"); await page.waitForTimeout(60);
  assert(await page.isVisible("[role=menu]"), "menu opens");
  await page.keyboard.press("Escape"); await page.waitForTimeout(60);
  assert(!(await page.isVisible("[role=menu]")), "Escape closes the menu");
  await page.click("summary"); await page.waitForTimeout(60);
  await page.click("[role=menuitem]:has-text('Delete workspace')"); await page.waitForTimeout(60);
  assert(!(await page.isVisible("[role=menu]")), "picking closes the menu");
  // dialog: opened by the menu item, focused, closed by Escape and by a click outside
  await page.waitForSelector("[role=dialog]");
  assert(await page.evaluate(() => document.activeElement.getAttribute("role")) === "dialog", "dialog takes focus");
  await page.keyboard.press("Escape"); await page.waitForTimeout(60);
  assert(await page.locator("[role=dialog]").count() === 0, "Escape closes the dialog");
  await page.click("text=Billing >> nth=0"); await page.waitForTimeout(60);
  await page.click(".panel-foot button:has-text('Delete workspace')"); await page.waitForTimeout(60);
  await page.mouse.click(10, 10); await page.waitForTimeout(60);
  assert(await page.locator("[role=dialog]").count() === 0, "a click outside closes the dialog");
  // switch toggles
  await page.click("[role=tab]:has-text('Notifications')"); await page.waitForTimeout(60);
  const sw = page.locator("[role=switch]").nth(1);
  assert(!(await sw.isChecked()), "weekly starts off");
  await sw.click(); await page.waitForTimeout(60);
  assert(await sw.isChecked(), "switch turns on");
  // dialog keeps Tab inside and gives focus back when it closes
  await page.click("[role=tab]:has-text('Billing')"); await page.waitForTimeout(60);
  await page.click(".panel-foot button:has-text('Delete workspace')"); await page.waitForTimeout(60);
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    assert(await page.evaluate(() => !!document.activeElement.closest("[role=dialog]")), "Tab stays inside the dialog");
  }
  await page.keyboard.press("Shift+Tab");
  assert(await page.evaluate(() => !!document.activeElement.closest("[role=dialog]")), "Shift+Tab stays inside the dialog");
  await page.keyboard.press("Escape"); await page.waitForTimeout(60);
  assert(await page.evaluate(() => document.activeElement.textContent) === "Delete workspace", "focus returns to the opener");
  // data table: sort by a header, reverse it, page through
  const firstName = () => page.textContent(".data-table tbody tr:first-child td:first-child");
  await page.click("th button:has-text('Cards')"); await page.waitForTimeout(60);
  assert((await firstName()).includes("Alan Turing"), "fewest cards first");
  assert(await page.getAttribute("th:has-text('Cards')", "aria-sort") === "ascending", "aria-sort ascending");
  await page.click("th button:has-text('Cards')"); await page.waitForTimeout(60);
  assert((await firstName()).includes("Grace Hopper"), "most cards first");
  assert(await page.isVisible(".pager >> text=1–4 of 6"), "pager shows the range");
  await page.click(".pager button:has-text('Next')"); await page.waitForTimeout(60);
  assert(await page.locator(".data-table tbody tr").count() === 2, "second page has the rest");
  assert(await page.isDisabled(".pager button:has-text('Next')"), "no page after the last");
  // combobox: type, arrow down, Enter picks; Escape closes
  await page.click("[role=tab]:has-text('Account')"); await page.waitForTimeout(60);
  await page.fill("[role=combobox]", "america"); await page.waitForTimeout(60);
  assert(await page.locator("[role=option]").count() === 3, "three American zones");
  await page.keyboard.press("ArrowDown"); await page.waitForTimeout(60);
  assert(await page.getAttribute("[role=combobox]", "aria-activedescendant") === "field-time-zone-1", "arrow moves the highlight");
  await page.keyboard.press("Enter"); await page.waitForTimeout(60);
  assert(await page.inputValue("[role=combobox]") === "America/Chicago", "Enter picks the highlighted zone");
  assert(await page.locator("[role=option]").count() === 0, "the list closes after a pick");
  await page.fill("[role=combobox]", "tok"); await page.waitForTimeout(60);
  await page.click("[role=option]:has-text('Asia/Tokyo')"); await page.waitForTimeout(60);
  assert(await page.inputValue("[role=combobox]") === "Asia/Tokyo", "a click picks");
  // a key pressed right after typing, before the next frame, acts on the new text
  await page.evaluate(() => { const i = document.querySelector("[role=combobox]"); i.value = "eu"; i.dispatchEvent(new Event("input", { bubbles: true }));
    i.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })); });
  await page.waitForTimeout(60);
  assert(await page.inputValue("[role=combobox]") === "eu", "typing is not undone by a quick key press");
  await page.keyboard.press("Escape"); await page.waitForTimeout(60);
  assert(await page.locator("[role=option]").count() === 0, "Escape closes the list");
  // contrast: primary button text is readable in both schemes
  for (const scheme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: scheme }); await page.waitForTimeout(60);
    const [fg, bg] = await page.evaluate(() => { const s = getComputedStyle(document.querySelector("button[type=submit], .panel-foot button")); return [s.color, s.backgroundColor]; });
    assert(fg !== bg, "button text differs from its background in " + scheme);
  }
  assert(errors.length === 0, "no page errors: " + errors.join(" | "));
  console.log("ok");
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
'''


class Kit(unittest.TestCase):
    def test_components_render_the_same_on_every_backend(self):
        path = os.path.join(write({"k.klar": RENDER}), "k.klar")
        env = dict(os.environ, PYTHONPATH=ROOT)
        for name, cmd in runners(path).items():
            with self.subTest(backend=name):
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.maxDiff = None
                self.assertEqual(r.stdout, EXPECTED)

    def test_the_showcase_tests_pass(self):
        r = klar("test", SHOWCASE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_type_parameters_work_in_local_annotations(self):
        src = ('fn wrap[T](x: T) -> list[T] {\n  let out: list[T] = [x]\n  let f = fn(y: T) => y\n  return push(out, f(x))\n}\n'
               'fn main() uses io { print(len(wrap(1))) }\n')
        r = klar("run", os.path.join(write({"g.klar": src}), "g.klar"))
        self.assertEqual(r.stdout, "2\n", r.stderr)

    @unittest.skipUnless(NODE, "node is not installed")
    def test_components_behave_in_the_browser(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        html = os.path.join(d, "app.html")
        b = klar("build", "--target", "web", SHOWCASE, "-o", html)
        self.assertEqual(b.returncode, 0, b.stderr)
        script = os.path.join(d, "drive.js")
        with open(script, "w") as f:
            f.write(BROWSER)
        r = subprocess.run([NODE, script, html], capture_output=True, text=True, timeout=120)
        if r.returncode != 0 and "Cannot find module 'playwright'" in r.stderr:
            self.skipTest("playwright is not installed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
