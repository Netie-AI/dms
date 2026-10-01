(function () {
  var data = window.PIPELINE_INVENTORY;
  if (!data) {
    document.body.textContent = "inventory.js missing. Run python tools/pipeline-map/regen.py";
    return;
  }

  var sha = document.getElementById("sha");
  sha.textContent = "commit " + data.commit + "  ·  " + data.repo;

  var order = document.getElementById("order");
  order.textContent =
    "Order follows Executor._live_ask on this commit: intake, then the F5 gate, then certified SQL, then ontology compile, then Insights generate, then Cortex submit. Typed ingest and the scorers sit beside that call. They are not inside it. A dashed node is absent or unverified.";

  var filter = "all";
  var selected = "intake";

  function byId(id) {
    for (var i = 0; i < data.nodes.length; i++) {
      if (data.nodes[i].id === id) return data.nodes[i];
    }
    return null;
  }

  function edgesFrom(id) {
    return data.edges.filter(function (e) { return e.from === id; });
  }
  function edgesTo(id) {
    return data.edges.filter(function (e) { return e.to === id; });
  }

  function statusLabel(node) {
    if (node.status === "absent") return "absent on this commit";
    if (node.status === "partial") return "partial: lane name not in code";
    if (node.status === "unverified") return "unverified";
    if (!node.on_ask_path) return "verified, off the ask call";
    return "verified";
  }

  function renderLegend() {
    var host = document.getElementById("legend");
    host.innerHTML = "";
    ["all", "verified", "partial", "absent", "unverified"].forEach(function (key) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = key;
      if (key === filter) b.style.outline = "2px solid #1c1915";
      b.addEventListener("click", function () {
        filter = key;
        render();
      });
      host.appendChild(b);
    });
  }

  function visible(node) {
    if (filter === "all") return true;
    return node.status === filter;
  }

  function renderFlow() {
    var host = document.getElementById("flow");
    host.innerHTML = "";
    var stages = [];
    data.nodes.forEach(function (node) {
      if (!visible(node)) return;
      var stage = stages.find(function (s) { return s.name === node.stage; });
      if (!stage) {
        stage = { name: node.stage, nodes: [] };
        stages.push(stage);
      }
      stage.nodes.push(node);
    });
    stages.forEach(function (stage) {
      var box = document.createElement("section");
      box.className = "stage";
      var h = document.createElement("h2");
      h.textContent = stage.name;
      box.appendChild(h);
      stage.nodes.forEach(function (node) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "node " + node.status + (node.id === selected ? " selected" : "");
        var title = document.createElement("span");
        title.className = "title";
        title.textContent = node.title;
        var meta = document.createElement("span");
        meta.className = "meta";
        var cite = node.citations.find(function (c) { return c.ok; });
        meta.textContent = statusLabel(node) + (cite ? "  ·  " + cite.path.split("/").pop() + ":" + cite.line : "");
        b.appendChild(title);
        b.appendChild(meta);
        b.addEventListener("click", function () {
          selected = node.id;
          render();
        });
        box.appendChild(b);
      });
      host.appendChild(box);
    });
  }

  function addCite(parent, cite) {
    var div = document.createElement("div");
    div.className = "cite";
    var loc = cite.ok ? cite.path + ":" + cite.line : cite.path + " (anchor missing)";
    div.textContent = loc + (cite.text ? "\n" + cite.text : "");
    parent.appendChild(div);
  }

  function renderDetail() {
    var host = document.getElementById("detail");
    host.innerHTML = "";
    var node = byId(selected) || data.nodes[0];
    var h = document.createElement("h3");
    h.textContent = node.title;
    host.appendChild(h);
    var pill = document.createElement("div");
    pill.className = "pill";
    pill.textContent = statusLabel(node);
    host.appendChild(pill);
    if (node.lane_name_unverified) {
      var note = document.createElement("p");
      note.className = "note";
      note.textContent = "The name \"" + node.lane_name_unverified + "\" is not a symbol in this tree. The why says what the code does instead.";
      host.appendChild(note);
    }
    var why = document.createElement("p");
    why.className = "why";
    why.textContent = node.why;
    host.appendChild(why);

    var h4 = document.createElement("h4");
    h4.textContent = "File:line";
    host.appendChild(h4);
    node.citations.forEach(function (c) { addCite(host, c); });

    var hr = document.createElement("h4");
    hr.textContent = "Named reasons in the cited span";
    host.appendChild(hr);
    if (!node.reasons.length) {
      var empty = document.createElement("div");
      empty.className = "reason";
      empty.textContent = node.status === "absent" ? "none: this step is not in the tree" : "none extracted in the scanned lines";
      host.appendChild(empty);
    }
    node.reasons.forEach(function (r) {
      var div = document.createElement("div");
      div.className = "reason";
      div.textContent = r.token + "  ·  " + r.path + ":" + r.line;
      host.appendChild(div);
    });

    function edgeBlock(title, list, dir) {
      var eh = document.createElement("h4");
      eh.textContent = title;
      host.appendChild(eh);
      if (!list.length) {
        var none = document.createElement("div");
        none.className = "edge";
        none.textContent = "none";
        host.appendChild(none);
        return;
      }
      list.forEach(function (e) {
        var div = document.createElement("div");
        div.className = "edge";
        var btn = document.createElement("button");
        btn.type = "button";
        var other = dir === "out" ? e.to : e.from;
        var otherNode = byId(other);
        btn.textContent = (dir === "out" ? "to " : "from ") + (otherNode ? otherNode.title : other) + " - " + e.label;
        btn.addEventListener("click", function () {
          selected = other;
          render();
        });
        div.appendChild(btn);
        var loc = document.createElement("div");
        loc.textContent = (e.ok ? e.path + ":" + e.line : e.path + " (anchor missing)") + (e.text ? "\n" + e.text : "");
        div.appendChild(loc);
        host.appendChild(div);
      });
    }
    edgeBlock("Edges out", edgesFrom(node.id), "out");
    edgeBlock("Edges in", edgesTo(node.id), "in");
  }

  function render() {
    renderLegend();
    renderFlow();
    renderDetail();
  }

  render();
})();
