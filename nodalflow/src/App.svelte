<script>
  import { invoke } from "@tauri-apps/api/tauri";
  import { open } from "@tauri-apps/api/dialog";
  import { onMount, afterUpdate, tick } from "svelte";

  // ── Core state ─────────────────────────────────────────────────────────
  let input = "";
  let isProcessing = false;
  let cortexOnline = false;
  let availableModels = [];
  let selectedModel = "llama3";

  // ── Chronicle ───────────────────────────────────────────────────────────
  let chronicle = [];
  let trash = [];
  let chronicleContainer;
  let showNewNodesIndicator = false;
  let userScrolledUp = false;
  let collapsedNodes = new Set();

  // ── Vault ───────────────────────────────────────────────────────────────
  let shardMounted = false;
  let shardInfo = null;
  let shardError = null;
  let shardStats = null;
  let maxTier = null;

  // ── Panels ──────────────────────────────────────────────────────────────
  let showSourceViewer = false;
  let selectedSource = null;
  let sourceContent = null;
  let sourceVerified = null;
  let showStats = false;
  let showTrash = false;
  let showSettings = false;

  // ── Annotation ──────────────────────────────────────────────────────────
  let editingAnnotation = null;
  let annotationText = "";

  // ── Lifecycle ───────────────────────────────────────────────────────────
  onMount(async () => {
    try {
      await invoke("check_cortex_status");
      cortexOnline = true;
    } catch (e) {
      cortexOnline = false;
    }
    try {
      availableModels = await invoke("list_models");
      if (availableModels.length > 0 && !availableModels.includes(selectedModel)) {
        selectedModel = availableModels[0];
      }
    } catch (e) { availableModels = []; }
  });

  afterUpdate(async () => {
    if (!chronicleContainer) return;
    if (!userScrolledUp) {
      await tick();
      chronicleContainer.scrollTop = chronicleContainer.scrollHeight;
      showNewNodesIndicator = false;
    } else if (!isProcessing && chronicle.length > 0) {
      showNewNodesIndicator = true;
    }
  });

  function handleScroll() {
    if (!chronicleContainer) return;
    const { scrollTop, scrollHeight, clientHeight } = chronicleContainer;
    userScrolledUp = (scrollHeight - scrollTop - clientHeight) > 100;
    if (!userScrolledUp) showNewNodesIndicator = false;
  }

  function scrollToBottom() {
    if (chronicleContainer) {
      chronicleContainer.scrollTop = chronicleContainer.scrollHeight;
      userScrolledUp = false;
      showNewNodesIndicator = false;
    }
  }

  // ── Vault operations ────────────────────────────────────────────────────
  async function mountShard() {
    try {
      const selected = await open({ directory: true, title: "Select AXM Genesis Shard" });
      if (!selected) return;
      shardError = null;
      const info = await invoke("mount_vault", { path: selected });
      shardInfo = info;
      shardMounted = true;
      try { shardStats = await invoke("get_statistics"); } catch (e) {}
      appendSystem(`Shard mounted: ${info.title} · ${info.claim_count} claims · ${trustLabel(info.trust_level)}`);
    } catch (e) {
      shardError = e.toString();
    }
  }

  async function unmountShard() {
    try {
      await invoke("unmount_vault");
      shardMounted = false;
      shardInfo = null;
      shardStats = null;
      appendSystem("Shard unmounted.");
    } catch (e) { shardError = e.toString(); }
  }

  function appendSystem(msg) {
    chronicle = [...chronicle, { id: generateId(), type: "system", content: msg, timestamp: new Date() }];
  }

  function trustLabel(level) {
    return { Verified: "✓ Verified", SignatureOnly: "◑ Partial", Unverified: "○ Unverified", Failed: "✗ Failed" }[level] || level;
  }
  function trustClass(level) {
    return { Verified: "verified", SignatureOnly: "partial", Unverified: "unverified", Failed: "failed" }[level] || "unverified";
  }

  // ── Source verification ─────────────────────────────────────────────────
  async function openSourceViewer(claim) {
    selectedSource = claim;
    sourceContent = null;
    sourceVerified = null;
    showSourceViewer = true;
    if (!claim.source_hash || claim.byte_start < 0) {
      sourceContent = "(No provenance data for this claim)";
      return;
    }
    try {
      sourceContent = await invoke("get_content_slice", {
        sourceHash: claim.source_hash,
        byteStart: claim.byte_start,
        byteEnd: claim.byte_end,
      });
      sourceVerified = await invoke("verify_claim", { claim });
    } catch (e) {
      sourceContent = `Error: ${e}`;
      sourceVerified = false;
    }
  }

  // ── Browse ──────────────────────────────────────────────────────────────
  async function browseAllClaims() {
    if (!shardMounted) return;
    try {
      const claims = await invoke("get_all_claims", { maxTier, limit: 50 });
      chronicle = [...chronicle, {
        id: generateId(), type: "browse",
        title: `All Claims — ${shardInfo?.title || "shard"}`,
        claims, timestamp: new Date(),
      }];
    } catch (e) {
      chronicle = [...chronicle, { id: generateId(), type: "error", content: `Browse failed: ${e}`, timestamp: new Date() }];
    }
  }

  // ── Main query ──────────────────────────────────────────────────────────
  async function handleSubmit() {
    if (!input.trim() || isProcessing) return;

    const userMessage = input.trim();
    const ts = new Date();
    input = "";
    isProcessing = true;
    userScrolledUp = false;

    let newNodes = [{ id: generateId(), type: "intent", content: userMessage, timestamp: ts }];

    try {
      if (shardMounted) {
        // Vault path: backend queries shard then calls Ollama with injected context
        const response = await invoke("query_ollama", {
          prompt: userMessage,
          model: selectedModel,
          maxTier,
        });
        newNodes = [...newNodes, ...parseEnrichedResponse(response, ts)];
      } else {
        // No vault — plain Ollama with chronicle context
        const contextPrompt = buildPlainPrompt(userMessage);
        const raw = await invoke("query_ollama", { prompt: contextPrompt, model: selectedModel });
        // scaffold backend returns string; v2 returns EnrichedResponse — handle both
        const text = typeof raw === "string" ? raw : raw.content;
        newNodes = [...newNodes, ...parseResponse(text, ts)];
      }
    } catch (e) {
      newNodes.push({ id: generateId(), type: "error", content: `${e}`, timestamp: ts });
    }

    chronicle = [...chronicle, ...newNodes];
    await tick();
    for (const node of newNodes) {
      if (node.type === "viz") executeD3(node.id, node.content);
    }
    isProcessing = false;
  }

  function buildPlainPrompt(userMessage) {
    const systemPrompt = `You are Nodal Flow, a sovereign local-first AI interface running entirely on the user's hardware.

RESPONSE FORMAT:
- Visualizations: <NODE type="viz" title="...">D3.js v7 code targeting d3.select("#node-canvas-" + nodeId)</NODE>
- Code: <NODE type="code" lang="..." title="...">code</NODE>
- Writing: <NODE type="write" title="...">markdown text</NODE>
- Chat: plain text, no tags`;

    const ctx = chronicle.slice(-6).map(n => {
      if (n.type === "intent") return `User: ${n.content}`;
      if (n.type === "chat") return `Assistant: ${n.content}`;
      if (n.title) return `[${n.type}: "${n.title}"]`;
      return `[${n.type}]`;
    }).join("\n");

    return `${systemPrompt}\n\n${ctx ? "Recent:\n" + ctx + "\n\n" : ""}User: ${userMessage}\nAssistant:`;
  }

  // Parse EnrichedResponse {content, sources, has_verified_context, trust_level}
  function parseEnrichedResponse(response, timestamp) {
    const nodes = [];
    const content = typeof response === "string" ? response : (response.content || "");
    const sources = typeof response === "object" ? (response.sources || []) : [];
    const hasVerified = typeof response === "object" ? response.has_verified_context : false;

    const nodeRe = /<NODE\s+type="(\w+)"([^>]*)>([\s\S]*?)<\/NODE>/g;
    let match, lastIndex = 0;

    while ((match = nodeRe.exec(content)) !== null) {
      const textBefore = content.slice(lastIndex, match.index).trim();
      if (textBefore) nodes.push({ id: generateId(), type: "chat", content: textBefore, timestamp, verified: hasVerified });

      const nodeType = match[1];
      const attrs = match[2];
      const body = match[3].trim();
      const getAttr = (name) => { const m = attrs.match(new RegExp(`${name}="([^"]*)"`)); return m ? m[1] : ""; };

      if (nodeType === "citation") {
        nodes.push({
          id: generateId(), type: "citation",
          source_hash: getAttr("source_hash"),
          byte_start: parseInt(getAttr("byte_start") || "-1"),
          byte_end: parseInt(getAttr("byte_end") || "-1"),
          subject: getAttr("subject"), predicate: getAttr("predicate"), object: getAttr("object"),
          evidence: body, timestamp,
        });
      } else {
        nodes.push({
          id: generateId(), type: nodeType,
          lang: getAttr("lang") || null, title: getAttr("title") || null,
          content: body, timestamp,
        });
      }
      lastIndex = match.index + match[0].length;
    }

    const remaining = content.slice(lastIndex).trim();
    if (remaining) nodes.push({ id: generateId(), type: "chat", content: remaining, timestamp, verified: hasVerified });
    if (nodes.length === 0 && content) nodes.push({ id: generateId(), type: "chat", content, timestamp, verified: hasVerified });

    if (sources.length > 0) {
      nodes.push({
        id: generateId(), type: "sources",
        title: `${sources.length} verified source${sources.length !== 1 ? "s" : ""}`,
        claims: sources, timestamp, expanded: false,
      });
    }
    return nodes;
  }

  // Parse plain string response
  function parseResponse(response, timestamp) {
    const nodes = [];
    const nodeRe = /<NODE\s+type="(\w+)"([^>]*)>([\s\S]*?)<\/NODE>/g;
    let match, lastIndex = 0;

    while ((match = nodeRe.exec(response)) !== null) {
      const textBefore = response.slice(lastIndex, match.index).trim();
      if (textBefore) nodes.push({ id: generateId(), type: "chat", content: textBefore, timestamp });
      const attrs = match[2];
      const getAttr = (name) => { const m = attrs.match(new RegExp(`${name}="([^"]*)"`)); return m ? m[1] : ""; };
      nodes.push({
        id: generateId(), type: match[1],
        lang: getAttr("lang") || null, title: getAttr("title") || null,
        content: match[3].trim(), timestamp,
      });
      lastIndex = match.index + match[0].length;
    }

    const remaining = response.slice(lastIndex).trim();
    if (remaining) nodes.push({ id: generateId(), type: "chat", content: remaining, timestamp });
    if (nodes.length === 0) nodes.push({ id: generateId(), type: "chat", content: response, timestamp });
    return nodes;
  }

  // ── D3 execution ────────────────────────────────────────────────────────
  function executeD3(nodeId, code) {
    try {
      new Function(`
        const nodeId = "${nodeId}";
        const container = d3.select("#node-canvas-${nodeId}");
        const width = 560, height = 360;
        ${code}
      `)();
    } catch (e) {
      chronicle = chronicle.map(n => n.id === nodeId ? { ...n, error: e.message } : n);
    }
  }

  // ── Utilities ───────────────────────────────────────────────────────────
  function generateId() {
    return "n-" + Date.now() + "-" + Math.random().toString(36).substr(2, 8);
  }
  function formatTime(date) {
    return date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  function handleKeydown(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSubmit(); }
  }
  function toggleCollapse(nodeId) {
    const s = new Set(collapsedNodes);
    s.has(nodeId) ? s.delete(nodeId) : s.add(nodeId);
    collapsedNodes = s;
  }
  function toggleSourcesExpanded(nodeId) {
    chronicle = chronicle.map(n => n.id === nodeId ? { ...n, expanded: !n.expanded } : n);
  }

  // ── Trash ───────────────────────────────────────────────────────────────
  function moveToTrash(nodeId) {
    const node = chronicle.find(n => n.id === nodeId);
    if (node) {
      trash = [...trash, { ...node, deletedAt: new Date() }];
      chronicle = chronicle.filter(n => n.id !== nodeId);
    }
  }
  function restoreFromTrash(nodeId) {
    const node = trash.find(n => n.id === nodeId);
    if (node) {
      const { deletedAt, ...r } = node;
      chronicle = [...chronicle, r].sort((a, b) => a.timestamp - b.timestamp);
      trash = trash.filter(n => n.id !== nodeId);
    }
  }

  // ── Annotation ──────────────────────────────────────────────────────────
  function startAnnotation(nodeId) {
    editingAnnotation = nodeId;
    annotationText = chronicle.find(n => n.id === nodeId)?.annotation || "";
  }
  function saveAnnotation() {
    if (editingAnnotation) {
      chronicle = chronicle.map(n =>
        n.id === editingAnnotation ? { ...n, annotation: annotationText.trim() || null } : n
      );
      editingAnnotation = null;
      annotationText = "";
    }
  }
</script>

<main class="app">
  <!-- SIDEBAR -->
  <aside class="sidebar">
    <header class="sidebar-header">
      <div class="logo">◇ Nodal Flow</div>
      <div class="version">v0.2</div>
    </header>

    <div class="status-section">
      <div class="status" class:online={cortexOnline}>
        <span class="status-dot"></span>
        <span>{cortexOnline ? "Cortex Online" : "Cortex Offline"}</span>
      </div>
      {#if availableModels.length > 0}
        <select class="model-select" bind:value={selectedModel}>
          {#each availableModels as m}<option value={m}>{m}</option>{/each}
        </select>
      {/if}
    </div>

    <!-- Vault -->
    <div class="shard-section">
      <div class="section-label">The Vault</div>
      {#if shardMounted && shardInfo}
        <div class="shard-info">
          <div class="shard-title">{shardInfo.title}</div>
          <div class="shard-meta">
            <span class="namespace">{shardInfo.namespace}</span>
            <span class="trust-badge {trustClass(shardInfo.trust_level)}">{trustLabel(shardInfo.trust_level)}</span>
          </div>
          <div class="shard-counts">{shardInfo.entity_count} entities · {shardInfo.claim_count} claims</div>
          <div class="shard-actions">
            <button class="shard-btn" on:click={browseAllClaims}>Browse</button>
            <button class="shard-btn" on:click={() => showStats = true}>Stats</button>
            <button class="shard-btn danger" on:click={unmountShard}>Unmount</button>
          </div>
        </div>
      {:else}
        <button class="mount-btn" on:click={mountShard}>+ Mount Knowledge Shard</button>
        {#if shardError}<div class="shard-error">{shardError}</div>{/if}
      {/if}
    </div>

    {#if shardMounted}
      <div class="filter-section">
        <div class="section-label">Confidence</div>
        <div class="tier-buttons">
          <button class="tier-btn" class:active={maxTier === 0} on:click={() => maxTier = maxTier === 0 ? null : 0}>T0</button>
          <button class="tier-btn" class:active={maxTier === 1} on:click={() => maxTier = maxTier === 1 ? null : 1}>T0–1</button>
          <button class="tier-btn" class:active={maxTier === null} on:click={() => maxTier = null}>All</button>
        </div>
      </div>
    {/if}

    <!-- Input -->
    <div class="input-section">
      <div class="input-label">{shardMounted ? "Query the Vault" : "Intent"}</div>
      <textarea
        bind:value={input}
        on:keydown={handleKeydown}
        placeholder={shardMounted ? "Ask anything — the shard proves it" : "What do you want to create?"}
        disabled={isProcessing}
        rows="5"
      ></textarea>
      <div class="input-actions">
        <button class="submit-btn" on:click={handleSubmit} disabled={isProcessing || !input.trim()}>
          {isProcessing ? "Querying..." : shardMounted ? "Query" : "Execute"}
        </button>
        <button class="submit-btn secondary" on:click={() => showSettings = !showSettings} title="Settings">⚙</button>
      </div>
    </div>

    <div class="sidebar-footer">
      <div class="stats">
        <span>{chronicle.length} nodes</span>
        <button class="icon-btn" class:has-items={trash.length > 0} on:click={() => showTrash = !showTrash}>
          🗑 {trash.length}
        </button>
      </div>
    </div>
  </aside>

  <!-- CHRONICLE -->
  <section class="chronicle" bind:this={chronicleContainer} on:scroll={handleScroll}>
    {#if chronicle.length === 0}
      <div class="empty-state">
        <div class="empty-logo">◇</div>
        <h2>The Chronicle</h2>
        <p>Everything accumulates. Nothing is silently replaced.</p>
        {#if !shardMounted}
          <p class="mount-hint">← Mount a knowledge shard to enable verified retrieval</p>
          <div class="suggestions">
            <button on:click={() => { input = "Visualize a sine wave"; handleSubmit(); }}>Visualize a sine wave</button>
            <button on:click={() => { input = "Write a haiku about sovereignty"; handleSubmit(); }}>Write a haiku</button>
          </div>
        {:else}
          <div class="suggestions">
            <button on:click={() => { input = "What treats severe bleeding?"; handleSubmit(); }}>What treats severe bleeding?</button>
            <button on:click={() => { input = "What is contraindicated with elevation?"; handleSubmit(); }}>Contraindications?</button>
            <button on:click={browseAllClaims}>Browse all claims</button>
          </div>
        {/if}
      </div>
    {:else}
      {#each chronicle as node (node.id)}
        {@const collapsed = collapsedNodes.has(node.id)}
        <div class="chronicle-node" class:has-verified={node.verified} id={node.id}>

          <div class="node-gutter">
            <span class="node-time">{formatTime(node.timestamp)}</span>
            {#if node.type !== "intent" && node.type !== "system"}
              <div class="node-actions">
                <button class="action-btn" on:click={() => toggleCollapse(node.id)}>{collapsed ? "▶" : "▼"}</button>
                <button class="action-btn" on:click={() => startAnnotation(node.id)}>✎</button>
                <button class="action-btn del" on:click={() => moveToTrash(node.id)}>✕</button>
              </div>
            {/if}
          </div>

          <div class="node-content">
            {#if collapsed && node.type !== "intent" && node.type !== "system"}
              <div class="collapsed-row" on:click={() => toggleCollapse(node.id)}>
                <span class="badge {node.type}">{node.type}</span>
                <span class="collapsed-preview">{node.title || node.content?.slice(0, 60) || "…"}</span>
              </div>

            {:else if node.type === "intent"}
              <div class="intent-label">Intent</div>
              <div class="intent-text">{node.content}</div>

            {:else if node.type === "system"}
              <div class="system-row">ℹ {node.content}</div>

            {:else if node.type === "error"}
              <div class="error-row">{node.content}</div>

            {:else if node.type === "chat"}
              <div class="node-header">
                <span class="badge">response</span>
                {#if node.verified}<span class="lock-icon" title="Grounded in verified shard">🔒</span>{/if}
              </div>
              <div class="chat-text">{node.content}</div>

            {:else if node.type === "citation"}
              <div class="node-header">
                <span class="badge citation">citation</span>
                <span class="claim-label">{node.subject} → {node.predicate} → {node.object}</span>
              </div>
              <div class="citation-block" on:click={() => openSourceViewer(node)}>
                <blockquote>"{node.evidence || `${node.subject} ${node.predicate} ${node.object}`}"</blockquote>
                <div class="citation-meta">
                  <span>bytes {node.byte_start}–{node.byte_end}</span>
                  <span class="verify-link">🔒 Verify source →</span>
                </div>
              </div>

            {:else if node.type === "sources"}
              <div class="sources-row">
                <span class="badge sources">sources</span>
                <span>{node.title}</span>
                <button class="expand-btn" on:click={() => toggleSourcesExpanded(node.id)}>
                  {node.expanded ? "Collapse" : "Expand"}
                </button>
              </div>
              {#if node.expanded}
                <div class="sources-list">
                  {#each node.claims as claim}
                    <div class="source-item" on:click={() => openSourceViewer(claim)}>
                      <span class="tier-pill">T{claim.tier}</span>
                      <span class="src-subject">{claim.subject}</span>
                      <span class="src-pred">{claim.predicate}</span>
                      <span class="src-obj">{claim.object}</span>
                    </div>
                  {/each}
                </div>
              {/if}

            {:else if node.type === "browse"}
              <div class="node-header">
                <span class="badge browse">browse</span>
                <span class="node-title">{node.title}</span>
                <span class="claim-label">{node.claims?.length || 0} claims</span>
              </div>
              <div class="claims-grid">
                {#each (node.claims || []) as claim}
                  <div class="claim-card" on:click={() => openSourceViewer(claim)}>
                    <div class="triple">
                      <span class="subj">{claim.subject}</span>
                      <span class="pred">{claim.predicate}</span>
                      <span class="obj">{claim.object}</span>
                    </div>
                    {#if claim.evidence}
                      <div class="claim-ev">"{claim.evidence.slice(0, 80)}{claim.evidence.length > 80 ? '…' : ''}"</div>
                    {/if}
                    <div class="claim-meta">
                      <span class="tier-pill">T{claim.tier}</span>
                      {#if claim.byte_start >= 0}<span class="has-src">🔒 {claim.byte_start}–{claim.byte_end}</span>{/if}
                    </div>
                  </div>
                {/each}
              </div>

            {:else if node.type === "viz"}
              <div class="node-header">
                <span class="badge viz">viz</span>
                {#if node.title}<span class="node-title">{node.title}</span>{/if}
              </div>
              {#if node.error}
                <div class="viz-wrap"><span class="viz-err">D3 error: {node.error}</span></div>
              {:else}
                <div class="viz-wrap" id="node-canvas-{node.id}"></div>
              {/if}

            {:else if node.type === "code"}
              <div class="node-header">
                <span class="badge code">code</span>
                {#if node.title}<span class="node-title">{node.title}</span>{/if}
                {#if node.lang}<span class="lang-tag">{node.lang}</span>{/if}
              </div>
              <pre class="code-block">{node.content}</pre>

            {:else if node.type === "write"}
              <div class="node-header">
                <span class="badge write">write</span>
                {#if node.title}<span class="node-title">{node.title}</span>{/if}
              </div>
              <div class="write-block">{node.content}</div>

            {:else}
              <div class="node-header">
                <span class="badge">{node.type}</span>
                {#if node.title}<span class="node-title">{node.title}</span>{/if}
              </div>
              <div class="chat-text">{node.content}</div>
            {/if}

            <!-- Annotation -->
            {#if editingAnnotation === node.id}
              <div class="ann-editor">
                <textarea bind:value={annotationText} rows="3" placeholder="Add a note…"></textarea>
                <div class="ann-actions">
                  <button class="ann-btn save" on:click={saveAnnotation}>Save</button>
                  <button class="ann-btn cancel" on:click={() => editingAnnotation = null}>Cancel</button>
                </div>
              </div>
            {:else if node.annotation}
              <div class="annotation" on:click={() => startAnnotation(node.id)}>✎ {node.annotation}</div>
            {/if}
          </div>
        </div>
      {/each}

      {#if isProcessing}
        <div class="chronicle-node">
          <div class="node-gutter"><span class="node-time">--:--</span></div>
          <div class="node-content">
            <div class="processing">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
            </div>
          </div>
        </div>
      {/if}
    {/if}

    {#if showNewNodesIndicator}
      <button class="scroll-indicator" on:click={scrollToBottom}>↓ New nodes</button>
    {/if}
  </section>

  <!-- SOURCE VERIFICATION PANEL -->
  {#if showSourceViewer && selectedSource}
    <aside class="panel source-panel">
      <header class="panel-header">
        <h3>🔒 Source Verification</h3>
        <button class="close-btn" on:click={() => showSourceViewer = false}>×</button>
      </header>
      <div class="panel-body">
        <div class="verify-status" class:ok={sourceVerified === true} class:fail={sourceVerified === false}>
          {#if sourceVerified === true}✓ VERIFIED — Evidence matches source bytes
          {:else if sourceVerified === false}✗ MISMATCH — Evidence does not match source
          {:else}○ Loading…{/if}
        </div>

        <div class="meta-rows">
          <div class="meta-row"><span class="ml">Source Hash</span><code class="mv">{selectedSource.source_hash?.slice(0,16) || 'N/A'}…</code></div>
          <div class="meta-row"><span class="ml">Byte Range</span><code class="mv">{selectedSource.byte_start} – {selectedSource.byte_end}</code></div>
          {#if selectedSource.subject}
            <div class="meta-row"><span class="ml">Claim</span><code class="mv">{selectedSource.subject} {selectedSource.predicate} {selectedSource.object}</code></div>
          {/if}
        </div>

        <div class="compare-block">
          <div class="compare-label">Claim Evidence</div>
          <blockquote class="evidence-q">{selectedSource.evidence || "No evidence text"}</blockquote>
        </div>
        <div class="compare-block">
          <div class="compare-label">Source bytes {selectedSource.byte_start}–{selectedSource.byte_end}</div>
          <blockquote class="source-q" class:match={sourceVerified === true}>{sourceContent || "Loading…"}</blockquote>
        </div>

        {#if sourceVerified === true}
          <div class="verify-confirm">This claim is backed by verified, byte-addressable evidence.</div>
        {/if}
      </div>
    </aside>
  {/if}

  <!-- STATS PANEL -->
  {#if showStats && shardStats}
    <aside class="panel stats-panel">
      <header class="panel-header">
        <h3>📊 Shard Statistics</h3>
        <button class="close-btn" on:click={() => showStats = false}>×</button>
      </header>
      <div class="panel-body">
        <div class="stat-grid">
          <div class="stat-card"><span class="sv">{shardStats.entities}</span><span class="sl">Entities</span></div>
          <div class="stat-card"><span class="sv">{shardStats.claims}</span><span class="sl">Claims</span></div>
          <div class="stat-card"><span class="sv">{shardStats.provenance_links}</span><span class="sl">Provenance</span></div>
          <div class="stat-card"><span class="sv">{shardStats.evidence_spans}</span><span class="sl">Spans</span></div>
        </div>
        <div class="tier-section">
          <div class="tier-head">Claims by Tier</div>
          {#each [{label:"Tier 0 (High)",key:"tier_0",c:"t0"},{label:"Tier 1 (Med)",key:"tier_1",c:"t1"},{label:"Tier 2 (Review)",key:"tier_2",c:"t2"}] as t}
            <div class="tier-row">
              <span class="tl">{t.label}</span>
              <div class="tbar"><div class="tfill {t.c}" style="width:{((shardStats.claims_by_tier?.[t.key]||0)/(shardStats.claims||1)*100)}%"></div></div>
              <span class="tc">{shardStats.claims_by_tier?.[t.key]||0}</span>
            </div>
          {/each}
        </div>
        <div class="pred-count"><span class="sv" style="font-size:1.1rem">{shardStats.unique_predicates}</span> unique predicates</div>
      </div>
    </aside>
  {/if}

  <!-- TRASH PANEL -->
  {#if showTrash}
    <aside class="panel trash-panel">
      <header class="panel-header">
        <h3>🗑 Trash</h3>
        <button class="close-btn" on:click={() => showTrash = false}>×</button>
      </header>
      {#if trash.length === 0}
        <div class="panel-empty">No deleted nodes</div>
      {:else}
        <div class="trash-list">
          {#each trash as node (node.id)}
            <div class="trash-item">
              <div>
                <span class="trash-type">{node.type}</span>
                <span class="trash-preview">{node.title || node.content?.slice(0,40) || "…"}</span>
              </div>
              <button class="restore-btn" on:click={() => restoreFromTrash(node.id)}>Restore</button>
            </div>
          {/each}
        </div>
        <button class="empty-btn" on:click={() => trash = []}>Empty Trash</button>
      {/if}
    </aside>
  {/if}

  <!-- SETTINGS PANEL -->
  {#if showSettings}
    <aside class="panel settings-panel">
      <header class="panel-header">
        <h3>⚙ Settings</h3>
        <button class="close-btn" on:click={() => showSettings = false}>×</button>
      </header>
      <div class="panel-body">
        <div class="setting-group">
          <label>Model</label>
          <select bind:value={selectedModel}>
            {#each availableModels as m}<option value={m}>{m}</option>{/each}
          </select>
        </div>
        <div class="setting-group">
          <label>Max Tier</label>
          <select bind:value={maxTier}>
            <option value={null}>All tiers</option>
            <option value={0}>Tier 0 only (highest confidence)</option>
            <option value={1}>Tier 0–1</option>
            <option value={2}>All tiers (0–2)</option>
          </select>
        </div>
        <div class="setting-info">
          <p><strong>Tier 0:</strong> Rule-based · confidence 1.0</p>
          <p><strong>Tier 1:</strong> Pattern-matched · high confidence</p>
          <p><strong>Tier 2:</strong> LLM-extracted · review recommended</p>
        </div>
      </div>
    </aside>
  {/if}
</main>

<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Syne:wght@400;500;600&display=swap');

  :global(*) { margin:0; padding:0; box-sizing:border-box; }
  :global(body) { background:#050505; color:#e5e5e5; font-family:'Syne',-apple-system,sans-serif; overflow:hidden; }

  .app { display:flex; height:100vh; width:100vw; }

  /* SIDEBAR */
  .sidebar { width:300px; min-width:300px; background:#080808; border-right:1px solid #111; display:flex; flex-direction:column; overflow-y:auto; }

  .sidebar-header { padding:18px 20px; border-bottom:1px solid #111; display:flex; justify-content:space-between; align-items:baseline; }
  .logo { font-size:0.95rem; font-weight:600; letter-spacing:0.04em; }
  .version { font-size:0.58rem; color:#2a2a2a; font-family:'JetBrains Mono',monospace; }

  .status-section { padding:10px 20px; border-bottom:1px solid #111; display:flex; justify-content:space-between; align-items:center; }
  .status { display:flex; align-items:center; gap:7px; font-size:0.65rem; font-family:'JetBrains Mono',monospace; color:#333; }
  .status-dot { width:5px; height:5px; background:#222; border-radius:50%; transition:all 0.3s; }
  .status.online .status-dot { background:#10b981; box-shadow:0 0 6px rgba(16,185,129,0.7); }
  .model-select { background:#0a0a0a; border:1px solid #1a1a1a; color:#555; font-size:0.62rem; padding:3px 6px; border-radius:3px; font-family:'JetBrains Mono',monospace; }

  .shard-section, .filter-section { padding:14px 20px; border-bottom:1px solid #111; }
  .section-label { font-size:0.52rem; font-family:'JetBrains Mono',monospace; color:#2a2a2a; text-transform:uppercase; letter-spacing:0.15em; margin-bottom:10px; }

  .shard-info { background:rgba(16,185,129,0.03); border:1px solid rgba(16,185,129,0.12); border-radius:7px; padding:11px; }
  .shard-title { font-weight:600; font-size:0.82rem; margin-bottom:5px; color:#e0e0e0; }
  .shard-meta { display:flex; gap:7px; align-items:center; margin-bottom:6px; }
  .namespace { font-size:0.58rem; font-family:'JetBrains Mono',monospace; color:#444; }
  .trust-badge { font-size:0.52rem; font-family:'JetBrains Mono',monospace; padding:2px 6px; border-radius:3px; }
  .trust-badge.verified { background:rgba(16,185,129,0.12); color:#10b981; }
  .trust-badge.partial  { background:rgba(251,191,36,0.12); color:#fbbf24; }
  .trust-badge.unverified { background:rgba(107,114,128,0.12); color:#6b7280; }
  .trust-badge.failed   { background:rgba(239,68,68,0.12); color:#ef4444; }
  .shard-counts { font-size:0.62rem; font-family:'JetBrains Mono',monospace; color:#444; margin-bottom:9px; }
  .shard-actions { display:flex; gap:5px; }
  .shard-btn { padding:4px 9px; background:#0d0d0d; border:1px solid #1a1a1a; border-radius:3px; cursor:pointer; font-size:0.65rem; color:#777; font-family:'JetBrains Mono',monospace; transition:all 0.15s; }
  .shard-btn:hover { background:#151515; color:#ddd; }
  .shard-btn.danger:hover { border-color:#ef4444; color:#ef4444; }

  .mount-btn { width:100%; padding:13px; background:#090909; border:1px dashed #1e1e1e; border-radius:7px; color:#333; cursor:pointer; font-size:0.78rem; font-family:'Syne',sans-serif; transition:all 0.2s; }
  .mount-btn:hover { border-color:#10b981; color:#10b981; background:rgba(16,185,129,0.03); }
  .shard-error { margin-top:7px; font-size:0.65rem; color:#ef4444; font-family:'JetBrains Mono',monospace; }

  .tier-buttons { display:flex; gap:5px; }
  .tier-btn { flex:1; padding:6px; background:#0a0a0a; border:1px solid #181818; border-radius:3px; color:#333; font-size:0.62rem; cursor:pointer; font-family:'JetBrains Mono',monospace; transition:all 0.15s; }
  .tier-btn:hover { color:#777; }
  .tier-btn.active { background:rgba(16,185,129,0.07); border-color:rgba(16,185,129,0.25); color:#10b981; }

  .input-section { padding:14px 20px; flex:1; display:flex; flex-direction:column; }
  .input-label { font-size:0.52rem; font-family:'JetBrains Mono',monospace; color:#2a2a2a; text-transform:uppercase; letter-spacing:0.15em; margin-bottom:8px; }
  .input-section textarea { flex:1; min-height:90px; background:#090909; border:1px solid #111; border-radius:7px; padding:11px; font-size:0.82rem; color:#e5e5e5; resize:none; font-family:'Syne',sans-serif; line-height:1.5; transition:border-color 0.15s; }
  .input-section textarea:focus { outline:none; border-color:#10b981; }
  .input-section textarea::placeholder { color:#202020; }
  .input-actions { display:flex; gap:7px; margin-top:9px; }
  .submit-btn { flex:1; padding:10px; background:#10b981; border:none; border-radius:5px; font-weight:600; color:#000; cursor:pointer; font-size:0.78rem; font-family:'Syne',sans-serif; transition:all 0.15s; }
  .submit-btn:hover:not(:disabled) { background:#0ea872; }
  .submit-btn:disabled { background:#0e0e0e; color:#2a2a2a; cursor:not-allowed; }
  .submit-btn.secondary { flex:none; width:38px; background:#0d0d0d; border:1px solid #181818; color:#333; font-size:0.8rem; }
  .submit-btn.secondary:hover:not(:disabled) { background:#111; color:#777; }

  .sidebar-footer { padding:12px 20px; border-top:1px solid #111; }
  .stats { display:flex; justify-content:space-between; align-items:center; font-size:0.6rem; font-family:'JetBrains Mono',monospace; color:#2a2a2a; }
  .icon-btn { background:none; border:none; color:#2a2a2a; cursor:pointer; font-size:0.65rem; padding:3px 5px; border-radius:3px; }
  .icon-btn:hover { background:#111; color:#777; }
  .icon-btn.has-items { color:#f59e0b; }

  /* CHRONICLE */
  .chronicle { flex:1; overflow-y:auto; padding:36px 44px; position:relative; background:#050505; }

  .empty-state { display:flex; flex-direction:column; align-items:center; justify-content:center; height:100%; text-align:center; color:#2a2a2a; }
  .empty-logo { font-size:3rem; margin-bottom:20px; opacity:0.12; }
  .empty-state h2 { font-size:1.4rem; margin-bottom:8px; color:#444; font-weight:400; }
  .empty-state p { color:#2a2a2a; font-size:0.82rem; line-height:1.6; }
  .mount-hint { color:#10b981 !important; margin-top:14px; }
  .suggestions { display:flex; gap:8px; margin-top:20px; flex-wrap:wrap; justify-content:center; }
  .suggestions button { padding:8px 14px; background:#090909; border:1px solid #181818; border-radius:5px; color:#444; font-size:0.78rem; cursor:pointer; font-family:'Syne',sans-serif; transition:all 0.15s; }
  .suggestions button:hover { border-color:#10b981; color:#10b981; }

  /* NODES */
  .chronicle-node { display:flex; gap:14px; padding:12px 0; border-bottom:1px solid #090909; }
  .chronicle-node.has-verified { border-left:2px solid rgba(16,185,129,0.35); padding-left:12px; margin-left:-14px; }

  .node-gutter { width:48px; min-width:48px; display:flex; flex-direction:column; align-items:flex-end; gap:5px; }
  .node-time { font-size:0.55rem; font-family:'JetBrains Mono',monospace; color:#181818; }
  .node-actions { display:flex; gap:1px; opacity:0; transition:opacity 0.15s; }
  .chronicle-node:hover .node-actions { opacity:1; }
  .action-btn { width:18px; height:18px; background:none; border:none; color:#222; cursor:pointer; font-size:0.6rem; border-radius:2px; display:flex; align-items:center; justify-content:center; }
  .action-btn:hover { background:#111; color:#777; }
  .action-btn.del:hover { color:#ef4444; }

  .node-content { flex:1; min-width:0; }

  .collapsed-row { display:flex; align-items:center; gap:8px; padding:7px 10px; background:#090909; border:1px solid #101010; border-radius:4px; cursor:pointer; }
  .collapsed-row:hover { background:#0c0c0c; }
  .collapsed-preview { flex:1; font-size:0.75rem; color:#333; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

  .badge { display:inline-flex; align-items:center; padding:2px 6px; font-size:0.48rem; font-family:'JetBrains Mono',monospace; text-transform:uppercase; letter-spacing:0.07em; border-radius:2px; background:#111; color:#333; }
  .badge.viz      { background:rgba(59,130,246,0.09); color:#3b82f6; }
  .badge.code     { background:rgba(16,185,129,0.09); color:#10b981; }
  .badge.write    { background:rgba(168,85,247,0.09); color:#a855f7; }
  .badge.citation { background:rgba(16,185,129,0.11); color:#10b981; }
  .badge.browse   { background:rgba(251,191,36,0.09); color:#fbbf24; }
  .badge.sources  { background:rgba(99,102,241,0.09); color:#6366f1; }

  .node-header { display:flex; align-items:center; gap:7px; margin-bottom:9px; flex-wrap:wrap; }
  .node-title { font-size:0.75rem; color:#555; }
  .lang-tag { font-size:0.52rem; font-family:'JetBrains Mono',monospace; color:#333; background:#0d0d0d; padding:1px 5px; border-radius:2px; }
  .lock-icon { font-size:0.8rem; }
  .claim-label { font-size:0.6rem; color:#333; font-family:'JetBrains Mono',monospace; }

  .system-row { display:flex; align-items:center; gap:7px; padding:7px 10px; background:rgba(99,102,241,0.04); border-radius:4px; font-size:0.7rem; color:#5558c8; font-family:'JetBrains Mono',monospace; }
  .error-row { padding:10px 12px; background:rgba(239,68,68,0.06); border:1px solid rgba(239,68,68,0.12); border-radius:5px; color:#f87171; font-size:0.75rem; font-family:'JetBrains Mono',monospace; }

  .intent-label { font-size:0.48rem; font-family:'JetBrains Mono',monospace; color:#222; text-transform:uppercase; letter-spacing:0.15em; margin-bottom:4px; }
  .intent-text { font-size:0.92rem; color:#e5e5e5; line-height:1.5; }
  .chat-text { font-size:0.87rem; line-height:1.7; color:#999; white-space:pre-wrap; }

  .citation-block { background:rgba(16,185,129,0.025); border:1px solid rgba(16,185,129,0.1); border-radius:6px; padding:12px; cursor:pointer; transition:all 0.15s; }
  .citation-block:hover { background:rgba(16,185,129,0.05); border-color:rgba(16,185,129,0.18); }
  .citation-block blockquote { font-size:0.87rem; color:#aaa; border-left:2px solid #10b981; padding-left:11px; margin:0; font-style:italic; line-height:1.5; }
  .citation-meta { display:flex; gap:12px; margin-top:9px; font-size:0.57rem; font-family:'JetBrains Mono',monospace; color:#333; }
  .verify-link { margin-left:auto; color:#3b82f6; }

  .sources-row { display:flex; align-items:center; gap:7px; padding:7px 10px; background:rgba(99,102,241,0.03); border-radius:4px; font-size:0.78rem; color:#6a6df0; }
  .expand-btn { margin-left:auto; background:none; border:1px solid #1e1e1e; color:#444; padding:2px 7px; border-radius:3px; font-size:0.6rem; cursor:pointer; font-family:'JetBrains Mono',monospace; }
  .expand-btn:hover { border-color:#6366f1; color:#6366f1; }
  .sources-list { margin-top:6px; display:flex; flex-direction:column; gap:4px; }
  .source-item { padding:7px 9px; background:#080808; border:1px solid #0f0f0f; border-radius:4px; font-size:0.72rem; color:#666; cursor:pointer; display:flex; align-items:center; gap:7px; transition:all 0.15s; }
  .source-item:hover { background:#0a0a0a; border-color:#1a1a1a; }
  .src-subject { color:#ddd; font-weight:500; }
  .src-pred { color:#10b981; }
  .tier-pill { font-size:0.52rem; background:#0e0e0e; padding:1px 5px; border-radius:2px; font-family:'JetBrains Mono',monospace; color:#444; }

  .claims-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:8px; margin-top:9px; }
  .claim-card { background:#080808; border:1px solid #0f0f0f; border-radius:6px; padding:10px; cursor:pointer; transition:all 0.15s; }
  .claim-card:hover { background:#0a0a0a; border-color:rgba(16,185,129,0.15); }
  .triple { display:flex; flex-wrap:wrap; gap:4px; margin-bottom:6px; font-size:0.77rem; }
  .subj { color:#e0e0e0; font-weight:500; }
  .pred { color:#10b981; }
  .obj  { color:#666; }
  .claim-ev { font-size:0.67rem; color:#333; font-style:italic; margin-bottom:6px; line-height:1.4; }
  .claim-meta { display:flex; gap:7px; font-size:0.57rem; color:#222; align-items:center; }
  .has-src { color:#10b981; }

  .viz-wrap { background:#080808; border:1px solid #0f0f0f; border-radius:6px; padding:14px; min-height:160px; display:flex; align-items:center; justify-content:center; }
  .viz-err { color:#ef4444; font-size:0.72rem; font-family:'JetBrains Mono',monospace; }
  :global(.viz-wrap svg) { max-width:100%; max-height:400px; }

  .code-block { background:#080808; border:1px solid #0f0f0f; border-radius:6px; padding:12px; overflow-x:auto; font-family:'JetBrains Mono',monospace; font-size:0.72rem; color:#10b981; line-height:1.5; }
  .write-block { background:#080808; border:1px solid #0f0f0f; border-radius:6px; padding:16px; font-size:0.87rem; line-height:1.8; color:#aaa; white-space:pre-wrap; }

  .annotation { margin-top:9px; padding:7px 11px; background:rgba(167,139,250,0.03); border-left:2px solid rgba(167,139,250,0.15); border-radius:0 4px 4px 0; font-size:0.77rem; color:#9b7fd4; cursor:pointer; }
  .annotation:hover { background:rgba(167,139,250,0.06); }
  .ann-editor { margin-top:9px; padding:9px; background:#090909; border:1px solid #111; border-radius:4px; }
  .ann-editor textarea { width:100%; background:#0c0c0c; border:1px solid #1a1a1a; border-radius:3px; padding:7px; font-size:0.77rem; color:#e5e5e5; resize:none; margin-bottom:7px; font-family:'Syne',sans-serif; }
  .ann-actions { display:flex; gap:5px; }
  .ann-btn { padding:4px 10px; border:none; border-radius:3px; font-size:0.65rem; cursor:pointer; font-family:'Syne',sans-serif; }
  .ann-btn.save { background:#3b82f6; color:#fff; }
  .ann-btn.cancel { background:#111; color:#555; }

  .processing { display:flex; gap:4px; padding:9px 0; }
  .dot { width:6px; height:6px; background:#1a1a1a; border-radius:50%; animation:pulse 1.4s infinite ease-in-out; }
  .dot:nth-child(2) { animation-delay:0.2s; }
  .dot:nth-child(3) { animation-delay:0.4s; }
  @keyframes pulse { 0%,80%,100%{opacity:0.2;transform:scale(0.8);}40%{opacity:1;transform:scale(1);} }

  .scroll-indicator { position:fixed; bottom:22px; left:50%; transform:translateX(-50%); padding:8px 16px; background:#10b981; border:none; border-radius:16px; color:#000; font-size:0.72rem; font-weight:600; cursor:pointer; z-index:100; box-shadow:0 4px 14px rgba(16,185,129,0.25); font-family:'Syne',sans-serif; }

  /* PANELS */
  .panel { position:absolute; top:0; right:0; width:400px; height:100%; background:#080808; border-left:1px solid #111; display:flex; flex-direction:column; z-index:200; box-shadow:-6px 0 28px rgba(0,0,0,0.7); }
  .panel-header { padding:16px 18px; border-bottom:1px solid #111; display:flex; justify-content:space-between; align-items:center; }
  .panel-header h3 { font-size:0.85rem; font-weight:600; }
  .close-btn { background:none; border:none; color:#333; font-size:1.3rem; cursor:pointer; width:26px; height:26px; display:flex; align-items:center; justify-content:center; border-radius:3px; }
  .close-btn:hover { background:#111; color:#ddd; }
  .panel-body { padding:18px; flex:1; overflow-y:auto; }
  .panel-empty { padding:36px; text-align:center; color:#1e1e1e; }

  .source-panel .panel-header h3 { color:#10b981; }
  .verify-status { padding:9px 12px; border-radius:4px; font-size:0.7rem; font-family:'JetBrains Mono',monospace; margin-bottom:16px; background:#0a0a0a; color:#444; }
  .verify-status.ok   { background:rgba(16,185,129,0.07); color:#10b981; }
  .verify-status.fail { background:rgba(239,68,68,0.07); color:#ef4444; }
  .meta-rows { margin-bottom:16px; }
  .meta-row { display:flex; gap:9px; margin-bottom:7px; font-size:0.7rem; align-items:baseline; }
  .ml { color:#333; min-width:80px; }
  .mv { font-family:'JetBrains Mono',monospace; color:#10b981; font-size:0.65rem; word-break:break-all; }
  .compare-block { margin-bottom:14px; }
  .compare-label { font-size:0.55rem; text-transform:uppercase; color:#333; margin-bottom:5px; font-family:'JetBrains Mono',monospace; letter-spacing:0.08em; }
  .evidence-q, .source-q { background:#090909; border:1px solid #0f0f0f; border-radius:4px; padding:10px; font-size:0.82rem; color:#666; font-style:italic; line-height:1.5; }
  .source-q.match { border-color:rgba(16,185,129,0.2); color:#10b981; }
  .verify-confirm { margin-top:14px; padding:10px; background:rgba(16,185,129,0.05); border-radius:4px; font-size:0.75rem; color:#10b981; text-align:center; font-family:'JetBrains Mono',monospace; }

  .stat-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:9px; margin-bottom:18px; }
  .stat-card { background:#0d0d0d; border:1px solid #111; border-radius:6px; padding:12px; text-align:center; }
  .sv { display:block; font-size:1.3rem; font-weight:600; color:#fff; margin-bottom:3px; }
  .sl { font-size:0.52rem; color:#333; text-transform:uppercase; font-family:'JetBrains Mono',monospace; letter-spacing:0.1em; }
  .tier-section { margin-bottom:14px; }
  .tier-head { font-size:0.7rem; color:#666; margin-bottom:9px; font-weight:500; }
  .tier-row { display:flex; align-items:center; gap:9px; margin-bottom:7px; }
  .tl { width:85px; font-size:0.65rem; color:#444; }
  .tbar { flex:1; height:5px; background:#111; border-radius:3px; overflow:hidden; }
  .tfill { height:100%; border-radius:3px; transition:width 0.4s; }
  .tfill.t0 { background:#10b981; }
  .tfill.t1 { background:#fbbf24; }
  .tfill.t2 { background:#f87171; }
  .tc { width:28px; text-align:right; font-size:0.6rem; font-family:'JetBrains Mono',monospace; color:#555; }
  .pred-count { padding:9px; background:#0d0d0d; border-radius:4px; text-align:center; color:#555; font-size:0.77rem; }

  .trash-list { flex:1; overflow-y:auto; padding:9px; }
  .trash-item { display:flex; justify-content:space-between; align-items:center; padding:9px 11px; background:#0d0d0d; border:1px solid #111; border-radius:4px; margin-bottom:5px; }
  .trash-type { font-size:0.52rem; font-family:'JetBrains Mono',monospace; color:#333; text-transform:uppercase; display:block; margin-bottom:2px; }
  .trash-preview { font-size:0.7rem; color:#555; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; display:block; max-width:220px; }
  .restore-btn { padding:4px 9px; border:none; border-radius:3px; font-size:0.6rem; cursor:pointer; background:#111; color:#10b981; flex-shrink:0; margin-left:9px; }
  .restore-btn:hover { background:rgba(16,185,129,0.07); }
  .empty-btn { margin:9px; padding:9px; background:#0d0d0d; border:1px solid #111; color:#ef4444; border-radius:4px; font-size:0.7rem; cursor:pointer; width:calc(100% - 18px); font-family:'Syne',sans-serif; }
  .empty-btn:hover { background:#110a0a; border-color:#ef4444; }

  .setting-group { margin-bottom:16px; }
  .setting-group label { display:block; font-size:0.6rem; color:#555; margin-bottom:6px; text-transform:uppercase; font-family:'JetBrains Mono',monospace; letter-spacing:0.1em; }
  .setting-group select { width:100%; padding:8px 9px; background:#0d0d0d; border:1px solid #1a1a1a; border-radius:4px; color:#e5e5e5; font-size:0.82rem; font-family:'Syne',sans-serif; }
  .setting-info { background:#0d0d0d; border-radius:4px; padding:11px; font-size:0.7rem; color:#444; line-height:1.6; }
  .setting-info p { margin-bottom:4px; }
  .setting-info strong { color:#666; }

  .chronicle::-webkit-scrollbar, .panel-body::-webkit-scrollbar { width:5px; }
  .chronicle::-webkit-scrollbar-track, .panel-body::-webkit-scrollbar-track { background:transparent; }
  .chronicle::-webkit-scrollbar-thumb, .panel-body::-webkit-scrollbar-thumb { background:#111; border-radius:3px; }
  .chronicle::-webkit-scrollbar-thumb:hover, .panel-body::-webkit-scrollbar-thumb:hover { background:#1a1a1a; }
</style>
