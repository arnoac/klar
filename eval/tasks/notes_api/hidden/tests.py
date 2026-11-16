"""Hidden acceptance tests for the notes_api task."""

import html
import shutil
import tempfile

from checks import Fail, expect, expect_eq

TIMEOUT = 60


def create(s, title="t", body=None, tags=None):
    payload = {"title": title}
    if body is not None:
        payload["body"] = body
    if tags is not None:
        payload["tags"] = tags
    r = s.request("POST", "/notes", payload)
    expect_eq(r.status, 201, f"status of POST /notes {payload}")
    return r.json()


def expect_json(r, status, what):
    expect_eq(r.status, status, f"status of {what}")
    expect("application/json" in r.content_type(), f"{what}: Content-Type should be application/json, got "
                                                   f"{r.content_type()!r}")
    return r.json()


def expect_error(r, status, what):
    data = expect_json(r, status, what)
    expect(isinstance(data, dict) and isinstance(data.get("error"), str),
           f"{what}: expected {{\"error\": \"...\"}}, got {r.body[:200]!r}")


def test_create_and_get(prog):
    with prog.server() as s:
        n = create(s, "Groceries", "milk, eggs", ["home", "todo"])
        expect_eq(n, {"id": 1, "title": "Groceries", "body": "milk, eggs", "tags": ["home", "todo"]}, "created note")
        r = s.request("GET", "/notes/1")
        expect_eq(expect_json(r, 200, "GET /notes/1"), n, "GET /notes/1")


def test_defaults_and_trimming(prog):
    with prog.server() as s:
        n = create(s, "  Spaced  ")
        expect_eq(n, {"id": 1, "title": "Spaced", "body": "", "tags": []}, "created note")


def test_ids_increase(prog):
    with prog.server() as s:
        ids = [create(s, f"n{i}")["id"] for i in range(3)]
        expect_eq(ids, [1, 2, 3], "ids")


def test_list_sorted_by_id(prog):
    with prog.server() as s:
        for i in range(12):
            create(s, f"note {i}")
        data = expect_json(s.request("GET", "/notes"), 200, "GET /notes")
        expect_eq([n["id"] for n in data], list(range(1, 13)), "ids in list")


def test_empty_list(prog):
    with prog.server() as s:
        expect_eq(expect_json(s.request("GET", "/notes"), 200, "GET /notes"), [], "list")


def test_tags_deduplicated_in_order(prog):
    with prog.server() as s:
        n = create(s, "t", tags=["b", "a", "b", "c-1", "a"])
        expect_eq(n["tags"], ["b", "a", "c-1"], "tags")


def test_validation_errors(prog):
    bad = [{}, {"title": ""}, {"title": "   "}, {"title": 5}, {"title": "x" * 101}, {"title": "a", "body": 3},
           {"title": "a", "tags": "home"}, {"title": "a", "tags": ["Home"]}, {"title": "a", "tags": [""]},
           {"title": "a", "tags": ["a" * 21]}, {"title": "a", "tags": ["ok", 1]}, {"title": "a", "tags": ["a b"]},
           {"title": None}]
    with prog.server() as s:
        for payload in bad:
            expect_error(s.request("POST", "/notes", payload), 400, f"POST /notes {payload}")
        expect_eq(expect_json(s.request("GET", "/notes"), 200, "GET /notes"), [], "notes after rejected posts")
        expect_eq(create(s, "x" * 100)["id"], 1, "id of the first stored note")


def test_bad_json(prog):
    with prog.server() as s:
        for raw in ("{", "", "[1,2]", "\"title\"", "null"):
            expect_error(s.request("POST", "/notes", raw=raw), 400, f"POST /notes body {raw!r}")


def test_not_found(prog):
    with prog.server() as s:
        create(s, "a")
        for path in ("/notes/2", "/notes/abc", "/notes/0", "/notes/-1", "/nope", "/notes/1/extra"):
            expect_error(s.request("GET", path), 404, f"GET {path}")
        expect_error(s.request("DELETE", "/notes/9"), 404, "DELETE /notes/9")
        expect_error(s.request("PATCH", "/notes/9", {"title": "x"}), 404, "PATCH /notes/9")


def test_patch_partial(prog):
    with prog.server() as s:
        create(s, "old", "body", ["a"])
        r = s.request("PATCH", "/notes/1", {"title": " new "})
        expect_eq(expect_json(r, 200, "PATCH title"), {"id": 1, "title": "new", "body": "body", "tags": ["a"]},
                  "patched note")
        r = s.request("PATCH", "/notes/1", {"tags": ["x", "x", "y"], "body": ""})
        expect_eq(expect_json(r, 200, "PATCH tags"), {"id": 1, "title": "new", "body": "", "tags": ["x", "y"]},
                  "patched note")
        expect_eq(s.request("GET", "/notes/1").json()["tags"], ["x", "y"], "stored tags")


def test_patch_validation_changes_nothing(prog):
    with prog.server() as s:
        create(s, "keep", "b", ["t"])
        for payload in ({"title": ""}, {"tags": ["BAD"]}, {"body": None}, {"title": "ok", "tags": "x"}):
            expect_error(s.request("PATCH", "/notes/1", payload), 400, f"PATCH {payload}")
        expect_eq(s.request("GET", "/notes/1").json(), {"id": 1, "title": "keep", "body": "b", "tags": ["t"]},
                  "note after rejected patches")
        expect_error(s.request("PATCH", "/notes/1", raw="nope"), 400, "PATCH with invalid JSON")


def test_delete(prog):
    with prog.server() as s:
        create(s, "a")
        create(s, "b")
        r = s.request("DELETE", "/notes/1")
        expect_eq(r.status, 204, "status of DELETE /notes/1")
        expect_eq(r.body, "", "body of DELETE")
        expect_error(s.request("GET", "/notes/1"), 404, "GET deleted note")
        expect_eq([n["id"] for n in s.request("GET", "/notes").json()], [2], "ids after delete")
        expect_eq(create(s, "c")["id"], 3, "id after a delete")


def test_filter_by_tag(prog):
    with prog.server() as s:
        create(s, "a", tags=["work"])
        create(s, "b", tags=["home", "work"])
        create(s, "c", tags=["home"])
        got = [n["id"] for n in expect_json(s.request("GET", "/notes?tag=work"), 200, "GET ?tag=work")]
        expect_eq(got, [1, 2], "ids for tag=work")
        expect_eq(s.request("GET", "/notes?tag=none").json(), [], "notes for an unused tag")


def test_search_ignores_case(prog):
    with prog.server() as s:
        create(s, "Buy MILK")
        create(s, "other", "need milk and bread")
        create(s, "nothing")
        got = [n["id"] for n in expect_json(s.request("GET", "/notes?q=Milk"), 200, "GET ?q=Milk")]
        expect_eq(got, [1, 2], "ids for q=Milk")


def test_combined_filters_and_encoding(prog):
    with prog.server() as s:
        create(s, "tea time", tags=["home"])
        create(s, "tea at work", tags=["work"])
        create(s, "coffee", tags=["home"])
        got = [n["id"] for n in s.request("GET", "/notes?tag=home&q=TEA").json()]
        expect_eq(got, [1], "ids for tag=home&q=TEA")
        got = [n["id"] for n in s.request("GET", "/notes?q=tea%20at").json()]
        expect_eq(got, [2], "ids for q=tea%20at")


def test_html_page_escapes(prog):
    with prog.server() as s:
        create(s, "<script>alert(1)</script>")
        create(s, "Tom & Jerry")
        r = s.request("GET", "/")
        expect_eq(r.status, 200, "status of GET /")
        expect("text/html" in r.content_type(), f"GET /: Content-Type should be text/html, got {r.content_type()!r}")
        expect("<script>alert(1)</script>" not in r.body, "GET /: the title <script>... appears as raw markup")
        expect(r.body.count("<li") >= 2, "GET /: expected an <li> per note")
        expect(html.escape("<script>alert(1)</script>", quote=False) in r.body
               or "&#60;script&#62;" in r.body or "&lt;script&gt;" in r.body,
               "GET /: escaped title not found")
        expect("Tom &amp; Jerry" in r.body or "Tom &#38; Jerry" in r.body, "GET /: 'Tom & Jerry' not escaped")


def test_unicode(prog):
    with prog.server() as s:
        n = create(s, "Café ☕", "naïve – ok")
        expect_eq(s.request("GET", f"/notes/{n['id']}").json()["title"], "Café ☕", "title")


def test_survives_restart(prog):
    data = tempfile.mkdtemp(prefix="klareval-data-")
    try:
        with prog.server(data) as s:
            create(s, "one", tags=["a"])
            create(s, "two")
            create(s, "three")
            s.request("DELETE", "/notes/3")
            s.request("PATCH", "/notes/2", {"body": "edited"})
            s.restart()
            notes = expect_json(s.request("GET", "/notes"), 200, "GET /notes after restart")
            expect_eq(notes, [{"id": 1, "title": "one", "body": "", "tags": ["a"]},
                              {"id": 2, "title": "two", "body": "edited", "tags": []}], "notes after restart")
            expect_eq(create(s, "four")["id"], 4, "id of the first note after restart")
    finally:
        shutil.rmtree(data, ignore_errors=True)
