"""std.layout: containers, grids and typed options render the same everywhere and really lay out in a browser."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_lessons import runners, write  # noqa: E402
from test_web import NODE, ROOT, klar  # noqa: E402

PAGE = '''use std.html { Html, text, p, h3, id, html_page }
use std.layout { Zero, Sm, Lg, Tablet, Laptop, Desktop, Center, Between, Narrow, Wide, Rem,
  container, grid, row, stack, spread, center, box, spacer, sidebar,
  gap, pad, cols, cols_at, fit_cols, col_span_at, full_row, align, justify, max_width, side_width, centered, grow,
  no_wrap, row_at, hide_from, show_from }

fn tile(name: str) -> Html[unit] { return box([id(name)], [text(name)]) }

fn page() -> Html[unit] {
  return container([max_width(Wide), id("page")], [
    grid([id("g"), cols(1), cols_at(Tablet, 2), cols_at(Laptop, 4), gap(Sm)], [
      box([id("wide"), col_span_at(Laptop, 2)], [text("wide")]), tile("b"), tile("c"), box([id("all"), full_row()], [text("all")]),
      box([], [grid([id("inner")], [tile("i1"), tile("i2")])]),
    ]),
    grid([id("fit"), fit_cols(10)], [tile("f1"), tile("f2"), tile("f3")]),
    row([id("r"), justify(Between), align(Center), pad(Lg), no_wrap()], [tile("r1"), spacer(), box([id("g1"), grow(), max_width(Rem(16))], [])]),
    stack([id("s"), row_at(Tablet), gap(Zero)], [tile("s1"), tile("s2")]),
    box([id("phone-only"), hide_from(Tablet)], [text("menu")]),
    box([id("desk-only"), show_from(Desktop)], [text("wide screens")]),
    sidebar([id("sb"), side_width(12)], tile("side"), tile("main")),
    box([id("narrow"), max_width(Narrow), centered()], [text("narrow")]),
    center([id("ctr")], [p([], [text("empty")])]),
  ])
}

fn main() uses io { print(html_page("layout", "", page())) }
'''

# For each viewport width: what the browser must compute.
PROBE = r'''
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto("file://" + process.argv[2]);
  const out = {};
  for (const w of [390, 800, 1100, 1400]) {
    await page.setViewportSize({ width: w, height: 900 });
    out[w] = await page.evaluate(() => {
      const $ = id => document.getElementById(id);
      const cols = id => getComputedStyle($(id)).gridTemplateColumns.split(" ").filter(t => t !== "0px").length;
      const shown = id => getComputedStyle($(id)).display !== "none";
      const r = id => $(id).getBoundingClientRect();
      return {
        g: cols("g"), inner: cols("inner"), fit: cols("fit"),
        wide: Math.round(r("wide").width / r("b").width * 10) / 10,
        all: Math.round(r("all").width) === Math.round(r("g").width),
        s: getComputedStyle($("s")).flexDirection, sGap: getComputedStyle($("s")).rowGap,
        phone: shown("phone-only"), desk: shown("desk-only"),
        sideW: Math.round(r("side").width), sideBeside: r("main").top === r("side").top,
        narrowW: Math.round(r("narrow").width), narrowCentred: Math.abs((r("narrow").left - r("page").left) - (r("page").right - r("narrow").right)) < 2,
        g1W: Math.round(r("g1").width), rJustify: getComputedStyle($("r")).justifyContent, rPad: getComputedStyle($("r")).paddingTop,
        pageW: Math.round(r("page").width),
      };
    });
  }
  console.log(JSON.stringify(out));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
'''


class Layout(unittest.TestCase):
    def test_same_html_from_every_backend(self):
        path = os.path.join(write({"l.klar": PAGE}), "l.klar")
        outs = {}
        env = dict(os.environ, PYTHONPATH=ROOT)
        for name, cmd in runners(path).items():
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            outs[name] = r.stdout
        self.assertEqual(len(set(outs.values())), 1, list(outs))
        html = outs["interp"]
        # options become classes, and responsive options become CSS variables joined into one style attribute
        self.assertIn('class="k-gap-sm grid" style="--cols: repeat(1, minmax(0, 1fr)); '
                      '--cols-tablet: repeat(2, minmax(0, 1fr)); --cols-laptop: repeat(4, minmax(0, 1fr))"', html)
        self.assertIn('<div id="page" class="k-w-wide container">', html)

    def test_typos_are_compile_errors(self):
        src = 'use std.html { Html, text }\nuse std.layout { grid, gap, Small }\nfn main() uses io { print(1) }\n'
        r = klar("check", os.path.join(write({"t.klar": src}), "t.klar"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Small", r.stderr + r.stdout)

    @unittest.skipUnless(NODE, "node is not installed")
    def test_the_browser_lays_it_out_at_every_screen_width(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        src = os.path.join(d, "l.klar")
        with open(src, "w") as f:
            f.write(PAGE)
        r = klar("run", src)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(d, "page.html"), "w") as f:
            f.write(r.stdout)
        with open(os.path.join(d, "probe.js"), "w") as f:
            f.write(PROBE)
        p = subprocess.run([NODE, os.path.join(d, "probe.js"), os.path.join(d, "page.html")], capture_output=True,
                           text=True, timeout=120)
        if p.returncode != 0 and "Cannot find module 'playwright'" in p.stderr:
            self.skipTest("playwright is not installed")
        self.assertEqual(p.returncode, 0, p.stderr)
        got = {int(k): v for k, v in json.loads(p.stdout).items()}
        self.maxDiff = None
        phone, tablet, laptop, desktop = got[390], got[800], got[1100], got[1400]
        # cols / cols_at: phone first, then wider screens override
        self.assertEqual([phone["g"], tablet["g"], laptop["g"], desktop["g"]], [1, 2, 4, 4])
        # col_span_at(Laptop, 2) only spans from laptop up; full_row always covers the row
        self.assertEqual([phone["wide"], tablet["wide"], laptop["wide"]], [1.0, 1.0, 2.0])
        self.assertTrue(all(v["all"] for v in got.values()))
        # a nested grid does not inherit its parent's column count
        self.assertEqual(laptop["inner"], 1)
        # fit_cols(10): as many 10rem columns as fit
        self.assertEqual(phone["fit"], 2)
        self.assertEqual(desktop["fit"], 3)
        # row_at(Tablet): a stack on phones, a row from tablet up
        self.assertEqual([phone["s"], tablet["s"]], ["column", "row"])
        self.assertEqual(phone["sGap"], "0px")
        # hide_from / show_from
        self.assertEqual([phone["phone"], tablet["phone"]], [True, False])
        self.assertEqual([laptop["desk"], desktop["desk"]], [False, True])
        # sidebar: stacked on phones, a 12rem side column from tablet up
        self.assertFalse(phone["sideBeside"])
        self.assertTrue(tablet["sideBeside"])
        self.assertEqual(tablet["sideW"], 192)
        # max_width + centered; max_width(Wide) caps the container; Rem(n) caps an element
        self.assertEqual(desktop["narrowW"], 480)
        self.assertTrue(desktop["narrowCentred"])
        self.assertEqual(desktop["pageW"], 1152)
        self.assertEqual(desktop["g1W"], 256)
        self.assertEqual(desktop["rJustify"], "space-between")
        self.assertEqual(desktop["rPad"], "24px")


if __name__ == "__main__":
    unittest.main()
