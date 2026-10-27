// Knowledge graph view: a 3D map of Markdown notes, their links and tags.

(() => {
  const el = document.getElementById("graph");
  const info = document.getElementById("graph-info");
  const search = document.getElementById("graph-search");
  const card = document.getElementById("notecard");
  let graph = null;
  let data = null;
  let selected = null;
  let loading = null;

  function loadLibrary() {
    if (window.ForceGraph3D) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/static/vendor/3d-force-graph.min.js";
      s.onload = resolve;
      s.onerror = () => reject(new Error("Could not load the 3D graph library."));
      document.head.appendChild(s);
    });
  }

  async function fetchGraph(refresh) {
    info.textContent = "Scanning notes…";
    data = await (await fetch("/api/graph" + (refresh ? "?refresh=true" : ""))).json();
    const notes = data.nodes.filter((n) => n.kind === "note").length;
    const tags = data.nodes.length - notes;
    info.textContent = `${notes} notes, ${tags} tags, ${data.links.length} links${data.truncated ? " (first 2000 notes)" : ""}`;
    return data;
  }

  function neighbors(id) {
    const out = new Set();
    for (const l of data.links) {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      if (s === id) out.add(t);
      if (t === id) out.add(s);
    }
    return out;
  }

  function render() {
    if (!data.nodes.length) {
      el.innerHTML = `<div class="graph-empty"><p>No Markdown notes found in<br><code>${esc(data.root)}</code><br><br>
        Set <code>JARVIS_NOTES_DIR</code> in <code>.env</code> to your notes folder (for example an Obsidian vault)
        and restart Jarvis.</p></div>`;
      return;
    }
    el.innerHTML = "";
    graph = ForceGraph3D()(el)
      .backgroundColor("rgba(0,0,0,0)")
      .width(el.clientWidth)
      .height(el.clientHeight)
      .graphData({ nodes: data.nodes, links: data.links })
      .nodeLabel((n) => n.name)
      .nodeAutoColorBy("group")
      .nodeVal((n) => (n.kind === "tag" ? 1.5 : 2 + n.links))
      .nodeOpacity(0.9)
      .nodeColor((n) => {
        if (!selected) return n.kind === "tag" ? "#7b61ff" : n.color;
        if (n.id === selected.id) return "#ffffff";
        return selected.near.has(n.id) ? (n.kind === "tag" ? "#7b61ff" : n.color) : "#1c2636";
      })
      .linkColor((l) => (l.kind === "tag" ? "rgba(123,97,255,0.35)" : "rgba(54,197,240,0.45)"))
      .linkWidth((l) => {
        if (!selected) return 0.4;
        const s = l.source.id ?? l.source, t = l.target.id ?? l.target;
        return s === selected.id || t === selected.id ? 1.6 : 0.2;
      })
      .linkDirectionalParticles((l) => (selected && ((l.source.id ?? l.source) === selected.id || (l.target.id ?? l.target) === selected.id) ? 2 : 0))
      .onNodeClick(select)
      .onBackgroundClick(clearSelection);
    graph.d3Force("charge").strength(-60);
  }

  function focus(node) {
    const distance = 160;
    const ratio = 1 + distance / Math.hypot(node.x || 1, node.y || 1, node.z || 1);
    graph.cameraPosition({ x: node.x * ratio, y: node.y * ratio, z: node.z * ratio }, node, 1200);
  }

  async function select(node) {
    selected = { id: node.id, near: neighbors(node.id) };
    graph.nodeColor(graph.nodeColor()).linkWidth(graph.linkWidth()).linkDirectionalParticles(graph.linkDirectionalParticles());
    focus(node);
    card.hidden = false;
    document.getElementById("note-name").textContent = node.name;
    const meta = document.getElementById("note-meta");
    const preview = document.getElementById("note-preview");
    const askBtn = document.getElementById("note-ask");
    const openLink = document.getElementById("note-open");
    openLink.hidden = true;
    if (node.kind === "tag") {
      const notes = [...selected.near].map((id) => data.nodes.find((n) => n.id === id)?.name).filter(Boolean);
      meta.textContent = `Tag used in ${notes.length} notes`;
      preview.textContent = notes.join("\n");
      askBtn.onclick = () => window.jarvisAsk(`What do my notes tagged ${node.name} say? Summarize the main ideas.`);
      return;
    }
    meta.textContent = "Loading…";
    preview.textContent = "";
    const note = await (await fetch("/api/note?id=" + encodeURIComponent(node.id))).json();
    meta.textContent = `${node.id} · ${node.links} connections · edited ${note.modified}`;
    preview.textContent = note.preview;
    if (note.obsidian_url) { openLink.href = note.obsidian_url; openLink.hidden = false; }
    askBtn.onclick = () => window.jarvisAsk(`Read my note at ${note.path} and summarize it, then tell me how it connects to my other notes.`);
  }

  function clearSelection() {
    selected = null;
    card.hidden = true;
    if (graph) graph.nodeColor(graph.nodeColor()).linkWidth(graph.linkWidth()).linkDirectionalParticles(graph.linkDirectionalParticles());
  }

  async function open(refresh = false) {
    if (loading) return loading;
    loading = (async () => {
      try {
        await loadLibrary();
        if (!data || refresh) { await fetchGraph(refresh); clearSelection(); render(); }
        else if (graph) graph.width(el.clientWidth).height(el.clientHeight);
      } catch (e) {
        el.innerHTML = `<div class="graph-empty"><p>${esc(e.message)}</p></div>`;
      } finally {
        loading = null;
      }
    })();
    return loading;
  }

  window.openGraph = () => open(false);
  document.getElementById("graph-refresh").addEventListener("click", () => open(true));
  document.getElementById("note-close").addEventListener("click", clearSelection);
  search.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !graph) return;
    const q = search.value.trim().toLowerCase();
    const hit = data.nodes.find((n) => n.name.toLowerCase() === q) || data.nodes.find((n) => n.name.toLowerCase().includes(q));
    if (hit) select(hit); else info.textContent = `No note matches "${search.value}"`;
  });
  window.addEventListener("resize", () => { if (graph && !el.closest("[hidden]")) graph.width(el.clientWidth).height(el.clientHeight); });
})();
