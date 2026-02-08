<script>
  import { invoke } from "@tauri-apps/api/tauri";
  import { open } from "@tauri-apps/api/dialog";
  import { onMount, afterUpdate, tick } from "svelte";

  // State
  let input = "";
  let isProcessing = false;
  let cortexStatus = "checking...";
  
  // Shard state (NEW)
  let shardMounted = false;
  let shardInfo = null;
  let shardStats = null;
  let showStats = false;
  
  // Source verification panel (NEW)
  let showSourceViewer = false;
  let selectedSource = null;
  let sourceContent = "";
  let sourceVerified = null;
  
  // The Chronicle: Everything accumulates here
  let chronicle = [];
  let trash = []; // Deleted nodes go here for recovery
  let chronicleContainer;
  let showNewNodesIndicator = false;
  let userScrolledUp = false;
  let lastChronicleLen = 0;
  
  // Collapsing: nodes older than threshold auto-collapse to prevent DOM death
  let expandedNodes = new Set();   // Manual expand override (prevents auto-collapse)
  let collapsedNodes = new Set();  // Manual collapse override (forces collapse)
  const COLLAPSE_THRESHOLD = 20;   // Nodes beyond this from the end are collapsed
  
  function isNodeCollapsed(node, index) {
    // Manual force-collapse always wins
    if (collapsedNodes.has(node.id)) return true;

    // Never collapse short system nodes
    if (node.type === "intent" || node.type === "error" || node.type === "system") return false;

    // Manual expand override blocks auto-collapse
    if (expandedNodes.has(node.id)) return false;

    // Collapse policy based on age
    const distanceFromEnd = chronicle.length - 1 - index;
    const tooOld = distanceFromEnd > COLLAPSE_THRESHOLD;

    if (!tooOld) return false;

    // Chat can be long and DOM-killing
    if (node.type === "chat") {
      return (node.content || "").length > 400;
    }

    // Heavy nodes collapse by default when old
    return (
      node.type === "viz" ||
      node.type === "code" ||
      node.type === "write" ||
      node.type === "audio" ||
      node.type === "image" ||
      node.type === "citation"
    );
  }
  
  function toggleSet(setRef, nodeId) {
    const next = new Set(setRef);
    if (next.has(nodeId)) next.delete(nodeId);
    else next.add(nodeId);
    return next;
  }

  async function expandNode(node) {
    // Expanding should clear forced-collapse
    collapsedNodes = (() => {
      const next = new Set(collapsedNodes);
      next.delete(node.id);
      return next;
    })();

    // Add to expanded set
    expandedNodes = toggleSet(expandedNodes, node.id);
    await tick();

    // If this is a viz node, the container now exists
    if (node.type === "viz") {
      executeD3(node.id, node.content);
    }
  }

  async function collapseNode(node) {
    // Collapsing should clear manual expand override
    expandedNodes = (() => {
      const next = new Set(expandedNodes);
      next.delete(node.id);
      return next;
    })();

    // Add to collapsed set
    collapsedNodes = toggleSet(collapsedNodes, node.id);
    await tick();
  }

  // The System Prompt - Revised promise: history preserved, revisions allowed
  const SYSTEM_PROMPT = `You are Nodal Flow, a sovereign local-first AI interface.
You run entirely on the user's hardware. You are not a cloud service.

THE CHRONICLE MODEL:
Everything the user creates accumulates on an infinite scroll.
Nothing is silently replaced. Revisions create new nodes and preserve history.
Each response becomes a permanent node in their timeline unless they explicitly delete it.

RESPONSE RULES:

1. VISUALIZATION requests (graph, chart, plot, diagram, visualize):
   - Output valid D3.js v7 code that creates an SVG
   - Wrap in <NODE type="viz" title="...">...</NODE>
   - Use the pre-defined variables: container (d3 selection), width (560), height (360)
   - Start your code with: const svg = container.append("svg").attr("width", width).attr("height", height);
   - Keep visualizations self-contained

2. CODE requests (write code, script, function):
   - Output clean, working code
   - Wrap in <NODE type="code" lang="..." title="...">...</NODE>
   - Include the language attribute

3. WRITING requests (write, draft, compose text, essay, email):
   - Output markdown-formatted text
   - Wrap in <NODE type="write" title="...">...</NODE>

4. AUDIO/MUSIC requests (beat, melody, song):
   - Describe what you would generate
   - Wrap in <NODE type="audio" title="...">...</NODE>
   - (Audio generation coming in v0.2)

5. IMAGE requests (image, picture, illustration, cover art):
   - Describe what you would generate
   - Wrap in <NODE type="image" title="...">...</NODE>
   - (Image generation coming in v0.2)

6. REVISION requests (edit, change, update a previous node):
   - Create a NEW node with the revision
   - Reference the original with: derives_from="[original title or description]"
   - Wrap in <NODE type="..." title="..." derives_from="...">...</NODE>

7. CHAT responses (questions, conversation):
   - Respond naturally in plain text
   - No NODE tags needed

CRITICAL: Always include a descriptive title attribute. This helps the user scan their chronicle.

You are sovereign. You are local. Everything accumulates. History is preserved.`;

  onMount(async () => {
    try {
      cortexStatus = await invoke("check_cortex_status");
    } catch (e) {
      cortexStatus = "⚠️ Offline - run: ollama serve";
    }
    
    // Run health check
    runDoctor();
    
    // Global keyboard handler
    const handleGlobalKeydown = (e) => {
      if (e.key === "Escape") {
        if (showTrash) showTrash = false;
        if (editingAnnotation) cancelAnnotation();
        if (showSourceViewer) showSourceViewer = false;
        if (showStats) showStats = false;
      }
    };
    window.addEventListener('keydown', handleGlobalKeydown);
    
    return () => {
      window.removeEventListener('keydown', handleGlobalKeydown);
    };
  });

  // Gated autoscroll: only scroll if user is near bottom
  afterUpdate(async () => {
    if (!chronicleContainer) return;

    const grew = chronicle.length > lastChronicleLen;
    lastChronicleLen = chronicle.length;

    if (!userScrolledUp) {
      await tick();
      chronicleContainer.scrollTop = chronicleContainer.scrollHeight;
      showNewNodesIndicator = false;
      return;
    }

    if (grew) {
      showNewNodesIndicator = true;
    }
  });

  function handleScroll() {
    if (!chronicleContainer) return;
    
    const { scrollTop, scrollHeight, clientHeight } = chronicleContainer;
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    
    // User is "near bottom" if within 100px
    userScrolledUp = distanceFromBottom > 100;
    
    if (!userScrolledUp) {
      showNewNodesIndicator = false;
    }
  }

  function scrollToBottom() {
    if (chronicleContainer) {
      chronicleContainer.scrollTop = chronicleContainer.scrollHeight;
      userScrolledUp = false;
      showNewNodesIndicator = false;
    }
  }

  // ============================================================================
  // SHARD MANAGEMENT (NEW)
  // ============================================================================

  async function mountShard() {
    const selected = await open({
      directory: true,
      title: "Select AXM Shard Directory"
    });
    
    if (selected) {
      try {
        shardInfo = await invoke("mount_vault", { path: selected });
        shardMounted = true;
        
        // Add system notification to chronicle
        addNode({
          type: "system",
          content: `📦 Mounted shard: ${shardInfo.title} (${shardInfo.claim_count} claims, ${shardInfo.entity_count} entities)`
        });
        
        
        // Load initial claims
        const initialClaims = await invoke("get_all_claims", { max_tier: 2, limit: 100 });
        for (const claim of initialClaims) {
          addNode({
            type: "citation",
            claim,
            content: `${claim.subject} ${claim.predicate} ${claim.object}`,
          });
        }

        // Load stats
        shardStats = await invoke("get_statistics");
        
      } catch (e) {
        addNode({ 
          type: "error", 
          content: `Failed to mount shard: ${e}` 
        });
      }
    }
  }

  async function unmountShard() {
    try {
      await invoke("unmount_vault");
      shardMounted = false;
      shardInfo = null;
      shardStats = null;
      
      addNode({
        type: "system",
        content: "⏏️ Shard unmounted"
      });
    } catch (e) {
      addNode({ 
        type: "error", 
        content: `Failed to unmount: ${e}` 
      });
    }
  }

  // Document drop → Forge → Genesis → Shard
  let isCreatingShard = false;
  let isDragging = false;

  async function handleDocumentDrop(e) {
    e.preventDefault();
    isDragging = false;
    
    const files = e.dataTransfer?.files;
    if (!files || files.length === 0) return;
    
    const file = files[0];
    const filePath = file.path; // Tauri provides this
    
    if (!filePath) {
      addNode({ type: "error", content: "Could not get file path from drop" });
      return;
    }
    
    isCreatingShard = true;
    addNode({ type: "system", content: `🔨 Creating shard from: ${file.name}...` });
    
    try {
      const outputDir = `./shards/${file.name.replace(/\.[^/.]+$/, "")}`;
      
      const shardPath = await invoke("create_shard_from_document", {
        doc_path: filePath,
        output_dir: outputDir,
        namespace: "axm:user",
        publisher_id: "@nodal-flow",
        publisher_name: "Nodal Flow User",
      });
      
      addNode({ type: "system", content: `✓ Shard created: ${shardPath}` });
      
      // Auto-mount the new shard
      shardInfo = await invoke("mount_vault", { path: shardPath });
      shardMounted = true;
      
      // Load claims
      const initialClaims = await invoke("get_all_claims", { max_tier: 2, limit: 100 });
      for (const claim of initialClaims) {
        addNode({
          type: "citation",
          claim: claim,
          content: `${claim.subject} → ${claim.predicate} → ${claim.object}`,
        });
      }
      
      addNode({ type: "system", content: `📦 Mounted: ${shardInfo.title || 'Shard'} (${initialClaims.length} claims)` });
      
    } catch (e) {
      addNode({ type: "error", content: `Shard creation failed: ${e}` });
    } finally {
      isCreatingShard = false;
    }
  }

  // Doctor check on startup
  async function runDoctor() {
    try {
      const health = await invoke("doctor");
      console.log("AXM Stack Health:", health);
      if (!health.all_ok) {
        addNode({ 
          type: "error", 
          content: `⚠️ Stack health check: Python=${health.python_version}, Forge=${health.forge_importable ? '✓' : '✗'}, Genesis=${health.genesis_build_importable && health.genesis_verify_importable ? '✓' : '✗'}` 
        });
      }
    } catch (e) {
      console.error("Doctor failed:", e);
    }
  }

  async function toggleStats() {
    if (!shardMounted) return;
    
    if (!showStats) {
      try {
        shardStats = await invoke("get_statistics");
        showStats = true;
      } catch (e) {
        addNode({ 
          type: "error", 
          content: `Failed to load stats: ${e}` 
        });
      }
    } else {
      showStats = false;
    }
  }

  // ============================================================================
  // QUERY HANDLING (UPDATED FOR VAULT)
  // ============================================================================

  async function handleSubmit() {
    if (!input.trim() || isProcessing) return;
    
    const userMessage = input.trim();
    const requestTimestamp = new Date();
    input = "";
    isProcessing = true;

    // Build all new nodes in a batch
    let newNodes = [];

    // Add user intent
    newNodes.push({
      id: generateId(),
      type: "intent",
      content: userMessage,
      timestamp: requestTimestamp,
    });

    try {
      if (shardMounted) {
        // VAULT-FIRST MODE: Search the vault for relevant claims
        try {
          const vaultResults = await invoke("query_vault", { 
            search_term: userMessage, 
            max_tier: 2, 
            limit: 20 
          });
          
          if (vaultResults.length > 0) {
            // Add citation nodes for each claim
            for (const claim of vaultResults) {
              newNodes.push({
                id: generateId(),
                type: "citation",
                claim: claim,
                content: `${claim.subject} ${claim.predicate} ${claim.object}`,
                evidence: claim.evidence,
                title: `${claim.subject} ${claim.predicate} ${claim.object}`,
                source_hash: claim.source_hash,
                byte_start: claim.byte_start,
                byte_end: claim.byte_end,
                tier: claim.tier,
                timestamp: requestTimestamp,
              });
            }
          }
        } catch (vaultErr) {
          // Vault query failed, note it but continue to LLM
          newNodes.push({
            id: generateId(),
            type: "error",
            content: `Vault query failed: ${vaultErr}`,
            timestamp: requestTimestamp,
          });
        }
      }
      
      // Then query Ollama for synthesis (whether or not we found vault results)
      const contextPrompt = buildPrompt(userMessage);
      const response = await invoke("query_ollama", { prompt: contextPrompt });
      
      // Parse response and add to batch
      const parsedNodes = parseResponse(response, requestTimestamp);
      newNodes = [...newNodes, ...parsedNodes];
      
    } catch (e) {
      newNodes.push({
        id: generateId(),
        type: "error",
        content: `Cortex error: ${e}`,
        timestamp: requestTimestamp,
      });
    }

    // Single batch append
    chronicle = [...chronicle, ...newNodes.map(normalizeNode)];
    
    // Execute D3 for any viz nodes after DOM update
    await tick();
    for (const node of newNodes) {
      if (node.type === "viz") {
        executeD3(node.id, node.content);
      }
    }
    
    isProcessing = false;
  }

  // ============================================================================
  // SOURCE VERIFICATION (NEW)
  // ============================================================================

  async function verifyClaim(claim) {
    selectedSource = claim;
    showSourceViewer = true;
    sourceVerified = null;
    sourceContent = "Loading...";
    
    try {
      // Get the actual bytes from the source
      sourceContent = await invoke("get_content_slice", {
        source_hash: claim.source_hash,
        byte_start: claim.byte_start,
        byte_end: claim.byte_end
      });
      
      // Verify match
      sourceVerified = await invoke("verify_claim", { claim });
    } catch (e) {
      sourceVerified = false;
      sourceContent = `Error retrieving source: ${e}`;
    }
  }

  function closeSourceViewer() {
    showSourceViewer = false;
    selectedSource = null;
    sourceContent = "";
    sourceVerified = null;
  }

  // ============================================================================
  // HELPER FUNCTIONS (EXISTING + UPDATED)
  // ============================================================================

  function buildPrompt(userMessage) {
    // Include recent chronicle context (last 6 items)
    const recentContext = chronicle.slice(-6).map(node => {
      if (node.type === "intent") return `User: ${node.content}`;
      if (node.type === "chat") return `Assistant: ${node.content}`;
      if (node.type === "citation") return `[Citation: ${node.title}]`;
      if (node.title) return `[${node.type}: "${node.title}"]`;
      return `[${node.type}]`;
    }).join("\n");

    return `${SYSTEM_PROMPT}

${recentContext ? "Recent chronicle:\n" + recentContext + "\n" : ""}
User: ${userMessage}
Assistant:`;
  }

  function parseResponse(response, timestamp) {
    const nodes = [];
    
    // More flexible NODE tag parsing
    const nodePattern = /<NODE\s+([^>]+)>([\s\S]*?)<\/NODE>/g;
    let match;
    let lastIndex = 0;
    
    while ((match = nodePattern.exec(response)) !== null) {
      // Capture any text before this node as chat
      const textBefore = response.slice(lastIndex, match.index).trim();
      if (textBefore) {
        nodes.push({
          id: generateId(),
          type: "chat",
          content: textBefore,
          timestamp: timestamp,
        });
      }
      
      // Parse attributes flexibly
      const attrString = match[1];
      const content = match[2].trim();
      
      const typeMatch = attrString.match(/type="(\w+)"/);
      const langMatch = attrString.match(/lang="(\w+)"/);
      const titleMatch = attrString.match(/title="([^"]*)"/);
      const derivesMatch = attrString.match(/derives_from="([^"]*)"/);
      
      if (typeMatch) {
        nodes.push({
          id: generateId(),
          type: typeMatch[1],
          lang: langMatch ? langMatch[1] : null,
          title: titleMatch ? titleMatch[1] : null,
          derivesFrom: derivesMatch ? derivesMatch[1] : null,
          content: content,
          timestamp: timestamp,
        });
      }
      
      lastIndex = match.index + match[0].length;
    }
    
    // Capture any remaining text as chat
    const remaining = response.slice(lastIndex).trim();
    if (remaining) {
      nodes.push({
        id: generateId(),
        type: "chat",
        content: remaining,
        timestamp: timestamp,
      });
    }
    
    // If no nodes were parsed, treat entire response as chat
    if (nodes.length === 0) {
      nodes.push({
        id: generateId(),
        type: "chat",
        content: response,
        timestamp: timestamp,
      });
    }
    
    return nodes;
  }

  function addNode(nodeData) {
    const node = normalizeNode({
      id: generateId(),
      timestamp: new Date(),
      ...nodeData
    });
    chronicle = [...chronicle, node];
  }

  function normalizeNode(node) {
    // Handle both derivesFrom and derives_from
    if (node.derives_from && !node.derivesFrom) {
      node.derivesFrom = node.derives_from;
      delete node.derives_from;
    }
    return node;
  }

  function generateId() {
    return `node_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }

  function formatTimestamp(ts) {
    return ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  // D3 execution (existing)
  function executeD3(nodeId, code) {
    try {
      const container = d3.select(`#viz-${nodeId}`);
      const width = 560;
      const height = 360;
      
      // Clear previous content
      container.selectAll("*").remove();
      
      // Execute the D3 code
      eval(code);
    } catch (e) {
      const container = document.getElementById(`viz-${nodeId}`);
      if (container) {
        container.innerHTML = `<div class="viz-error">D3 Error: ${e.message}</div>`;
      }
    }
  }

  // Annotation system (existing)
  let editingAnnotation = null;
  let annotationText = "";

  function startAnnotation(node) {
    editingAnnotation = node.id;
    annotationText = node.annotation || "";
  }

  function saveAnnotation(node) {
    chronicle = chronicle.map(n => 
      n.id === node.id ? { ...n, annotation: annotationText } : n
    );
    editingAnnotation = null;
    annotationText = "";
  }

  function cancelAnnotation() {
    editingAnnotation = null;
    annotationText = "";
  }

  // Trash system (existing)
  let showTrash = false;

  function deleteNode(node) {
    trash = [node, ...trash];
    chronicle = chronicle.filter(n => n.id !== node.id);
  }

  function restoreNode(node) {
    chronicle = [...chronicle, node];
    trash = trash.filter(n => n.id !== node.id);
  }

  function permanentlyDelete(node) {
    trash = trash.filter(n => n.id !== node.id);
  }

  function emptyTrash() {
    if (confirm(`Permanently delete ${trash.length} items?`)) {
      trash = [];
    }
  }

  function copyToClipboard(text) {
    navigator.clipboard.writeText(text);
  }

  function downloadNode(node) {
    const content = node.content || "";
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${node.type}_${node.id}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }
</script>

<div class="app"
     on:drop={handleDocumentDrop}
     on:dragover|preventDefault={() => isDragging = true}
     on:dragleave={() => isDragging = false}
     class:dragging={isDragging}>
  
  <!-- Drop Zone Overlay -->
  {#if isDragging || isCreatingShard}
    <div class="drop-overlay">
      {#if isCreatingShard}
        <div class="drop-message">🔨 Creating shard...</div>
      {:else}
        <div class="drop-message">📄 Drop document to create shard</div>
      {/if}
    </div>
  {/if}

  <!-- Toolbar -->
  <div class="toolbar">
    <div class="toolbar-left">
      <div class="status">
        <span class="status-label">Cortex:</span>
        <span class="status-value">{cortexStatus}</span>
      </div>
      
      {#if !shardMounted}
        <button class="shard-btn" on:click={mountShard}>
          📁 Mount Shard
        </button>
      {:else}
        <button class="shard-btn mounted" on:click={unmountShard}>
          ⏏️ {shardInfo?.title || 'Shard'}
        </button>
        <button class="stats-btn" on:click={toggleStats}>
          📊 Stats
        </button>
      {/if}
    </div>

    <div class="toolbar-right">
      <button class="trash-btn" on:click={() => showTrash = !showTrash}>
        🗑️ {trash.length > 0 ? `(${trash.length})` : ''}
      </button>
    </div>
  </div>

  <!-- Main Chronicle -->
  <div class="chronicle" bind:this={chronicleContainer} on:scroll={handleScroll}>
    {#each chronicle as node, index (node.id)}
      <div class="node {node.type}" class:collapsed={isNodeCollapsed(node, index)}>
        <div class="node-header">
          <div class="node-meta">
            <span class="node-type">{node.type}</span>
            {#if node.title}
              <span class="node-title">{node.title}</span>
            {/if}
            {#if node.derivesFrom}
              <span class="derives-from">← {node.derivesFrom}</span>
            {/if}
            {#if node.tier !== undefined}
              <span class="tier">T{node.tier}</span>
            {/if}
          </div>
          <div class="node-actions">
            <span class="timestamp">{formatTimestamp(node.timestamp)}</span>
            {#if isNodeCollapsed(node, index)}
              <button class="icon-btn" on:click={() => expandNode(node)}>▼</button>
            {:else}
              <button class="icon-btn" on:click={() => collapseNode(node)}>▲</button>
            {/if}
            {#if node.type !== "intent"}
              <button class="icon-btn" on:click={() => copyToClipboard(node.content)}>📋</button>
              <button class="icon-btn" on:click={() => downloadNode(node)}>⬇️</button>
              <button class="icon-btn delete" on:click={() => deleteNode(node)}>×</button>
            {/if}
          </div>
        </div>

        {#if !isNodeCollapsed(node, index)}
          <div class="node-content">
            {#if node.type === "intent"}
              <div class="intent-text">{node.content}</div>
            
            {:else if node.type === "system"}
              <div class="system-text">{node.content}</div>
            
            {:else if node.type === "citation"}
              <div class="citation-block" on:click={() => verifyClaim(node.claim)}>
                <div class="citation-claim">{node.content}</div>
                {#if node.evidence}
                  <div class="citation-evidence">"{node.evidence}"</div>
                {/if}
                <div class="citation-meta">
                  <span class="source-hash">{node.source_hash.substring(0, 8)}...</span>
                  <span class="byte-range">{node.byte_start}—{node.byte_end}</span>
                  <span class="verify-hint">🔍 Click to verify</span>
                </div>
              </div>
            
            {:else if node.type === "chat"}
              <div class="chat-text">{node.content}</div>
            
            {:else if node.type === "viz"}
              <div id="viz-{node.id}" class="viz-container"></div>
            
            {:else if node.type === "code"}
              <pre class="code-block"><code>{node.content}</code></pre>
            
            {:else if node.type === "write"}
              <div class="write-block">{node.content}</div>
            
            {:else if node.type === "audio"}
              <div class="placeholder-block">
                <span class="placeholder-icon">🎵</span>
                <p>{node.content}</p>
                <p class="coming-soon">(Audio generation in v0.2)</p>
              </div>
            
            {:else if node.type === "image"}
              <div class="placeholder-block">
                <span class="placeholder-icon">🖼️</span>
                <p>{node.content}</p>
                <p class="coming-soon">(Image generation in v0.2)</p>
              </div>
            
            {:else if node.type === "error"}
              <div class="error-block">{node.content}</div>
            {/if}

            <!-- Annotation -->
            {#if node.annotation && editingAnnotation !== node.id}
              <div class="annotation" on:click={() => startAnnotation(node)}>
                <span class="annotation-icon">📝</span>
                <span>{node.annotation}</span>
              </div>
            {/if}

            {#if editingAnnotation === node.id}
              <div class="annotation-editor">
                <textarea bind:value={annotationText} rows="3" placeholder="Add your note..."></textarea>
                <div class="annotation-actions">
                  <button class="ann-btn save" on:click={() => saveAnnotation(node)}>Save</button>
                  <button class="ann-btn cancel" on:click={cancelAnnotation}>Cancel</button>
                </div>
              </div>
            {/if}

            {#if !node.annotation && editingAnnotation !== node.id && node.type !== "intent" && node.type !== "error"}
              <button class="add-annotation-btn" on:click={() => startAnnotation(node)}>
                + Add note
              </button>
            {/if}
          </div>
        {/if}
      </div>
    {/each}

    {#if isProcessing}
      <div class="processing-indicator">
        <div class="processing-dot"></div>
        <div class="processing-dot"></div>
        <div class="processing-dot"></div>
      </div>
    {/if}
  </div>

  <!-- New nodes indicator -->
  {#if showNewNodesIndicator}
    <button class="new-nodes-indicator" on:click={scrollToBottom}>
      ↓ New nodes below
    </button>
  {/if}

  <!-- Input -->
  <div class="input-container">
    <form on:submit|preventDefault={handleSubmit}>
      <input
        type="text"
        bind:value={input}
        placeholder={shardMounted ? "Query the shard..." : "Talk to the cortex..."}
        disabled={isProcessing}
      />
      <button type="submit" disabled={!input.trim() || isProcessing}>
        {isProcessing ? "●●●" : "→"}
      </button>
    </form>
  </div>

  <!-- Trash Panel -->
  {#if showTrash}
    <div class="trash-panel">
      <div class="trash-header">
        <h3>Trash</h3>
        <button class="close-trash" on:click={() => showTrash = false}>×</button>
      </div>

      {#if trash.length === 0}
        <div class="trash-empty">Trash is empty</div>
      {:else}
        <div class="trash-list">
          {#each trash as node (node.id)}
            <div class="trash-item">
              <div class="trash-item-info">
                <span class="trash-type">{node.type}</span>
                {#if node.title}
                  <span class="trash-title">{node.title}</span>
                {/if}
              </div>
              <div class="trash-item-actions">
                <button class="restore-btn" on:click={() => restoreNode(node)}>Restore</button>
                <button class="perm-delete-btn" on:click={() => permanentlyDelete(node)}>Delete</button>
              </div>
            </div>
          {/each}
        </div>
        <button class="empty-trash-btn" on:click={emptyTrash}>Empty Trash</button>
      {/if}
    </div>
  {/if}

  <!-- Source Verification Panel -->
  {#if showSourceViewer}
    <div class="source-panel">
      <div class="source-header">
        <h3>Source Verification</h3>
        <button class="close-source" on:click={closeSourceViewer}>×</button>
      </div>

      {#if selectedSource}
        <div class="source-body">
          <div class="source-claim">
            <h4>Claim</h4>
            <p>{selectedSource.content}</p>
          </div>

          <div class="source-metadata">
            <div class="meta-row">
              <span class="meta-label">Source:</span>
              <span class="meta-value">{selectedSource.source_hash}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">Bytes:</span>
              <span class="meta-value">{selectedSource.byte_start} — {selectedSource.byte_end}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">Tier:</span>
              <span class="meta-value">{selectedSource.tier}</span>
            </div>
          </div>

          <div class="source-content">
            <h4>Source Content</h4>
            <pre>{sourceContent}</pre>
          </div>

          {#if sourceVerified !== null}
            <div class="verification-result {sourceVerified ? 'verified' : 'failed'}">
              {#if sourceVerified}
                <span class="verify-icon">✓</span>
                <span>Source verified - bytes match</span>
              {:else}
                <span class="verify-icon">✗</span>
                <span>Verification failed - bytes do not match</span>
              {/if}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  {/if}

  <!-- Stats Panel -->
  {#if showStats && shardStats}
    <div class="stats-panel">
      <div class="stats-header">
        <h3>Shard Statistics</h3>
        <button class="close-stats" on:click={() => showStats = false}>×</button>
      </div>

      <div class="stats-body">
        <div class="stat-group">
          <h4>Overview</h4>
          <div class="stat-row">
            <span class="stat-label">Entities:</span>
            <span class="stat-value">{shardStats.entities}</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">Claims:</span>
            <span class="stat-value">{shardStats.claims}</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">Evidence Spans:</span>
            <span class="stat-value">{shardStats.evidence_spans}</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">Unique Predicates:</span>
            <span class="stat-value">{shardStats.unique_predicates}</span>
          </div>
        </div>

        <div class="stat-group">
          <h4>Claims by Tier</h4>
          <div class="stat-row">
            <span class="stat-label">Tier 0 (High):</span>
            <span class="stat-value">{shardStats.claims_by_tier.tier_0}</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">Tier 1 (Medium):</span>
            <span class="stat-value">{shardStats.claims_by_tier.tier_1}</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">Tier 2 (LLM):</span>
            <span class="stat-value">{shardStats.claims_by_tier.tier_2}</span>
          </div>
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  * {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
  }

  :root {
    --bg: #000;
    --surface: #0a0a0a;
    --border: #1a1a1a;
    --text: #ccc;
    --text-dim: #666;
    --accent: #10b981;
    --intent: #3b82f6;
    --error: #ef4444;
    --citation: #a78bfa;
    --system: #64748b;
  }

  .app {
    display: flex;
    flex-direction: column;
    height: 100vh;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    position: relative;
  }

  .app.dragging {
    outline: 3px dashed var(--accent);
    outline-offset: -3px;
  }

  /* Drop Zone Overlay */
  .drop-overlay {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.85);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    pointer-events: none;
  }

  .drop-message {
    font-size: 1.5rem;
    color: var(--accent);
    padding: 40px 60px;
    border: 2px dashed var(--accent);
    border-radius: 12px;
    background: var(--surface);
  }

  /* Toolbar */
  .toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 20px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }

  .toolbar-left, .toolbar-right {
    display: flex;
    gap: 12px;
    align-items: center;
  }

  .status {
    display: flex;
    gap: 8px;
    font-size: 0.8rem;
    font-family: 'JetBrains Mono', monospace;
  }

  .status-label {
    color: var(--text-dim);
  }

  .status-value {
    color: var(--accent);
  }

  .shard-btn, .stats-btn, .trash-btn {
    padding: 6px 12px;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    font-size: 0.85rem;
    cursor: pointer;
    transition: all 0.2s;
  }

  .shard-btn:hover, .stats-btn:hover, .trash-btn:hover {
    background: #1a1a1a;
    border-color: #333;
  }

  .shard-btn.mounted {
    background: rgba(16, 185, 129, 0.1);
    border-color: rgba(16, 185, 129, 0.3);
    color: var(--accent);
  }

  /* Chronicle */
  .chronicle {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .node {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    transition: opacity 0.2s;
  }

  .node.collapsed {
    opacity: 0.5;
  }

  .node-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 14px;
    background: rgba(255, 255, 255, 0.02);
    border-bottom: 1px solid var(--border);
  }

  .node-meta {
    display: flex;
    gap: 10px;
    align-items: center;
  }

  .node-type {
    font-size: 0.65rem;
    font-family: 'JetBrains Mono', monospace;
    text-transform: uppercase;
    color: var(--text-dim);
    letter-spacing: 0.5px;
  }

  .node.intent .node-type { color: var(--intent); }
  .node.chat .node-type { color: var(--accent); }
  .node.citation .node-type { color: var(--citation); }
  .node.system .node-type { color: var(--system); }
  .node.error .node-type { color: var(--error); }

  .node-title {
    font-size: 0.85rem;
    color: var(--text);
  }

  .derives-from {
    font-size: 0.7rem;
    color: var(--text-dim);
    font-style: italic;
  }

  .tier {
    font-size: 0.65rem;
    font-family: 'JetBrains Mono', monospace;
    background: rgba(167, 139, 250, 0.2);
    color: var(--citation);
    padding: 2px 6px;
    border-radius: 4px;
  }

  .node-actions {
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .timestamp {
    font-size: 0.7rem;
    color: var(--text-dim);
    font-family: 'JetBrains Mono', monospace;
  }

  .icon-btn {
    background: none;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    padding: 4px;
    font-size: 0.9rem;
    transition: color 0.2s;
  }

  .icon-btn:hover {
    color: var(--text);
  }

  .icon-btn.delete:hover {
    color: var(--error);
  }

  .node-content {
    padding: 16px;
  }

  /* Intent */
  .intent-text {
    color: var(--intent);
    font-size: 0.95rem;
  }

  /* System */
  .system-text {
    color: var(--system);
    font-size: 0.9rem;
  }

  /* Citation */
  .citation-block {
    cursor: pointer;
    padding: 14px;
    background: rgba(167, 139, 250, 0.05);
    border: 1px solid rgba(167, 139, 250, 0.2);
    border-radius: 6px;
    transition: all 0.2s;
  }

  .citation-block:hover {
    background: rgba(167, 139, 250, 0.1);
    border-color: rgba(167, 139, 250, 0.4);
  }

  .citation-claim {
    font-size: 0.95rem;
    color: var(--citation);
    margin-bottom: 8px;
  }

  .citation-evidence {
    font-size: 0.85rem;
    color: var(--text-dim);
    font-style: italic;
    margin-bottom: 10px;
  }

  .citation-meta {
    display: flex;
    gap: 12px;
    font-size: 0.7rem;
    font-family: 'JetBrains Mono', monospace;
    color: var(--text-dim);
  }

  .verify-hint {
    color: var(--citation);
  }

  /* Chat */
  .chat-text {
    color: var(--text);
    line-height: 1.6;
    font-size: 0.95rem;
  }

  /* Viz container */
  .viz-container {
    background: #0a0a0a;
    border: 1px solid #1a1a1a;
    border-radius: 8px;
    padding: 20px;
    min-height: 200px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .viz-error {
    color: #ef4444;
    font-size: 0.85rem;
    font-family: 'JetBrains Mono', monospace;
  }

  :global(.viz-container svg) {
    max-width: 100%;
    max-height: 400px;
  }

  /* Code block */
  .code-block {
    background: #0a0a0a;
    border: 1px solid #1a1a1a;
    border-radius: 8px;
    padding: 16px;
    overflow-x: auto;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    line-height: 1.6;
    color: #10b981;
  }

  /* Write block */
  .write-block {
    background: #0a0a0a;
    border: 1px solid #1a1a1a;
    border-radius: 8px;
    padding: 20px;
    font-size: 0.95rem;
    line-height: 1.8;
    color: #ccc;
    white-space: pre-wrap;
  }

  /* Placeholder blocks */
  .placeholder-block {
    background: #0a0a0a;
    border: 1px dashed #222;
    border-radius: 8px;
    padding: 30px;
    text-align: center;
    color: #555;
  }

  .placeholder-icon {
    font-size: 2rem;
    display: block;
    margin-bottom: 12px;
    opacity: 0.5;
  }

  .coming-soon {
    font-size: 0.7rem;
    font-family: 'JetBrains Mono', monospace;
    color: #333;
  }

  /* Error block */
  .error-block {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.3);
    border-radius: 8px;
    padding: 16px;
    color: #ef4444;
    font-size: 0.85rem;
    font-family: 'JetBrains Mono', monospace;
  }

  /* Annotation */
  .annotation {
    margin-top: 12px;
    padding: 10px 14px;
    background: rgba(167, 139, 250, 0.05);
    border-left: 2px solid rgba(167, 139, 250, 0.3);
    border-radius: 0 4px 4px 0;
    font-size: 0.85rem;
    color: #a78bfa;
    cursor: pointer;
    transition: background 0.2s;
  }

  .annotation:hover {
    background: rgba(167, 139, 250, 0.1);
  }

  .annotation-editor {
    margin-top: 12px;
    padding: 12px;
    background: #0a0a0a;
    border: 1px solid #222;
    border-radius: 6px;
  }

  .annotation-editor textarea {
    width: 100%;
    background: #111;
    border: 1px solid #333;
    padding: 10px;
    font-size: 0.85rem;
    color: var(--text);
    font-family: inherit;
    margin-bottom: 8px;
    border-radius: 4px;
  }

  .annotation-actions {
    display: flex;
    gap: 8px;
  }

  .ann-btn {
    padding: 6px 12px;
    border: none;
    border-radius: 4px;
    font-size: 0.75rem;
    cursor: pointer;
  }

  .ann-btn.save {
    background: #3b82f6;
    color: white;
  }

  .ann-btn.cancel {
    background: #222;
    color: #888;
  }

  .add-annotation-btn {
    margin-top: 8px;
    padding: 4px 10px;
    background: transparent;
    border: 1px dashed #333;
    color: #666;
    border-radius: 4px;
    font-size: 0.75rem;
    cursor: pointer;
  }

  .add-annotation-btn:hover {
    border-color: #555;
    color: #888;
  }

  /* Processing indicator */
  .processing-indicator {
    display: flex;
    gap: 6px;
    padding: 12px 0;
  }

  .processing-dot {
    width: 8px;
    height: 8px;
    background: #333;
    border-radius: 50%;
    animation: pulse 1.4s infinite ease-in-out;
  }

  .processing-dot:nth-child(2) {
    animation-delay: 0.2s;
  }

  .processing-dot:nth-child(3) {
    animation-delay: 0.4s;
  }

  @keyframes pulse {
    0%, 80%, 100% {
      opacity: 0.3;
      transform: scale(0.8);
    }
    40% {
      opacity: 1;
      transform: scale(1);
    }
  }

  /* New nodes indicator */
  .new-nodes-indicator {
    position: absolute;
    bottom: 80px;
    left: 50%;
    transform: translateX(-50%);
    padding: 8px 16px;
    background: var(--accent);
    color: #000;
    border: none;
    border-radius: 20px;
    font-size: 0.85rem;
    font-weight: 500;
    cursor: pointer;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
    z-index: 100;
  }

  /* Input */
  .input-container {
    padding: 20px;
    border-top: 1px solid var(--border);
    background: var(--surface);
  }

  .input-container form {
    display: flex;
    gap: 12px;
  }

  .input-container input {
    flex: 1;
    padding: 12px 16px;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 8px;
    font-size: 0.95rem;
    font-family: inherit;
  }

  .input-container input:focus {
    outline: none;
    border-color: var(--accent);
  }

  .input-container button {
    padding: 12px 24px;
    background: var(--accent);
    color: #000;
    border: none;
    border-radius: 8px;
    font-size: 0.95rem;
    font-weight: 500;
    cursor: pointer;
    transition: opacity 0.2s;
  }

  .input-container button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .input-container button:not(:disabled):hover {
    opacity: 0.9;
  }

  /* Trash Panel */
  .trash-panel {
    position: absolute;
    top: 0;
    right: 0;
    width: 320px;
    height: 100%;
    background: #0a0a0a;
    border-left: 1px solid #1a1a1a;
    display: flex;
    flex-direction: column;
    z-index: 200;
  }

  .trash-header {
    padding: 20px;
    border-bottom: 1px solid #1a1a1a;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .trash-header h3 {
    font-size: 1rem;
    font-weight: 500;
  }

  .close-trash {
    background: none;
    border: none;
    color: #555;
    font-size: 1.25rem;
    cursor: pointer;
  }

  .close-trash:hover {
    color: #fff;
  }

  .trash-empty {
    padding: 40px;
    text-align: center;
    color: #444;
    font-size: 0.9rem;
  }

  .trash-list {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
  }

  .trash-item {
    padding: 12px;
    background: #111;
    border-radius: 6px;
    margin-bottom: 8px;
  }

  .trash-item-info {
    display: flex;
    gap: 8px;
    margin-bottom: 8px;
  }

  .trash-type {
    font-size: 0.65rem;
    font-family: 'JetBrains Mono', monospace;
    color: #555;
    text-transform: uppercase;
  }

  .trash-title {
    font-size: 0.8rem;
    color: #888;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .trash-item-actions {
    display: flex;
    gap: 8px;
  }

  .restore-btn, .perm-delete-btn {
    padding: 4px 10px;
    border: none;
    border-radius: 4px;
    font-size: 0.7rem;
    cursor: pointer;
  }

  .restore-btn {
    background: #222;
    color: #10b981;
  }

  .restore-btn:hover {
    background: #1a3a2a;
  }

  .perm-delete-btn {
    background: #222;
    color: #ef4444;
  }

  .perm-delete-btn:hover {
    background: #3a1a1a;
  }

  .empty-trash-btn {
    margin: 12px;
    padding: 10px;
    background: #1a1a1a;
    border: 1px solid #333;
    color: #ef4444;
    border-radius: 6px;
    font-size: 0.8rem;
    cursor: pointer;
  }

  .empty-trash-btn:hover {
    background: #2a1a1a;
  }

  /* Source Verification Panel */
  .source-panel {
    position: absolute;
    top: 0;
    right: 0;
    width: 480px;
    height: 100%;
    background: #0a0a0a;
    border-left: 1px solid #1a1a1a;
    display: flex;
    flex-direction: column;
    z-index: 250;
  }

  .source-header {
    padding: 20px;
    border-bottom: 1px solid #1a1a1a;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .source-header h3 {
    font-size: 1rem;
    font-weight: 500;
  }

  .close-source {
    background: none;
    border: none;
    color: #555;
    font-size: 1.25rem;
    cursor: pointer;
  }

  .close-source:hover {
    color: #fff;
  }

  .source-body {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  .source-claim h4, .source-metadata h4, .source-content h4 {
    font-size: 0.85rem;
    color: var(--text-dim);
    margin-bottom: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .source-claim p {
    color: var(--citation);
    font-size: 0.95rem;
  }

  .source-metadata {
    background: #111;
    border: 1px solid #1a1a1a;
    border-radius: 6px;
    padding: 12px;
  }

  .meta-row {
    display: flex;
    justify-content: space-between;
    padding: 6px 0;
    font-size: 0.8rem;
    font-family: 'JetBrains Mono', monospace;
  }

  .meta-label {
    color: var(--text-dim);
  }

  .meta-value {
    color: var(--text);
  }

  .source-content pre {
    background: #111;
    border: 1px solid #1a1a1a;
    border-radius: 6px;
    padding: 14px;
    font-size: 0.85rem;
    line-height: 1.6;
    color: var(--text);
    font-family: 'JetBrains Mono', monospace;
    white-space: pre-wrap;
    word-wrap: break-word;
  }

  .verification-result {
    padding: 14px;
    border-radius: 6px;
    font-size: 0.9rem;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .verification-result.verified {
    background: rgba(16, 185, 129, 0.1);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: var(--accent);
  }

  .verification-result.failed {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: var(--error);
  }

  .verify-icon {
    font-size: 1.2rem;
    font-weight: bold;
  }

  /* Stats Panel */
  .stats-panel {
    position: absolute;
    top: 0;
    right: 0;
    width: 320px;
    height: 100%;
    background: #0a0a0a;
    border-left: 1px solid #1a1a1a;
    display: flex;
    flex-direction: column;
    z-index: 200;
  }

  .stats-header {
    padding: 20px;
    border-bottom: 1px solid #1a1a1a;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .stats-header h3 {
    font-size: 1rem;
    font-weight: 500;
  }

  .close-stats {
    background: none;
    border: none;
    color: #555;
    font-size: 1.25rem;
    cursor: pointer;
  }

  .close-stats:hover {
    color: #fff;
  }

  .stats-body {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 24px;
  }

  .stat-group h4 {
    font-size: 0.85rem;
    color: var(--text-dim);
    margin-bottom: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .stat-row {
    display: flex;
    justify-content: space-between;
    padding: 8px 12px;
    background: #111;
    border-radius: 4px;
    margin-bottom: 6px;
  }

  .stat-label {
    font-size: 0.85rem;
    color: var(--text-dim);
  }

  .stat-value {
    font-size: 0.85rem;
    color: var(--accent);
    font-family: 'JetBrains Mono', monospace;
  }

  /* Scrollbar */
  .chronicle::-webkit-scrollbar,
  .trash-list::-webkit-scrollbar,
  .source-body::-webkit-scrollbar,
  .stats-body::-webkit-scrollbar {
    width: 8px;
  }

  .chronicle::-webkit-scrollbar-track,
  .trash-list::-webkit-scrollbar-track,
  .source-body::-webkit-scrollbar-track,
  .stats-body::-webkit-scrollbar-track {
    background: #000;
  }

  .chronicle::-webkit-scrollbar-thumb,
  .trash-list::-webkit-scrollbar-thumb,
  .source-body::-webkit-scrollbar-thumb,
  .stats-body::-webkit-scrollbar-thumb {
    background: #222;
    border-radius: 4px;
  }

  .chronicle::-webkit-scrollbar-thumb:hover,
  .trash-list::-webkit-scrollbar-thumb:hover,
  .source-body::-webkit-scrollbar-thumb:hover,
  .stats-body::-webkit-scrollbar-thumb:hover {
    background: #333;
  }
</style>
