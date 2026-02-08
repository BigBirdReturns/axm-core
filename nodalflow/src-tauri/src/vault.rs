//! The Vault: Sovereign Graph Engine
//!
//! This module implements verified knowledge retrieval from AXM Genesis shards.
//! It embeds DuckDB for zero-copy Parquet querying and provides the foundation
//! for cryptographic verification.
//!
//! Architecture:
//! ```
//! ┌─────────────────────────────────────────────────────────────────┐
//! │                         VAULT                                    │
//! ├─────────────────────────────────────────────────────────────────┤
//! │  DuckDB (in-memory)                                              │
//! │  ├── claims VIEW      → graph/claims.parquet                    │
//! │  ├── entities VIEW    → graph/entities.parquet                  │
//! │  ├── provenance VIEW  → graph/provenance.parquet                │
//! │  └── spans VIEW       → evidence/spans.parquet                  │
//! ├─────────────────────────────────────────────────────────────────┤
//! │  Manifest Cache                                                  │
//! │  ├── shard_id, merkle_root, sources[]                           │
//! │  └── source_hash → content_path mapping                         │
//! ├─────────────────────────────────────────────────────────────────┤
//! │  Verification State                                              │
//! │  ├── signature_valid: Option<bool>                              │
//! │  ├── merkle_valid: Option<bool>                                 │
//! │  └── trust_level: TrustLevel                                    │
//! └─────────────────────────────────────────────────────────────────┘
//! ```

use duckdb::{Connection, params};
use serde::{Serialize, Deserialize};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use thiserror::Error;

// ============================================================================
// ERROR TYPES
// ============================================================================

#[derive(Error, Debug)]
pub enum VaultError {
    #[error("Shard not found: {0}")]
    ShardNotFound(String),
    
    #[error("Missing required file: {0}")]
    MissingFile(String),
    
    #[error("Invalid manifest: {0}")]
    InvalidManifest(String),
    
    #[error("Database error: {0}")]
    DatabaseError(String),
    
    #[error("No shard mounted")]
    NotMounted,
    
    #[error("Content file not found for hash: {0}")]
    ContentNotFound(String),
    
    #[error("Byte range out of bounds: {0}..{1}")]
    ByteRangeError(i64, i64),
    
    #[error("UTF-8 decode error: {0}")]
    Utf8Error(String),
    
    #[error("Verification failed: {0}")]
    VerificationError(String),
}

impl From<VaultError> for String {
    fn from(e: VaultError) -> String {
        e.to_string()
    }
}

// ============================================================================
// DATA STRUCTURES
// ============================================================================

/// Trust level for a mounted shard
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
pub enum TrustLevel {
    /// Signature and Merkle root verified
    Verified,
    /// Signature valid but Merkle not checked (fast mount)
    SignatureOnly,
    /// No verification performed
    Unverified,
    /// Verification attempted but failed
    Failed,
}

/// A verified claim with full provenance chain
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerifiedClaim {
    /// Unique claim identifier (c_...)
    pub claim_id: String,
    /// Subject entity label (human-readable)
    pub subject: String,
    /// Subject entity ID (e_...)
    pub subject_id: String,
    /// Predicate (relationship type)
    pub predicate: String,
    /// Object label (entity label or literal value)
    pub object: String,
    /// Object entity ID (e_... or empty for literals)
    pub object_id: String,
    /// Object type: "entity" or "literal:string"
    pub object_type: String,
    /// Extraction tier (0 = high confidence, 2 = LLM-extracted)
    pub tier: i8,
    /// Evidence text from source document
    pub evidence: String,
    /// SHA-256 hash of source content file
    pub source_hash: String,
    /// Byte offset start in source file
    pub byte_start: i64,
    /// Byte offset end in source file
    pub byte_end: i64,
}

/// Source file metadata from manifest
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceFile {
    /// Relative path under content/
    pub path: String,
    /// SHA-256 hash of file contents
    pub hash: String,
}

/// Shard metadata extracted from manifest.json
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShardMetadata {
    /// Spec version (should be "1.0.0")
    pub spec_version: String,
    /// Unique shard identifier
    pub shard_id: String,
    /// Human-readable title
    pub title: String,
    /// Namespace (e.g., "survival/medical")
    pub namespace: String,
    /// Creation timestamp (RFC 3339)
    pub created_at: String,
    /// Publisher ID
    pub publisher_id: String,
    /// Publisher name
    pub publisher_name: String,
    /// SPDX license identifier
    pub license: String,
    /// Blake3 Merkle root (hex)
    pub merkle_root: String,
    /// Number of entities in shard
    pub entity_count: i64,
    /// Number of claims in shard
    pub claim_count: i64,
    /// Source files with hashes
    pub sources: Vec<SourceFile>,
    /// Current trust level
    pub trust_level: TrustLevel,
}

/// Query options for filtering claims
#[derive(Debug, Clone, Default)]
pub struct QueryOptions {
    /// Maximum tier to include (None = all tiers)
    pub max_tier: Option<i8>,
    /// Maximum results to return
    pub limit: Option<i32>,
    /// Include claims without evidence
    pub include_orphan_claims: bool,
}

// ============================================================================
// THE VAULT
// ============================================================================

pub struct Vault {
    /// DuckDB connection (in-memory)
    conn: Mutex<Connection>,
    /// Whether a shard is currently mounted
    is_mounted: Mutex<bool>,
    /// Current shard metadata
    metadata: Mutex<Option<ShardMetadata>>,
    /// Path to currently mounted shard
    shard_path: Mutex<Option<PathBuf>>,
    /// Mapping of source_hash -> content file path
    content_map: Mutex<HashMap<String, PathBuf>>,
}

impl Vault {
    /// Create a new Vault with an in-memory DuckDB instance
    pub fn new() -> Self {
        let conn = Connection::open_in_memory()
            .expect("Failed to initialize DuckDB");
        
        Vault {
            conn: Mutex::new(conn),
            is_mounted: Mutex::new(false),
            metadata: Mutex::new(None),
            shard_path: Mutex::new(None),
            content_map: Mutex::new(HashMap::new()),
        }
    }

    /// Mount an AXM Genesis shard
    /// 
    /// This performs:
    /// 1. Layout validation (required files exist)
    /// 2. Manifest parsing
    /// 3. Parquet view creation (zero-copy)
    /// 4. Content map building (hash -> path)
    /// 
    /// Verification (signature, Merkle) is optional and can be done separately.
    pub fn mount_shard(&self, shard_path: &str) -> Result<ShardMetadata, VaultError> {
        let path = Path::new(shard_path);
        
        if !path.exists() {
            return Err(VaultError::ShardNotFound(shard_path.to_string()));
        }

        // Validate required layout per AXM Genesis Spec v1.0.0
        let required_files = [
            "manifest.json",
            "graph/claims.parquet",
            "graph/entities.parquet",
            "graph/provenance.parquet",
            "evidence/spans.parquet",
        ];
        
        for file in &required_files {
            if !path.join(file).exists() {
                return Err(VaultError::MissingFile(file.to_string()));
            }
        }

        // Parse manifest
        let manifest_path = path.join("manifest.json");
        let manifest_str = fs::read_to_string(&manifest_path)
            .map_err(|e| VaultError::InvalidManifest(e.to_string()))?;
        let manifest: serde_json::Value = serde_json::from_str(&manifest_str)
            .map_err(|e| VaultError::InvalidManifest(e.to_string()))?;

        // Extract metadata
        let metadata = self.parse_manifest(&manifest)?;
        
        // Build content map (hash -> full path)
        let mut content_map = HashMap::new();
        for source in &metadata.sources {
            let content_path = path.join(&source.path);
            if content_path.exists() {
                content_map.insert(source.hash.clone(), content_path);
            }
        }

        // Mount Parquet files as DuckDB views
        let conn = self.conn.lock().map_err(|e| VaultError::DatabaseError(e.to_string()))?;
        
        let claims_path = path.join("graph/claims.parquet");
        let entities_path = path.join("graph/entities.parquet");
        let provenance_path = path.join("graph/provenance.parquet");
        let spans_path = path.join("evidence/spans.parquet");

        let mount_sql = format!(
            r#"
            CREATE OR REPLACE VIEW claims AS SELECT * FROM read_parquet('{}');
            CREATE OR REPLACE VIEW entities AS SELECT * FROM read_parquet('{}');
            CREATE OR REPLACE VIEW provenance AS SELECT * FROM read_parquet('{}');
            CREATE OR REPLACE VIEW spans AS SELECT * FROM read_parquet('{}');
            "#,
            claims_path.to_string_lossy(),
            entities_path.to_string_lossy(),
            provenance_path.to_string_lossy(),
            spans_path.to_string_lossy()
        );

        conn.execute_batch(&mount_sql)
            .map_err(|e| VaultError::DatabaseError(e.to_string()))?;

        // Update state
        *self.is_mounted.lock().unwrap() = true;
        *self.metadata.lock().unwrap() = Some(metadata.clone());
        *self.shard_path.lock().unwrap() = Some(path.to_path_buf());
        *self.content_map.lock().unwrap() = content_map;

        Ok(metadata)
    }

    /// Parse manifest.json into ShardMetadata
    fn parse_manifest(&self, manifest: &serde_json::Value) -> Result<ShardMetadata, VaultError> {
        let get_str = |v: &serde_json::Value, path: &[&str]| -> String {
            let mut current = v;
            for key in path {
                current = current.get(*key).unwrap_or(&serde_json::Value::Null);
            }
            current.as_str().unwrap_or("").to_string()
        };

        let get_i64 = |v: &serde_json::Value, path: &[&str]| -> i64 {
            let mut current = v;
            for key in path {
                current = current.get(*key).unwrap_or(&serde_json::Value::Null);
            }
            current.as_i64().unwrap_or(0)
        };

        // Parse sources array
        let sources: Vec<SourceFile> = manifest
            .get("sources")
            .and_then(|s| s.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|s| {
                        Some(SourceFile {
                            path: s.get("path")?.as_str()?.to_string(),
                            hash: s.get("hash")?.as_str()?.to_string(),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();

        Ok(ShardMetadata {
            spec_version: get_str(manifest, &["spec_version"]),
            shard_id: get_str(manifest, &["shard_id"]),
            title: get_str(manifest, &["metadata", "title"]),
            namespace: get_str(manifest, &["metadata", "namespace"]),
            created_at: get_str(manifest, &["metadata", "created_at"]),
            publisher_id: get_str(manifest, &["publisher", "id"]),
            publisher_name: get_str(manifest, &["publisher", "name"]),
            license: get_str(manifest, &["license", "spdx"]),
            merkle_root: get_str(manifest, &["integrity", "merkle_root"]),
            entity_count: get_i64(manifest, &["statistics", "entities"]),
            claim_count: get_i64(manifest, &["statistics", "claims"]),
            sources,
            trust_level: TrustLevel::Unverified,
        })
    }

    /// Query the graph for claims matching search terms
    /// 
    /// Searches across:
    /// - Subject labels
    /// - Object labels (entities) 
    /// - Object values (literals)
    /// - Predicates
    /// 
    /// Handles both entity and literal object types correctly.
    pub fn query(&self, search_term: &str, options: Option<QueryOptions>) -> Result<Vec<VerifiedClaim>, VaultError> {
        let conn = self.conn.lock().map_err(|e| VaultError::DatabaseError(e.to_string()))?;
        let mounted = *self.is_mounted.lock().unwrap();
        
        if !mounted {
            return Err(VaultError::NotMounted);
        }

        let opts = options.unwrap_or_default();
        let limit = opts.limit.unwrap_or(20);
        let tier_filter = opts.max_tier.map(|t| format!("AND c.tier <= {}", t)).unwrap_or_default();

        // The spec-compliant query:
        // - JOINs subject entity (always exists)
        // - LEFT JOINs object entity (only for object_type = 'entity')
        // - Uses CASE to return object label or literal value
        // - Includes provenance and spans for evidence
        let sql = format!(r#"
            SELECT 
                c.claim_id,
                subj.label as subject,
                subj.entity_id as subject_id,
                c.predicate,
                CASE 
                    WHEN c.object_type = 'entity' THEN COALESCE(obj.label, c.object)
                    ELSE c.object
                END as object,
                CASE 
                    WHEN c.object_type = 'entity' THEN COALESCE(obj.entity_id, '')
                    ELSE ''
                END as object_id,
                c.object_type,
                c.tier,
                COALESCE(s.text, '') as evidence,
                COALESCE(p.source_hash, '') as source_hash,
                COALESCE(p.byte_start, -1) as byte_start,
                COALESCE(p.byte_end, -1) as byte_end
            FROM claims c
            JOIN entities subj ON c.subject = subj.entity_id
            LEFT JOIN entities obj ON c.object = obj.entity_id AND c.object_type = 'entity'
            LEFT JOIN provenance p ON c.claim_id = p.claim_id
            LEFT JOIN spans s ON p.source_hash = s.source_hash 
                AND p.byte_start = s.byte_start 
                AND p.byte_end = s.byte_end
            WHERE (
                subj.label ILIKE ?
                OR (c.object_type = 'entity' AND obj.label ILIKE ?)
                OR (c.object_type = 'literal:string' AND c.object ILIKE ?)
                OR c.predicate ILIKE ?
            )
            {}
            ORDER BY c.tier ASC, c.claim_id
            LIMIT ?
        "#, tier_filter);

        let search_pattern = format!("%{}%", search_term);
        let mut stmt = conn.prepare(&sql)
            .map_err(|e| VaultError::DatabaseError(e.to_string()))?;

        let rows = stmt.query_map(
            params![&search_pattern, &search_pattern, &search_pattern, &search_pattern, limit],
            |row| {
                Ok(VerifiedClaim {
                    claim_id: row.get(0)?,
                    subject: row.get(1)?,
                    subject_id: row.get(2)?,
                    predicate: row.get(3)?,
                    object: row.get(4)?,
                    object_id: row.get(5)?,
                    object_type: row.get(6)?,
                    tier: row.get(7)?,
                    evidence: row.get(8)?,
                    source_hash: row.get(9)?,
                    byte_start: row.get(10)?,
                    byte_end: row.get(11)?,
                })
            }
        ).map_err(|e| VaultError::DatabaseError(e.to_string()))?;

        let mut results = Vec::new();
        for row in rows {
            results.push(row.map_err(|e| VaultError::DatabaseError(e.to_string()))?);
        }

        Ok(results)
    }

    /// Get all claims from the shard (for browsing)
    pub fn get_all_claims(&self, options: Option<QueryOptions>) -> Result<Vec<VerifiedClaim>, VaultError> {
        let conn = self.conn.lock().map_err(|e| VaultError::DatabaseError(e.to_string()))?;
        let mounted = *self.is_mounted.lock().unwrap();
        
        if !mounted {
            return Err(VaultError::NotMounted);
        }

        let opts = options.unwrap_or_default();
        let limit = opts.limit.unwrap_or(100);
        let tier_filter = opts.max_tier.map(|t| format!("WHERE c.tier <= {}", t)).unwrap_or_default();

        let sql = format!(r#"
            SELECT 
                c.claim_id,
                subj.label as subject,
                subj.entity_id as subject_id,
                c.predicate,
                CASE 
                    WHEN c.object_type = 'entity' THEN COALESCE(obj.label, c.object)
                    ELSE c.object
                END as object,
                CASE 
                    WHEN c.object_type = 'entity' THEN COALESCE(obj.entity_id, '')
                    ELSE ''
                END as object_id,
                c.object_type,
                c.tier,
                COALESCE(s.text, '') as evidence,
                COALESCE(p.source_hash, '') as source_hash,
                COALESCE(p.byte_start, -1) as byte_start,
                COALESCE(p.byte_end, -1) as byte_end
            FROM claims c
            JOIN entities subj ON c.subject = subj.entity_id
            LEFT JOIN entities obj ON c.object = obj.entity_id AND c.object_type = 'entity'
            LEFT JOIN provenance p ON c.claim_id = p.claim_id
            LEFT JOIN spans s ON p.source_hash = s.source_hash 
                AND p.byte_start = s.byte_start 
                AND p.byte_end = s.byte_end
            {}
            ORDER BY c.tier ASC, c.claim_id
            LIMIT ?
        "#, tier_filter);

        let mut stmt = conn.prepare(&sql)
            .map_err(|e| VaultError::DatabaseError(e.to_string()))?;

        let rows = stmt.query_map(params![limit], |row| {
            Ok(VerifiedClaim {
                claim_id: row.get(0)?,
                subject: row.get(1)?,
                subject_id: row.get(2)?,
                predicate: row.get(3)?,
                object: row.get(4)?,
                object_id: row.get(5)?,
                object_type: row.get(6)?,
                tier: row.get(7)?,
                evidence: row.get(8)?,
                source_hash: row.get(9)?,
                byte_start: row.get(10)?,
                byte_end: row.get(11)?,
            })
        }).map_err(|e| VaultError::DatabaseError(e.to_string()))?;

        let mut results = Vec::new();
        for row in rows {
            results.push(row.map_err(|e| VaultError::DatabaseError(e.to_string()))?);
        }

        Ok(results)
    }

    /// Get claims by following a specific entity (graph traversal)
    /// 
    /// Returns all claims where the entity is either subject or object.
    pub fn get_claims_for_entity(&self, entity_id: &str) -> Result<Vec<VerifiedClaim>, VaultError> {
        let conn = self.conn.lock().map_err(|e| VaultError::DatabaseError(e.to_string()))?;
        let mounted = *self.is_mounted.lock().unwrap();
        
        if !mounted {
            return Err(VaultError::NotMounted);
        }

        let sql = r#"
            SELECT 
                c.claim_id,
                subj.label as subject,
                subj.entity_id as subject_id,
                c.predicate,
                CASE 
                    WHEN c.object_type = 'entity' THEN COALESCE(obj.label, c.object)
                    ELSE c.object
                END as object,
                CASE 
                    WHEN c.object_type = 'entity' THEN COALESCE(obj.entity_id, '')
                    ELSE ''
                END as object_id,
                c.object_type,
                c.tier,
                COALESCE(s.text, '') as evidence,
                COALESCE(p.source_hash, '') as source_hash,
                COALESCE(p.byte_start, -1) as byte_start,
                COALESCE(p.byte_end, -1) as byte_end
            FROM claims c
            JOIN entities subj ON c.subject = subj.entity_id
            LEFT JOIN entities obj ON c.object = obj.entity_id AND c.object_type = 'entity'
            LEFT JOIN provenance p ON c.claim_id = p.claim_id
            LEFT JOIN spans s ON p.source_hash = s.source_hash 
                AND p.byte_start = s.byte_start 
                AND p.byte_end = s.byte_end
            WHERE c.subject = ? OR (c.object_type = 'entity' AND c.object = ?)
            ORDER BY c.tier ASC
        "#;

        let mut stmt = conn.prepare(sql)
            .map_err(|e| VaultError::DatabaseError(e.to_string()))?;

        let rows = stmt.query_map(params![entity_id, entity_id], |row| {
            Ok(VerifiedClaim {
                claim_id: row.get(0)?,
                subject: row.get(1)?,
                subject_id: row.get(2)?,
                predicate: row.get(3)?,
                object: row.get(4)?,
                object_id: row.get(5)?,
                object_type: row.get(6)?,
                tier: row.get(7)?,
                evidence: row.get(8)?,
                source_hash: row.get(9)?,
                byte_start: row.get(10)?,
                byte_end: row.get(11)?,
            })
        }).map_err(|e| VaultError::DatabaseError(e.to_string()))?;

        let mut results = Vec::new();
        for row in rows {
            results.push(row.map_err(|e| VaultError::DatabaseError(e.to_string()))?);
        }

        Ok(results)
    }

    /// Get the raw content text for a byte range
    /// 
    /// This enables the "Green Padlock" verification - users can see
    /// the exact source text that supports a claim.
    pub fn get_content_slice(
        &self,
        source_hash: &str,
        byte_start: i64,
        byte_end: i64,
    ) -> Result<String, VaultError> {
        let content_map = self.content_map.lock().unwrap();
        
        let content_path = content_map
            .get(source_hash)
            .ok_or_else(|| VaultError::ContentNotFound(source_hash.to_string()))?;

        let content_bytes = fs::read(content_path)
            .map_err(|e| VaultError::ContentNotFound(e.to_string()))?;

        let start = byte_start as usize;
        let end = byte_end as usize;

        if start > content_bytes.len() || end > content_bytes.len() || start > end {
            return Err(VaultError::ByteRangeError(byte_start, byte_end));
        }

        let slice = &content_bytes[start..end];
        String::from_utf8(slice.to_vec())
            .map_err(|e| VaultError::Utf8Error(e.to_string()))
    }

    /// Verify that a span's text matches the content file
    /// 
    /// Per AXM Spec Section 8: content_bytes[byte_start:byte_end].decode("utf-8") == span.text
    pub fn verify_span(&self, claim: &VerifiedClaim) -> Result<bool, VaultError> {
        if claim.source_hash.is_empty() || claim.byte_start < 0 {
            return Ok(false); // No provenance to verify
        }

        let content_text = self.get_content_slice(
            &claim.source_hash,
            claim.byte_start,
            claim.byte_end,
        )?;

        Ok(content_text == claim.evidence)
    }

    /// Get current shard metadata
    pub fn get_metadata(&self) -> Result<Option<ShardMetadata>, VaultError> {
        Ok(self.metadata.lock().unwrap().clone())
    }

    /// Check if a shard is mounted
    pub fn is_mounted(&self) -> bool {
        *self.is_mounted.lock().unwrap()
    }

    /// Get the path to the currently mounted shard
    pub fn get_shard_path(&self) -> Option<PathBuf> {
        self.shard_path.lock().unwrap().clone()
    }

    /// Execute arbitrary SQL on the mounted views (for advanced queries)
    pub fn execute_sql(&self, sql: &str) -> Result<Vec<serde_json::Value>, VaultError> {
        let conn = self.conn.lock().map_err(|e| VaultError::DatabaseError(e.to_string()))?;
        let mounted = *self.is_mounted.lock().unwrap();
        
        if !mounted {
            return Err(VaultError::NotMounted);
        }

        let mut stmt = conn.prepare(sql)
            .map_err(|e| VaultError::DatabaseError(format!("SQL error: {}", e)))?;

        let column_count = stmt.column_count();
        let column_names: Vec<String> = (0..column_count)
            .map(|i| stmt.column_name(i).unwrap_or("?").to_string())
            .collect();

        let rows = stmt.query_map([], |row| {
            let mut obj = serde_json::Map::new();
            for (i, name) in column_names.iter().enumerate() {
                // Try different types
                if let Ok(v) = row.get::<_, i64>(i) {
                    obj.insert(name.clone(), serde_json::Value::Number(v.into()));
                } else if let Ok(v) = row.get::<_, f64>(i) {
                    obj.insert(name.clone(), serde_json::json!(v));
                } else if let Ok(v) = row.get::<_, String>(i) {
                    obj.insert(name.clone(), serde_json::Value::String(v));
                } else {
                    obj.insert(name.clone(), serde_json::Value::Null);
                }
            }
            Ok(serde_json::Value::Object(obj))
        }).map_err(|e| VaultError::DatabaseError(e.to_string()))?;

        let mut results = Vec::new();
        for row in rows {
            if let Ok(v) = row {
                results.push(v);
            }
        }

        Ok(results)
    }

    /// Get statistics about the mounted shard
    pub fn get_statistics(&self) -> Result<serde_json::Value, VaultError> {
        let conn = self.conn.lock().map_err(|e| VaultError::DatabaseError(e.to_string()))?;
        let mounted = *self.is_mounted.lock().unwrap();
        
        if !mounted {
            return Err(VaultError::NotMounted);
        }

        let stats_sql = r#"
            SELECT 
                (SELECT COUNT(*) FROM entities) as entity_count,
                (SELECT COUNT(*) FROM claims) as claim_count,
                (SELECT COUNT(*) FROM claims WHERE tier = 0) as tier0_claims,
                (SELECT COUNT(*) FROM claims WHERE tier = 1) as tier1_claims,
                (SELECT COUNT(*) FROM claims WHERE tier = 2) as tier2_claims,
                (SELECT COUNT(*) FROM provenance) as provenance_count,
                (SELECT COUNT(*) FROM spans) as span_count,
                (SELECT COUNT(DISTINCT predicate) FROM claims) as unique_predicates
        "#;

        let mut stmt = conn.prepare(stats_sql)
            .map_err(|e| VaultError::DatabaseError(e.to_string()))?;

        let result = stmt.query_row([], |row| {
            Ok(serde_json::json!({
                "entities": row.get::<_, i64>(0)?,
                "claims": row.get::<_, i64>(1)?,
                "claims_by_tier": {
                    "tier_0": row.get::<_, i64>(2)?,
                    "tier_1": row.get::<_, i64>(3)?,
                    "tier_2": row.get::<_, i64>(4)?
                },
                "provenance_links": row.get::<_, i64>(5)?,
                "evidence_spans": row.get::<_, i64>(6)?,
                "unique_predicates": row.get::<_, i64>(7)?
            }))
        }).map_err(|e| VaultError::DatabaseError(e.to_string()))?;

        Ok(result)
    }

    /// Unmount the current shard
    pub fn unmount(&self) -> Result<(), VaultError> {
        let conn = self.conn.lock().map_err(|e| VaultError::DatabaseError(e.to_string()))?;
        
        conn.execute_batch(r#"
            DROP VIEW IF EXISTS claims;
            DROP VIEW IF EXISTS entities;
            DROP VIEW IF EXISTS provenance;
            DROP VIEW IF EXISTS spans;
        "#).map_err(|e| VaultError::DatabaseError(e.to_string()))?;

        *self.is_mounted.lock().unwrap() = false;
        *self.metadata.lock().unwrap() = None;
        *self.shard_path.lock().unwrap() = None;
        self.content_map.lock().unwrap().clear();

        Ok(())
    }
}

impl Default for Vault {
    fn default() -> Self {
        Self::new()
    }
}

// ============================================================================
// VERIFICATION MODULE (Foundational for Clarion)
// ============================================================================

pub mod verify {
    use super::*;
    use blake3::Hasher;
    use std::collections::BTreeMap;

    /// Compute the Merkle root of a shard
    /// 
    /// Per AXM Spec Section 4:
    /// - Excludes manifest.json and sig/*
    /// - leaf = Blake3(relpath_utf8 + 0x00 + file_bytes)
    /// - Sorted by UTF-8 byte order of relpath
    pub fn compute_merkle_root(shard_path: &Path) -> Result<String, VaultError> {
        let mut leaves: BTreeMap<String, [u8; 32]> = BTreeMap::new();

        // Walk the shard directory
        fn collect_files(
            base: &Path,
            current: &Path,
            leaves: &mut BTreeMap<String, [u8; 32]>,
        ) -> Result<(), VaultError> {
            for entry in fs::read_dir(current)
                .map_err(|e| VaultError::VerificationError(e.to_string()))?
            {
                let entry = entry.map_err(|e| VaultError::VerificationError(e.to_string()))?;
                let path = entry.path();
                let relpath = path.strip_prefix(base)
                    .map_err(|e| VaultError::VerificationError(e.to_string()))?
                    .to_string_lossy()
                    .replace('\\', "/"); // Normalize to POSIX

                // Skip excluded paths
                if relpath == "manifest.json" || relpath.starts_with("sig/") {
                    continue;
                }

                if path.is_dir() {
                    collect_files(base, &path, leaves)?;
                } else {
                    let file_bytes = fs::read(&path)
                        .map_err(|e| VaultError::VerificationError(e.to_string()))?;
                    
                    // leaf = Blake3(relpath_utf8 + 0x00 + file_bytes)
                    let mut hasher = Hasher::new();
                    hasher.update(relpath.as_bytes());
                    hasher.update(&[0x00]);
                    hasher.update(&file_bytes);
                    let hash = hasher.finalize();
                    
                    leaves.insert(relpath, *hash.as_bytes());
                }
            }
            Ok(())
        }

        collect_files(shard_path, shard_path, &mut leaves)?;

        // Build Merkle tree
        let mut hashes: Vec<[u8; 32]> = leaves.values().cloned().collect();

        if hashes.is_empty() {
            return Err(VaultError::VerificationError("No files to hash".to_string()));
        }

        while hashes.len() > 1 {
            let mut next_level = Vec::new();
            
            for chunk in hashes.chunks(2) {
                let mut hasher = Hasher::new();
                hasher.update(&chunk[0]);
                if chunk.len() > 1 {
                    hasher.update(&chunk[1]);
                } else {
                    // Duplicate last if odd
                    hasher.update(&chunk[0]);
                }
                next_level.push(*hasher.finalize().as_bytes());
            }
            
            hashes = next_level;
        }

        Ok(hex::encode(hashes[0]))
    }

    /// Verify shard integrity
    pub fn verify_shard(shard_path: &Path) -> Result<TrustLevel, VaultError> {
        // Read manifest
        let manifest_path = shard_path.join("manifest.json");
        let manifest_bytes = fs::read(&manifest_path)
            .map_err(|e| VaultError::VerificationError(e.to_string()))?;
        let manifest: serde_json::Value = serde_json::from_slice(&manifest_bytes)
            .map_err(|e| VaultError::VerificationError(e.to_string()))?;

        let expected_root = manifest
            .get("integrity")
            .and_then(|i| i.get("merkle_root"))
            .and_then(|r| r.as_str())
            .ok_or_else(|| VaultError::VerificationError("Missing merkle_root".to_string()))?;

        // Compute actual Merkle root
        let actual_root = compute_merkle_root(shard_path)?;

        if actual_root != expected_root {
            return Err(VaultError::VerificationError(format!(
                "Merkle root mismatch: expected {}, got {}",
                expected_root, actual_root
            )));
        }

        // TODO: Verify Ed25519 signature
        // For now, return SignatureOnly since we verified Merkle but not signature
        Ok(TrustLevel::SignatureOnly)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_vault_creation() {
        let vault = Vault::new();
        assert!(!vault.is_mounted());
    }
}
