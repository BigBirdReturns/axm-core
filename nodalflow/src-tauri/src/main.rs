//! Nodal Flow: Sovereign Graph Engine
//!
//! Main entry point for the Tauri application.
//! Routes commands between the frontend, Vault, and Cortex (Ollama).
//!
//! Architecture:
//! ```
//! Frontend (Svelte)
//!     │
//!     ▼
//! Tauri Commands ◄──── This file
//!     │
//!     ├─────► Vault (DuckDB/Parquet)
//!     │         │
//!     │         ▼
//!     │       AXM Shard (graph/, evidence/, content/)
//!     │
//!     └─────► Cortex (Ollama)
//!               │
//!               ▼
//!             Local LLM (llama3, etc.)
//! ```

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod vault;

use std::sync::Arc;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use tauri::State;
use vault::{Vault, VaultError, VerifiedClaim, ShardMetadata, QueryOptions, TrustLevel};

// ============================================================================
// DATA STRUCTURES
// ============================================================================

#[derive(Serialize, Deserialize, Debug)]
struct OllamaRequest {
    model: String,
    prompt: String,
    stream: bool,
}

#[derive(Serialize, Deserialize, Debug)]
struct OllamaResponse {
    response: String,
    done: bool,
}

/// Response from query_ollama including both LLM output and source claims
#[derive(Serialize, Debug)]
pub struct EnrichedResponse {
    /// The LLM's formatted response
    pub content: String,
    /// The verified claims that were injected as context
    pub sources: Vec<VerifiedClaim>,
    /// Whether verified facts were found and used
    pub has_verified_context: bool,
    /// Trust level of the current shard
    pub trust_level: Option<TrustLevel>,
}

/// Application state shared across commands
struct AppState {
    vault: Arc<Vault>,
}

// ============================================================================
// CORTEX COMMANDS (LLM Interface)
// ============================================================================

/// Check if Ollama is running and responsive
#[tauri::command]
async fn check_cortex_status() -> Result<String, String> {
    let client = Client::new();
    match client.get("http://127.0.0.1:11434/api/tags").send().await {
        Ok(r) if r.status().is_success() => Ok("Cortex Online".to_string()),
        Ok(r) => Err(format!("Cortex returned status: {}", r.status())),
        Err(e) => Err(format!("Cortex Offline: {}. Run: ollama serve", e)),
    }
}

/// List available models on the local Ollama instance
#[tauri::command]
async fn list_models() -> Result<Vec<String>, String> {
    #[derive(Deserialize)]
    struct TagsResponse {
        models: Vec<ModelInfo>,
    }
    
    #[derive(Deserialize)]
    struct ModelInfo {
        name: String,
    }

    let client = Client::new();
    let res = client
        .get("http://127.0.0.1:11434/api/tags")
        .send()
        .await
        .map_err(|e| e.to_string())?;

    let tags: TagsResponse = res.json().await.map_err(|e| e.to_string())?;
    Ok(tags.models.into_iter().map(|m| m.name).collect())
}

/// Main query endpoint: Vault → Context Injection → Ollama
/// 
/// Flow:
/// 1. Extract search terms from prompt
/// 2. Query Vault for matching verified claims
/// 3. Inject claims into LLM context with citation instructions
/// 4. Call Ollama
/// 5. Return enriched response with source metadata
#[tauri::command]
async fn query_ollama(
    prompt: String,
    model: Option<String>,
    max_tier: Option<i8>,
    state: State<'_, AppState>,
) -> Result<EnrichedResponse, String> {
    // 1. Extract search terms
    let search_terms = extract_search_terms(&prompt);
    
    // 2. Query Vault for each term
    let mut all_claims: Vec<VerifiedClaim> = Vec::new();
    let options = QueryOptions {
        max_tier,
        limit: Some(10),
        include_orphan_claims: false,
    };

    for term in &search_terms {
        if let Ok(claims) = state.vault.query(term, Some(options.clone())) {
            for claim in claims {
                // Deduplicate by claim_id
                if !all_claims.iter().any(|c| c.claim_id == claim.claim_id) {
                    all_claims.push(claim);
                }
            }
        }
    }

    // Get trust level
    let trust_level = state.vault.get_metadata()
        .ok()
        .flatten()
        .map(|m| m.trust_level);

    // 3. Build context-injected prompt
    let (system_prompt, context_block) = build_context(&all_claims, trust_level);
    let has_verified_context = !all_claims.is_empty();

    let final_prompt = format!(
        "{}\n\n{}\n\nUser: {}\n\nAssistant:",
        system_prompt,
        context_block,
        prompt
    );

    // 4. Call Ollama
    let client = Client::new();
    let request = OllamaRequest {
        model: model.unwrap_or_else(|| "llama3".to_string()),
        prompt: final_prompt,
        stream: false,
    };

    let res = client
        .post("http://127.0.0.1:11434/api/generate")
        .json(&request)
        .send()
        .await
        .map_err(|e| format!("Failed to reach Ollama: {}. Is it running?", e))?;

    if !res.status().is_success() {
        return Err(format!("Ollama error: {}", res.status()));
    }

    let body: OllamaResponse = res
        .json()
        .await
        .map_err(|e| format!("Failed to parse response: {}", e))?;

    // 5. Return enriched response
    Ok(EnrichedResponse {
        content: body.response,
        sources: all_claims,
        has_verified_context,
        trust_level,
    })
}

// ============================================================================
// VAULT COMMANDS (Graph Interface)
// ============================================================================

/// Mount an AXM Genesis shard
#[tauri::command]
async fn mount_vault(path: String, state: State<'_, AppState>) -> Result<ShardMetadata, String> {
    state.vault.mount_shard(&path).map_err(|e| e.to_string())
}

/// Unmount the current shard
#[tauri::command]
async fn unmount_vault(state: State<'_, AppState>) -> Result<(), String> {
    state.vault.unmount().map_err(|e| e.to_string())
}

/// Get current shard metadata
#[tauri::command]
async fn get_shard_info(state: State<'_, AppState>) -> Result<Option<ShardMetadata>, String> {
    state.vault.get_metadata().map_err(|e| e.to_string())
}

/// Query the vault directly (without LLM)
#[tauri::command]
async fn query_vault(
    search_term: String,
    max_tier: Option<i8>,
    limit: Option<i32>,
    state: State<'_, AppState>,
) -> Result<Vec<VerifiedClaim>, String> {
    let options = QueryOptions {
        max_tier,
        limit,
        include_orphan_claims: false,
    };
    state.vault.query(&search_term, Some(options)).map_err(|e| e.to_string())
}

/// Get all claims from the shard
#[tauri::command]
async fn get_all_claims(
    max_tier: Option<i8>,
    limit: Option<i32>,
    state: State<'_, AppState>,
) -> Result<Vec<VerifiedClaim>, String> {
    let options = QueryOptions {
        max_tier,
        limit,
        include_orphan_claims: false,
    };
    state.vault.get_all_claims(Some(options)).map_err(|e| e.to_string())
}

/// Get claims for a specific entity (graph traversal)
#[tauri::command]
async fn get_claims_for_entity(
    entity_id: String,
    state: State<'_, AppState>,
) -> Result<Vec<VerifiedClaim>, String> {
    state.vault.get_claims_for_entity(&entity_id).map_err(|e| e.to_string())
}

/// Get content slice for verification (Green Padlock)
#[tauri::command]
async fn get_content_slice(
    source_hash: String,
    byte_start: i64,
    byte_end: i64,
    state: State<'_, AppState>,
) -> Result<String, String> {
    state.vault.get_content_slice(&source_hash, byte_start, byte_end)
        .map_err(|e| e.to_string())
}

/// Verify that a claim's evidence matches the source
#[tauri::command]
async fn verify_claim(
    claim: VerifiedClaim,
    state: State<'_, AppState>,
) -> Result<bool, String> {
    state.vault.verify_span(&claim).map_err(|e| e.to_string())
}

/// Execute arbitrary SQL on the shard
#[tauri::command]
async fn execute_sql(
    sql: String,
    state: State<'_, AppState>,
) -> Result<Vec<serde_json::Value>, String> {
    state.vault.execute_sql(&sql).map_err(|e| e.to_string())
}

/// Get shard statistics
#[tauri::command]
async fn get_statistics(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    state.vault.get_statistics().map_err(|e| e.to_string())
}

/// Verify shard integrity (Merkle root)
#[tauri::command]
async fn verify_shard(state: State<'_, AppState>) -> Result<String, String> {
    let shard_path = state.vault.get_shard_path()
        .ok_or_else(|| "No shard mounted".to_string())?;
    
    match vault::verify::verify_shard(&shard_path) {
        Ok(TrustLevel::Verified) => Ok("Verified: Signature and Merkle root valid".to_string()),
        Ok(TrustLevel::SignatureOnly) => Ok("Partially verified: Merkle root valid".to_string()),
        Ok(_) => Ok("Unverified".to_string()),
        Err(e) => Err(format!("Verification failed: {}", e)),
    }
}

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

/// Extract meaningful search terms from a natural language prompt
fn extract_search_terms(prompt: &str) -> Vec<String> {
    let lowered = prompt.to_lowercase();
    
    // Remove common question patterns
    let patterns = [
        "what is", "what are", "what does", "what do",
        "how do i", "how to", "how does", "how can i",
        "tell me about", "explain", "describe", "define",
        "show me", "find", "search for", "look up",
        "who is", "who are", "when did", "when does",
        "where is", "where are", "why does", "why is",
        "can you", "could you", "would you", "please",
        "i need to know", "i want to know", "i'd like to know",
        "help me", "help with",
    ];
    
    let mut cleaned = lowered.clone();
    for pattern in &patterns {
        cleaned = cleaned.replace(pattern, " ");
    }
    
    // Remove punctuation
    cleaned = cleaned
        .chars()
        .map(|c| if c.is_alphanumeric() || c.is_whitespace() { c } else { ' ' })
        .collect();
    
    // Split into terms, filter short words and stopwords
    let stopwords = ["the", "a", "an", "is", "are", "was", "were", "be", "been",
                     "being", "have", "has", "had", "do", "does", "did", "will",
                     "would", "could", "should", "may", "might", "must", "shall",
                     "can", "need", "dare", "ought", "used", "to", "of", "in",
                     "for", "on", "with", "at", "by", "from", "as", "into",
                     "through", "during", "before", "after", "above", "below",
                     "between", "under", "again", "further", "then", "once",
                     "here", "there", "when", "where", "why", "how", "all",
                     "each", "few", "more", "most", "other", "some", "such",
                     "no", "nor", "not", "only", "own", "same", "so", "than",
                     "too", "very", "just", "and", "but", "if", "or", "because",
                     "until", "while", "about", "against", "this", "that", "these",
                     "those", "am", "it", "its", "they", "them", "their", "we",
                     "us", "our", "you", "your", "he", "him", "his", "she", "her"];
    
    let terms: Vec<String> = cleaned
        .split_whitespace()
        .filter(|w| w.len() > 2 && !stopwords.contains(w))
        .map(|s| s.to_string())
        .collect();
    
    // If we extracted nothing useful, try the whole cleaned prompt
    if terms.is_empty() {
        vec![cleaned.trim().to_string()]
    } else {
        terms
    }
}

/// Build the system prompt and context block for the LLM
fn build_context(claims: &[VerifiedClaim], trust_level: Option<TrustLevel>) -> (String, String) {
    if claims.is_empty() {
        let system = "You are Nodal Flow, a sovereign local-first AI assistant. \
                      You run entirely on the user's hardware. \
                      Answer concisely and helpfully.";
        return (system.to_string(), String::new());
    }

    let trust_note = match trust_level {
        Some(TrustLevel::Verified) => "These facts are cryptographically verified.",
        Some(TrustLevel::SignatureOnly) => "These facts have verified integrity.",
        _ => "These facts are from an unverified shard.",
    };

    let system = format!(r#"You are Nodal Flow, a Sovereign Intelligence with access to verified Knowledge Shards.

CRITICAL RULES:
1. Use ONLY the provided VERIFIED FACTS to answer questions about their topics.
2. When citing a fact, you MUST wrap it in a citation node:
   <NODE type="citation" source="[source_hash]" bytes="[byte_start]-[byte_end]" title="[subject] [predicate] [object]">[evidence text]</NODE>
3. If the facts don't contain relevant information, say: "I don't have verified information about that in the current knowledge shard."
4. DO NOT hallucinate or invent information. Stick to what's in the facts.
5. Be concise and direct.

{}

The user trusts you because you cite verifiable sources. Maintain that trust."#, trust_note);

    // Build facts block
    let mut context = String::from("\n=== VERIFIED KNOWLEDGE SHARD ===\n");
    
    for (i, claim) in claims.iter().enumerate() {
        context.push_str(&format!(
            "\nFACT #{}: {} {} {}\n",
            i + 1,
            claim.subject,
            claim.predicate,
            claim.object
        ));
        
        if !claim.evidence.is_empty() {
            context.push_str(&format!(
                "  EVIDENCE: \"{}\"\n",
                claim.evidence
            ));
        }
        
        if !claim.source_hash.is_empty() && claim.byte_start >= 0 {
            context.push_str(&format!(
                "  SOURCE: {} bytes {}-{}\n",
                &claim.source_hash[..12.min(claim.source_hash.len())],
                claim.byte_start,
                claim.byte_end
            ));
        }

        if claim.tier > 0 {
            context.push_str(&format!(
                "  CONFIDENCE: Tier {} ({})\n",
                claim.tier,
                match claim.tier {
                    0 => "high",
                    1 => "medium",
                    _ => "review recommended"
                }
            ));
        }
    }
    
    context.push_str("\n=== END VERIFIED FACTS ===\n");
    
    (system, context)
}

// ============================================================================
// MAIN
// ============================================================================

fn main() {
    // Initialize logging
    env_logger::Builder::from_env(
        env_logger::Env::default().default_filter_or("info")
    ).init();

    let vault = Arc::new(Vault::new());
    
    tauri::Builder::default()
        .manage(AppState { vault })
        .invoke_handler(tauri::generate_handler![
            // Cortex commands
            check_cortex_status,
            list_models,
            query_ollama,
            
            // Vault commands
            mount_vault,
            unmount_vault,
            get_shard_info,
            query_vault,
            get_all_claims,
            get_claims_for_entity,
            get_content_slice,
            verify_claim,
            execute_sql,
            get_statistics,
            verify_shard,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
