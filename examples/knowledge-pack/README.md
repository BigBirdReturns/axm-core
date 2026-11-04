# AXM Knowledge Pack: public reference 0.2.0

[Why this matters](https://bigbirdreturns.github.io/axm-tools/knowledge-pack-open/) · [Open the browser demonstration](https://bigbirdreturns.github.io/axm-tools/knowledge-pack/) · [Independent reader challenge](https://github.com/BigBirdReturns/axm-core/issues/58) · [Transport profile](TRANSPORT.md) · [Conformance vectors](web/conformance/index.json)

Open a real package, follow retained claims to exact source bytes, inspect its signed history, and run actual tamper checks. The browser downloads a WebAssembly verifier, then works locally. Opening a personal file sends no file content to a server. No account, model, backend service, or installation is needed for the browser demonstration.

This repository provides the material needed to reproduce and build upon that behavior: a Python creator, a native Go reader, the Go-to-WebAssembly browser adapter, public positive and negative fixtures, the transport profile, and test/build commands. Apache-2.0 covers this reference; third-party notices remain with their code.

## Run the published artifact

Download the Windows or Linux native reader from the public page, unpack it, and run:

```sh
./axm-pack-reader inspect example.axmpack.zip --trusted-key trusted-demo.pub
./axm-pack-reader challenge example.axmpack.zip --trusted-key trusted-demo.pub source
```

Windows uses `axm-pack-reader.exe`. A valid pack exits 0; a failed integrity check exits 1; malformed transport or command input exits 2. Challenges operate on disposable copies, preserving the original. The example key is intentionally public and comes with this reference. It is a test trust anchor, not independent authentication of a human or institution. Opening another publisher's package requires a key you trust through a separate channel.

## Reproduce from source

Requirements: Python 3.12 or newer and Go 1.24 or newer. The published release uses Go 1.27.3. Dependency versions and checksums live in `go/go.mod` and `go/go.sum`; the initial build may download these public dependencies. The browser and native reader make no network calls to verify a package.

From this directory or the unpacked source archive:

```sh
python scripts/build.py --test --web --native
python -m http.server 8080 --bind 127.0.0.1 --directory web
```

The first command builds the native verifier, runs Go unit tests and all public conformance cases, builds the WebAssembly reader, and emits Windows/Linux/source ZIPs plus `web/SHA256SUMS.txt`. The second command serves the same static files used by the public demonstration. Build commands have no estate-specific path, credential, signing secret, or private service dependency. Builds reuse the already published fixture bytes rather than regenerating the example's identity.

Creator regressions generate fresh ephemeral keys and exercise 34 source-preservation, archive-safety, history, and Python/Go agreement checks. After installing the local Python kernel, run `python scripts/test_creator.py`. The upstream Python library is retained; upstream tests that depend on their own separately published test private key are not bundled.

The reference contains the retained Genesis Python source under `vendor/axm-genesis`, and a filesystem-adapted copy of the independent Go verifier under `go/internal/genesis`. `UPSTREAM.json` records the original Go source hashes and the explicit read-only `io/fs.FS` adaptation. Genesis's canonicalization, signature, Merkle, source-span, identity, and profile checks remain subject to the same public vectors. The authoritative Genesis repository is unchanged. A WebAssembly port is another execution environment, not an independently authored third verifier.

## Create another package

Install the local Python kernel into an isolated environment:

```sh
python -m venv .venv
# Activate the environment using your operating system's standard command.
python -m pip install './vendor/axm-genesis[mldsa-compat,dev]'
python build_example.py --state ./demo-state --genesis ./vendor/axm-genesis --go-verifier ./build/axm-verify-go
python studio.py --state ./demo-state --go-verifier ./build/axm-verify-go --port 8796
```

The example builder generates a fresh local demonstration identity and a separate public key. Private signing keys stay in the selected state directory and never belong in a release or repository. The creator accepts source text and explicit exact excerpts; it does not claim automatic semantic extraction. The UI supports successor editions, preserving prior records byte for byte.

## Interoperability contract

`axm-pack-preview/0.1` is a proposed ZIP transport around unchanged Genesis v1 shards. Its untrusted `pack.json` index never decides authenticity. Readers independently verify each signed shard, derive its identity, require a complete included predecessor history, and derive the unique presented head. `TRANSPORT.md` defines accepted inventory, limits, rejection behavior, and the remaining scope.

The public conformance manifest includes the Genesis gold shard, 16 canonical kernel cases, a valid two-edition package, three signature/content mutations, a wrong key, a false head, missing history, traversal, and duplicate JSON keys. Every case carries its own SHA-256 and expected status. `scripts/conformance.py` checks the native CLI; the browser harness runs the same files through WebAssembly and exercises the visible interface.

To report another implementation, include the source revision, vector-manifest hash, result for each case, platform, and exact discrepancies. An implementation report can be opened as a public issue in this repository. Passing these tests establishes only their stated scope, not semantic correctness, exhaustive security review, independent publisher authentication, industry adoption, or standards-body endorsement.

## Boundaries

Signatures establish artifact integrity and the retained evidence relationships. Source truth, completeness, currentness, domain suitability, trust-key distribution, revocation, and task-resumption quality need separate evidence. Multi-publisher packages and external-history retrieval remain outside this transport draft. Unknown domain profiles remain visible as unchecked.

The browser verifier uses a bounded worker, archive limits, safe-path checks, duplicate-key detection, and a read-only filesystem. It executes no package content. Its actual bytes, source, and failures are public so another implementation can challenge the result.
