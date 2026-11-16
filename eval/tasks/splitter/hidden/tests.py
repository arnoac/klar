"""Hidden acceptance tests for the splitter task: the browser tests in tests.mjs, one Python test each."""

import os
import re

from checks import Fail

KIND = "web"
TESTS_MJS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests.mjs")


def _names():
    with open(TESTS_MJS) as f:
        return re.findall(r"^  async (\w+)\(", f.read(), re.M)


def _make(name):
    def test(prog):
        result = prog.browser(TESTS_MJS).get(name)
        if result is None:
            raise Fail("test did not run")
        if not result["ok"]:
            raise Fail(result["message"])
    test.__name__ = f"test_{name}"
    return test


for _n in _names():
    globals()[f"test_{_n}"] = _make(_n)
