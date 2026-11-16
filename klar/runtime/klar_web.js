// Klar runtime: browser target (std.ui apps).

function $flush() {
  if ($out.length === 0) return;
  const lines = $out;
  $out = []; $out_len = 0;
  for (const l of lines) console.log(l);
}
function $print_now(s) { console.log(s); }
function $read_line() { return $None; }
function $read_file(p) { return $Err(`read_file is not available in the browser: '${p}'`); }
function $write_file(p) { return $Err(`write_file is not available in the browser: '${p}'`); }
function $args() { return $L([]); }
function $http_response(status, headers, body) { return new $structs.HttpResponse(status, headers, body); }

async function $fetch(method, url, headers, body) {
  try {
    const r = await fetch(url, { method, headers: Object.fromEntries($entries(headers)), body: body === "" ? undefined : body });
    const hs = $M([...r.headers.entries()].map(([k, v]) => [k.toLowerCase(), v]));
    return $Ok($http_response(r.status, hs, await r.text()));
  } catch (e) {
    return $Err(`network error: ${e.message}`);
  }
}

// ---------------------------------------------------------------- std.ui: model / update / view
// The view is an Html value (enum HtmlNode / HtmlText / HtmlRaw); it is turned into DOM nodes and, on every
// change, compared with the previous view so only the differences touch the page.
function $ui_attrs(attrs) {
  const props = new Map(), events = {};
  const classes = [], styles = [];
  for (const a of $arr(attrs)) {
    if (a.t === "AttrText") {
      if (a.f[0] === "class") { if (a.f[1] !== "") classes.push(a.f[1]); }
      else if (a.f[0] === "style") { if (a.f[1] !== "") styles.push(a.f[1]); }
      else props.set(a.f[0], a.f[1]);
    } else if (a.t === "AttrFlag") {
      props.set(a.f[0], a.f[1]);
    } else {
      events[a.f[0]] = a.f[1];
    }
  }
  if (classes.length) props.set("class", classes.join(" "));
  if (styles.length) props.set("style", styles.join("; "));
  return { props, events };
}

class $UiApp {
  constructor(init, update, view, on_url, root) {
    this.model = init.model;
    this.update = update;
    this.view = view;
    this.on_url = on_url;
    this.root = root;
    this.vnode = null;
    this.dom = null;
    this.queued = false;
    this.focus = null;
    this.render();
    this.run(init.cmds);
  }
  dispatch(msg) {
    let next;
    try { next = this.update(this.model, msg); }
    catch (e) { return this.crash(e); }
    this.model = next.model;
    this.schedule();
    this.run(next.cmds);
  }
  crash(e) {
    const text = $render_error(e);
    console.error(text);
    const pre = document.createElement("pre");
    pre.className = "klar-error";
    pre.style.cssText = "position:fixed;left:1rem;right:1rem;bottom:1rem;margin:0;padding:1rem;z-index:99999;" +
      "background:#7f1d1d;color:#fff;border-radius:8px;white-space:pre-wrap;font:13px/1.4 ui-monospace,monospace";
    pre.textContent = text;
    document.body.appendChild(pre);
  }
  schedule() {
    if (this.queued) return;
    this.queued = true;
    requestAnimationFrame(() => this.flush());
  }
  // Render now if a change is waiting.
  flush() {
    if (!this.queued) return;
    this.queued = false;
    this.render();
  }
  render() {
    let v;
    try { v = this.view(this.model); }
    catch (e) { return this.crash(e); }
    const before = document.activeElement;
    if (this.dom === null) {
      this.dom = this.create(v);
      this.root.replaceChildren(this.dom);
    } else {
      this.dom = this.patch(this.dom, this.vnode, v);
    }
    this.vnode = v;
    // A modal dialog: remember what had focus when it opened, and give focus back when it closes.
    const modal = this.root.querySelector("[aria-modal=true]");
    if (modal && !this.modal) this.modal_return = before;
    if (!modal && this.modal && this.modal_return && this.modal_return.isConnected) this.modal_return.focus();
    this.modal = modal;
    if (this.focus !== null) {
      const el = document.getElementById(this.focus);
      this.focus = null;
      if (el) el.focus();
    }
  }
  run(cmds) {
    for (const c of $arr(cmds)) {
      switch (c.t) {
        case "CmdHttp": {
          const [method, url, headers, body, to_msg] = c.f;
          $fetch(method, url, headers, body).then(r => this.dispatch(to_msg(r)));
          break;
        }
        case "CmdAfter": setTimeout(() => this.dispatch(c.f[1]), c.f[0]); break;
        case "CmdSave": try { localStorage.setItem(c.f[0], c.f[1]); } catch {} break;
        case "CmdGo":
          history.pushState(null, "", c.f[0]);
          if (this.on_url) this.dispatch(this.on_url(location.pathname + location.search));
          break;
        case "CmdFocus": this.focus = c.f[0]; this.schedule(); break;
        case "CmdListen": {
          const [url, to_msg] = c.f;
          this.streams ??= new Map();
          if (this.streams.has(url)) { this.streams.get(url).to_msg = to_msg; break; }
          const s = { to_msg, es: new EventSource(url) };
          s.es.onmessage = ev => this.dispatch(s.to_msg(ev.data));
          this.streams.set(url, s);
          break;
        }
        case "CmdUnlisten": {
          const s = this.streams?.get(c.f[0]);
          if (s) { s.es.close(); this.streams.delete(c.f[0]); }
          break;
        }
      }
    }
  }
  // ---- DOM
  create(v) {
    if (v.t === "HtmlText") return document.createTextNode(v.f[0]);
    if (v.t === "HtmlRaw") {
      const wrap = document.createElement("klar-raw");
      wrap.style.display = "contents";
      wrap.innerHTML = v.f[0];
      return wrap;
    }
    const el = document.createElement(v.f[0]);
    const { props, events } = $ui_attrs(v.f[1]);
    for (const [k, x] of props) this.set_prop(el, k, x);
    this.set_events(el, events);
    for (const c of $arr(v.f[2])) el.appendChild(this.create(c));
    if (props.get("autofocus") === true) queueMicrotask(() => el.focus());
    return el;
  }
  set_prop(el, k, x) {
    if (typeof x === "boolean") {
      if (k === "checked" || k === "disabled" || k === "selected") el[k] = x;
      if (x) el.setAttribute(k, ""); else el.removeAttribute(k);
    } else if (k === "value" && "value" in el) {
      if (el.value !== x) el.value = x;
      el.setAttribute(k, x);
    } else {
      el.setAttribute(k, x);
    }
  }
  set_events(el, events) {
    el.$h = events;
    if (events.dragstart) { el.draggable = true; el.$dg = true; } else if (el.$dg) { el.draggable = false; el.$dg = false; }
    if (events.drop && !el.$drop) {
      // a drop target must accept dragover, and shows .drag-over while something is held above it
      el.$drop = true;
      let depth = 0;
      el.addEventListener("dragover", ev => { if (el.$h.drop) ev.preventDefault(); });
      el.addEventListener("dragenter", () => { if (el.$h.drop && depth++ === 0) el.classList.add("drag-over"); });
      el.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; el.classList.remove("drag-over"); } });
      el.addEventListener("drop", () => { depth = 0; el.classList.remove("drag-over"); });
    }
    for (const type of Object.keys(events)) {
      if (el.$on && el.$on[type]) continue;
      (el.$on ??= {})[type] = true;
      el.addEventListener(type, ev => {
        // A key pressed before the next frame must see the view of the newest model (typing then a quick
        // ArrowDown). Only keydown flushes: an input event's own text is not in the model yet.
        if (type === "keydown") this.flush();
        const h = el.$h[type];
        if (!h) return;
        if (type === "submit" || type === "drop" || type === "mousedown") ev.preventDefault();
        if (type === "drop") ev.stopPropagation();
        if (type === "dragstart") { ev.dataTransfer.effectAllowed = "move"; ev.dataTransfer.setData("text/plain", ""); }
        if (type === "click" && el.tagName === "A") ev.preventDefault();
        let msg;
        if (h.t === "HandleKey") {
          const m = h.f[0](ev.key);
          if (m.t !== "Some") return;
          ev.preventDefault();
          ev.stopPropagation();
          msg = m.f[0];
        } else if (h.t === "HandleMsg") msg = h.f[0];
        else if (h.t === "HandleText") msg = h.f[0](ev.target.value);
        else msg = h.f[0](!!ev.target.checked);
        this.dispatch(msg);
      });
    }
  }
  patch(dom, old, v) {
    if (old.t !== v.t || (v.t === "HtmlNode" && old.f[0] !== v.f[0]) || v.t === "HtmlRaw") {
      if (v.t === "HtmlRaw" && old.t === "HtmlRaw" && old.f[0] === v.f[0]) return dom;
      const n = this.create(v);
      dom.replaceWith(n);
      return n;
    }
    if (v.t === "HtmlText") {
      if (old.f[0] !== v.f[0]) dom.nodeValue = v.f[0];
      return dom;
    }
    const a = $ui_attrs(old.f[1]), b = $ui_attrs(v.f[1]);
    for (const [k] of a.props) if (!b.props.has(k)) { dom.removeAttribute(k); if (k === "checked" || k === "disabled") dom[k] = false; }
    for (const [k, x] of b.props) {
      if (a.props.get(k) !== x || k === "value" || k === "checked") this.set_prop(dom, k, x);
    }
    this.set_events(dom, b.events);
    const oc = $arr(old.f[2]), nc = $arr(v.f[2]);
    const kids = dom.childNodes;
    const n = Math.min(oc.length, nc.length);
    for (let i = 0; i < n; i++) this.patch(kids[i], oc[i], nc[i]);
    for (let i = n; i < nc.length; i++) dom.appendChild(this.create(nc[i]));
    for (let i = oc.length - 1; i >= nc.length; i--) dom.removeChild(kids[i]);
    return dom;
  }
}

function $ui_start(init, update, view, on_url, css) {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.prepend(style);
  const root = document.getElementById("app") || document.body;
  const url_msg = on_url.t === "Some" ? on_url.f[0] : null;
  if (url_msg) {
    const go = () => app.dispatch(url_msg(location.pathname + location.search));
    window.addEventListener("popstate", go);
    document.addEventListener("click", ev => {
      const a = ev.target.closest && ev.target.closest("a[href]");
      if (!a || ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
      if (a.target && a.target !== "_self") return;
      const u = new URL(a.href, location.href);
      if (u.origin !== location.origin) return;
      ev.preventDefault();
      history.pushState(null, "", u.pathname + u.search + u.hash);
      go();
    });
  }
  // std.kit menus are <details>: close them on a click outside, after picking an item, and on Escape.
  document.addEventListener("click", ev => {
    for (const d of document.querySelectorAll("details.menu[open]")) {
      const item = ev.target.closest && ev.target.closest(".menu-items > *");
      if (!d.contains(ev.target) || (item && d.contains(item))) d.removeAttribute("open");
    }
  });
  document.addEventListener("keydown", ev => {
    // keep Tab inside an open modal dialog
    const modal = ev.key === "Tab" && document.querySelector("[aria-modal=true]");
    if (modal) {
      const all = [...modal.querySelectorAll("a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex='-1'])")]
        .filter(e => e.offsetParent !== null);
      if (all.length === 0) { ev.preventDefault(); return; }
      const first = all[0], last = all[all.length - 1];
      if (ev.shiftKey && (document.activeElement === first || !modal.contains(document.activeElement))) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && (document.activeElement === last || !modal.contains(document.activeElement))) { ev.preventDefault(); first.focus(); }
      return;
    }
    if (ev.key !== "Escape") return;
    for (const d of document.querySelectorAll("details.menu[open]")) { d.removeAttribute("open"); d.querySelector("summary")?.focus(); }
  });
  const app = new $UiApp(init, update, view, url_msg, root);
  window.$klar_app = app;
  if (url_msg) app.dispatch(url_msg(location.pathname + location.search));
}

Object.assign($std, {
  "klar.stdpy.httplib.request": $fetch,
  "klar:ui.start": $ui_start,
  "klar:ui.load": key => { try { const v = localStorage.getItem(key); return v === null ? $None : $Some(v); } catch { return $None; } },
});

async function $start(main) {
  try {
    await main();
    $flush();
  } catch (e) {
    $flush();
    console.error($render_error(e));
    if (typeof document !== "undefined") {
      const pre = document.createElement("pre");
      pre.style.cssText = "color:#b91c1c;white-space:pre-wrap;padding:1rem";
      pre.textContent = $render_error(e);
      document.body.appendChild(pre);
    }
  }
}
