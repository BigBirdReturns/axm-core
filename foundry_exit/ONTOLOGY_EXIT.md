# AXM Foundry Ontology Exit v0

Take a Palantir Foundry tenant owner's **own** captured Ontology API v2
responses and, with **one command**, produce a **genesis-sealed shard** in which:

- the ontology's **structure is queryable** through the repo's own Spectra
  engine (`axiom_runtime`), and
- the **verbatim API responses are preserved byte-for-byte** as sealed content,
- **detached-verifiable** with an out-of-band public key.

**No Palantir code, no credentials, no network calls live in this code path.**
The tenant owner runs the GETs themselves (out of band, with their own token);
this feature only consumes the JSON they saved.

> **Evidence tier (honest).** The wire shapes here are **reconciled against
> Palantir's PUBLISHED Ontology API v2 docs** (links below). This is **NOT yet
> proven against an authorized live tenant** — the bundled fixture
> (`samples/foundry_ontology_fixture/`) is an **invented sample in the documented
> wire shape**, not real tenant data. What IS proven live (against that fixture,
> in-repo): load → translate → seal through the real genesis kernel → detached
> verify PASS → mount into Spectra → query. A later authorized-tenant capture is
> what would upgrade this tier.

## The three GETs a tenant owner runs

Against **their own tenant**, with **their own** bearer token, out of band. These
are read-only list endpoints on the public Ontology API v2. Substitute:
`$HOST` (e.g. `my-tenant.palantirfoundry.com`), `$ONTOLOGY` (ontology API name or
rid), `$TOKEN` (a bearer token the owner already holds), and `$OBJECT_TYPE`
(e.g. `Flight`).

```bash
# 1) List object types  ->  objectTypes.json   (ListObjectTypesV2Response)
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://$HOST/api/v2/ontologies/$ONTOLOGY/objectTypes" \
  > capture_dir/objectTypes.json

# 2) List OUTGOING link types for ONE object type  ->  linkTypes/<ObjectType>.json
#    (ListOutgoingLinkTypesResponseV2)  — repeat per object type you want links for
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://$HOST/api/v2/ontologies/$ONTOLOGY/objectTypes/$OBJECT_TYPE/outgoingLinkTypes" \
  > capture_dir/linkTypes/$OBJECT_TYPE.json

# 3) List objects (instances) for ONE object type  ->  objects/<ObjectType>.json
#    (ListObjectsResponseV2)  — repeat per object type you want instances for
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://$HOST/api/v2/ontologies/$ONTOLOGY/objects/$OBJECT_TYPE" \
  > capture_dir/objects/$OBJECT_TYPE.json
```

For paginated results, either capture the first page, or capture each page and
save the **JSON array of page objects** in one file (see multi-page below).

Documented endpoints (cite):
- object types — <https://www.palantir.com/docs/foundry/api/ontologies-v2-resources/object-types/list-object-types>
- outgoing link types — <https://www.palantir.com/docs/foundry/api/ontologies-v2-resources/object-types/list-outgoing-link-types>
- objects — <https://www.palantir.com/docs/foundry/api/ontologies-v2-resources/ontology-objects/list-objects>

## Capture-directory convention

The directory layout is **our** convention; the **file contents** are exactly
Foundry's documented wire shapes.

```
capture_dir/
  objectTypes.json                     REQUIRED  ListObjectTypesV2Response
  linkTypes/<objectTypeApiName>.json   OPTIONAL  ListOutgoingLinkTypesResponseV2  (one per source type)
  objects/<objectTypeApiName>.json     OPTIONAL  ListObjectsResponseV2            (first page or concatenated)
```

- **Links are OUTGOING and keyed by SOURCE.** `linkTypes/Flight.json` holds the
  links leaving `Flight`; each entry's `objectTypeApiName` names the **target**.
- **Tolerant parse, strict validation.** Unknown/extra fields are ignored and
  kept verbatim (Palantir may add fields). Missing **required** fields fail with
  an error that names the file and the key.
- **Multi-page.** If a file is a JSON **array** of response objects, it is
  treated as concatenated pages (the `data` arrays are merged; the last
  `totalCount` wins).

## Run it

```bash
# genesis kernel must be on PATH (axm-build / axm-verify)
python -m foundry_exit.run_ontology_exit <capture_dir> --out ontology_exit_out
# with no argument it uses the bundled fixture:
python -m foundry_exit.run_ontology_exit --out ontology_exit_out
```

Output: `ontology_exit_out/ontology_exit_packet.{json,md}` — shard id, counts,
detached-verify status, and the honest evidence-tier statement. The command
exits **nonzero** if the detached verify does not PASS.

## Claims vocabulary (what becomes queryable)

Every claim's evidence is bound to a unique byte span in the sealed
`content/source.txt`. Entity-object claims resolve `subject`/`object` to
`e1_…` entity ids in `graph/claims.jsonl`; JOIN `entities` on `entity_id` to
query by label. Literal claims keep the literal verbatim in `object`.

| Subject (entity label) | Predicate | Object | object_type | Tier |
|---|---|---|---|---|
| `objectType/{apiName}` | `has_property` | `property/{type}.{prop}` | entity | 1 |
| `property/{type}.{prop}` | `has_type` | `"<dataType.type>"` | literal:string | 1 |
| `objectType/{apiName}` | `primary_key` | `"<propName>"` | literal:string | 1 |
| `objectType/{source}` | `links_to` | `objectType/{target}` | entity | 1 |
| `link/{linkApiName}` | `cardinality` | `"ONE"`\|`"MANY"` | literal:string | 1 |
| `link/{linkApiName}` | `foreign_key` | `"<prop>"` | literal:string | 1 |
| `objectType/{apiName}` | `instance_count` | `"<N>"` | literal:integer | 0 |
| `objectType/{apiName}` | `instance_count_declared` | `"<totalCount>"` | literal:integer | 0 |
| `objectType/{apiName}` | `instances_captured` | `"<rows present>"` | literal:integer | 0 |

Entities minted: `objectType/{apiName}` (`object_type`),
`property/{type}.{prop}` (`property`), `link/{apiName}` (`link`).

**Instance counts are honest about partial captures.** `instance_count` is
emitted only when `objects/<X>.json` is present, and `N` is the number of `data`
rows **actually present in the sealed file** (never `totalCount`). If a declared
`totalCount` differs from the rows present, the shard instead emits **both**
`instance_count_declared` (the tenant's declared total) and `instances_captured`
(rows present) — so a partial capture is **declared, not hidden**. Instance
counts are **tier 0** (weakest evidence) because they describe a page snapshot,
not the whole object set.

## Example query (through Spectra)

```sql
-- object types
SELECT DISTINCT e.label
FROM claims c JOIN entities e ON e.entity_id = c.subject
WHERE e.entity_type = 'object_type';

-- Flight -> Aircraft link
SELECT s.label, o.label
FROM claims c
JOIN entities s ON s.entity_id = c.subject
JOIN entities o ON o.entity_id = c.object
WHERE c.predicate = 'links_to' AND s.label = 'objectType/Flight';
```

## Sealed content layout (a stated boundary)

The genesis compiler seals only **top-level** files in `content/`. So the
capture's `linkTypes/<X>.json` and `objects/<X>.json` are staged under
**flattened** top-level names inside the shard — `linkTypes__<X>.json` and
`objects__<X>.json`. The **file bytes are byte-for-byte identical** to the
capture; only the sealed filename is flattened. `objectTypes.json` keeps its
name. `content/source.txt` is the canonical text the claims cite.

## Custody / external-id invariant

Palantir `rid` / `apiName` values appear **only** as entity labels, claim
literals, and sealed content bytes. They are **never** the shard identity and
**never** a custody id. The custody id is the **genesis-derived `sh1_`** on the
sealed manifest bytes (`axm_verify.crypto.derive_shard_id`). The
identity-bearing `manifest.json` contains **no** Palantir rid. This mirrors the
dataset-exit "external-Palantir-ID-never-becomes-custody-ID" invariant.

## What v0 deliberately does NOT do

- **No actions.** The v2 list endpoints used here don't carry action types;
  `action_refs` is empty. (A later capture of the actions surface could add
  them.)
- **No interfaces / shared property types.** Not captured or modeled in v0.
- **No security markings.** The list endpoints don't carry them; v0 does **not**
  invent them (`security_markings` is empty). No Palantir permissions are made
  portable.
- **No dataset backing.** `backing_dataset_rids` is empty — not carried by these
  endpoints.
- **Instances are sealed as content, not per-row claims.** v0 records instance
  **counts** as tier-0 claims and preserves the verbatim `objects/*.json`; it
  does not mint a claim per object row.
- **No network, no credentials, no Palantir code.** The tenant owner runs the
  GETs; this feature only reads saved JSON.
