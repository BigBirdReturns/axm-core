# AXM Aperture extension tables

Spectra may project Genesis-sealed AXM Aperture package extensions into disposable DuckDB tables through `axiom_runtime.aperture_extensions.ApertureExtensionRuntime`. The runtime requires an explicit trusted publisher key held outside the shard, verifies the complete shard before reading any extension, validates all registered Aperture extensions as one bundle, and mutates only the local DuckDB connection.

The v1 extensions are `aperture-package-revisions@1`, `aperture-positions@1`, `aperture-facts@1`, `aperture-causal-edges@1`, `aperture-reveals@1`, `aperture-edition-maps@1`, and `aperture-sources@1`. Every value is a JSON string. Integers and decimals are canonical decimal strings. Lists are compact canonical JSON-array strings. Optional identifiers and interval coordinates use the empty string rather than null.

The runtime creates bare union views named `aperture_package_revisions`, `aperture_positions`, `aperture_facts`, `aperture_causal_edges`, `aperture_reveals`, `aperture_edition_maps`, and `aperture_sources`. They are query caches only. Arc remains narrative authority, Aperture remains edition and viewer-state authority, and Genesis remains byte, signature, and extension authority.

```python
from axiom_runtime.aperture_extensions import ApertureExtensionRuntime
from axiom_runtime.engine import SpectraEngine

spectra = SpectraEngine(...)
aperture = ApertureExtensionRuntime(spectra)
mount = aperture.mount_verified_shard("/verified/package-shard", "/keys/publisher.pub")
rows = spectra.query_json("SELECT fact_id, proposition FROM aperture_facts")
aperture.unmount(mount.mount_id)
```

A registered extension fails closed on missing manifest/file parity, noncanonical JSONL, unknown or missing fields, non-string values, invalid identities, broken package, position, fact, reveal, or TimeMap references, impossible segment interval shapes, invalid rational rates, or Genesis verification failure. Unknown non-Aperture extensions remain outside this runtime.
