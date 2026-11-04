// Shared behaviour: theme, language switch, nav shadow, reveal-on-scroll, copy
// buttons, card glow, code-block copy buttons and the docs table of contents.
(() => {
  const root = document.documentElement;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- theme: the stored choice wins, else the system's ----
  const lightQuery = window.matchMedia("(prefers-color-scheme: light)");
  const storedTheme = () => { try { return localStorage.getItem("vinta-theme"); } catch { return null; } };
  const applyTheme = () => {
    const t = storedTheme();
    root.dataset.theme = t === "light" || t === "dark" ? t : lightQuery.matches ? "light" : "dark";
  };
  applyTheme();
  lightQuery.addEventListener("change", applyTheme);
  document.querySelectorAll<HTMLElement>("[data-theme-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = root.dataset.theme === "light" ? "dark" : "light";
      try { localStorage.setItem("vinta-theme", next); } catch { /* private mode */ }
      applyTheme();
    });
  });

  // ---- language: ?lang= wins once, then the stored choice, else Python ----
  type Lang = "py" | "ts";
  const isLang = (v: unknown): v is Lang => v === "py" || v === "ts";
  const pickers = document.querySelectorAll<HTMLButtonElement>("[data-lang-pick]");
  const applyLang = (lang: Lang) => {
    root.dataset.lang = lang;
    pickers.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.langPick === lang)));
    document.dispatchEvent(new CustomEvent("vintasend:lang", { detail: lang }));
  };
  const current = (): Lang => (isLang(root.dataset.lang) ? root.dataset.lang : "py");
  applyLang(current());
  pickers.forEach((btn) => {
    btn.addEventListener("click", () => {
      const lang = btn.dataset.langPick;
      if (!isLang(lang)) return;
      try { localStorage.setItem("vintasend-lang", lang); } catch { /* private mode */ }
      const url = new URL(location.href);
      if (url.searchParams.has("lang")) { url.searchParams.delete("lang"); history.replaceState(null, "", url); }
      applyLang(lang);
    });
  });

  // ---- nav shadow ----
  const nav = document.querySelector(".nav");
  const onScroll = () => nav && nav.classList.toggle("scrolled", window.scrollY > 8);
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });

  // ---- reveal on scroll ----
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
  document.querySelectorAll(".reveal").forEach((el) => io.observe(el));
  // A revealed element inside a hidden language block never intersects; when the
  // language flips, re-observe so it animates in instead of staying invisible.
  document.addEventListener("vintasend:lang", () => {
    document.querySelectorAll(".reveal:not(.in)").forEach((el) => io.observe(el));
  });

  // ---- copy buttons with a data-copy payload ----
  document.querySelectorAll<HTMLElement>("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(btn.dataset.copy ?? ""); } catch { return; }
      btn.classList.add("copied");
      const ico = btn.querySelector(".ico");
      const prev = ico ? ico.textContent : "";
      if (ico) ico.textContent = "✓";
      setTimeout(() => { btn.classList.remove("copied"); if (ico) ico.textContent = prev; }, 1400);
    });
  });

  // ---- card glow follows the pointer ----
  document.querySelectorAll<HTMLElement>(".card").forEach((card) => {
    card.addEventListener("pointermove", (e) => {
      const r = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${e.clientX - r.left}px`);
      card.style.setProperty("--my", `${e.clientY - r.top}px`);
    });
  });

  // ---- copy button on every highlighted code block ----
  const copyIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  const checkIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></svg>';
  document.querySelectorAll<HTMLPreElement>("pre.astro-code").forEach((pre) => {
    if (pre.parentElement?.classList.contains("code-wrap")) return;
    const wrap = document.createElement("div");
    wrap.className = "code-wrap";
    pre.replaceWith(wrap);
    wrap.appendChild(pre);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "code-copy";
    btn.setAttribute("aria-label", "Copy code");
    btn.innerHTML = copyIcon;
    btn.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(pre.innerText.replace(/\n$/, "")); } catch { return; }
      btn.classList.add("copied");
      btn.innerHTML = checkIcon;
      setTimeout(() => { btn.classList.remove("copied"); btn.innerHTML = copyIcon; }, 1400);
    });
    wrap.appendChild(btn);
  });

  // ---- docs: mobile sidebar toggle ----
  const menuBtn = document.querySelector<HTMLElement>("[data-docs-menu]");
  const sidebar = document.querySelector<HTMLElement>(".sidebar");
  menuBtn?.addEventListener("click", () => sidebar?.classList.toggle("open"));

  // ---- docs: table of contents, built from the headings that are visible in
  // the current language, and rebuilt when the language changes ----
  const toc = document.querySelector<HTMLElement>("[data-toc]");
  const prose = document.querySelector<HTMLElement>(".prose");
  if (toc && prose) {
    let spy: IntersectionObserver | null = null;
    const build = () => {
      spy?.disconnect();
      const headings = Array.from(prose.querySelectorAll<HTMLHeadingElement>("h2, h3"))
        .filter((h) => h.id && h.offsetParent !== null);
      const list = toc.querySelector("ul");
      if (!list) return;
      list.innerHTML = "";
      toc.hidden = headings.length === 0;
      const links = new Map<Element, HTMLAnchorElement>();
      for (const h of headings) {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = `#${h.id}`;
        a.textContent = h.textContent;
        a.className = h.tagName.toLowerCase();
        li.appendChild(a);
        list.appendChild(li);
        links.set(h, a);
      }
      spy = new IntersectionObserver((entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          links.forEach((a) => a.classList.remove("on"));
          links.get(e.target)?.classList.add("on");
        }
      }, { rootMargin: "-70px 0px -70% 0px", threshold: 0 });
      headings.forEach((h) => spy?.observe(h));
    };
    build();
    document.addEventListener("vintasend:lang", build);
  }

  // ---- year in the footer ----
  document.querySelectorAll("[data-year]").forEach((el) => { el.textContent = String(new Date().getFullYear()); });

  // ---- typed terminal, kept from the sibling pages for the landing hero ----
  type Line = { t: "cmd" | "out"; s: string; d?: number; p?: string };
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, reduced ? 0 : ms));
  const runTerminal = (el: HTMLElement, lines: Line[]) => {
    let stopped = false;
    const run = async () => {
      while (!stopped) {
        el.innerHTML = "";
        for (const line of lines) {
          if (stopped) return;
          const div = document.createElement("div");
          div.className = "ln show";
          el.appendChild(div);
          if (line.t === "cmd") {
            const p = document.createElement("span"); p.className = "p"; p.textContent = line.p || "$ ";
            const c = document.createElement("span"); c.className = "c";
            const cur = document.createElement("span"); cur.className = "cur";
            div.append(p, c, cur);
            await sleep(line.d ?? 350);
            for (const ch of line.s) { c.textContent += ch; await sleep(18 + Math.random() * 40); }
            await sleep(260);
            cur.remove();
          } else {
            div.classList.remove("show");
            div.innerHTML = line.s;
            await sleep(line.d ?? 120);
            div.classList.add("show");
          }
        }
        const end = document.createElement("div"); end.className = "ln show"; end.innerHTML = '<span class="p">$ </span><span class="cur"></span>';
        el.appendChild(end);
        await sleep(4200);
      }
    };
    run();
    return () => { stopped = true; };
  };
  document.querySelectorAll<HTMLElement>(".term[data-script]").forEach((el) => {
    const src = document.getElementById(el.dataset.script ?? "");
    if (!src) return;
    const lines = JSON.parse(src.textContent ?? "[]") as Line[];
    const obs = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { runTerminal(el, lines); obs.disconnect(); } });
    obs.observe(el);
  });
})();
