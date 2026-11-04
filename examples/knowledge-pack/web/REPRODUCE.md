# Reproduce this release

Source: https://github.com/BigBirdReturns/axm-core/tree/main/examples/knowledge-pack

Download `downloads/axm-knowledge-pack-source-0.2.0.zip` or clone the repository. Enter its `examples/knowledge-pack` directory (the archive uses `axm-knowledge-pack`). With Python 3.12+ and Go 1.24+, run:

```
python scripts/build.py --test --web --native
```

The release was built with Go 1.27.3, `-trimpath`, `-buildvcs=false`, and `-ldflags=-s -w`. Public Go dependencies are pinned by module version and checksum. The script uses only Python standard-library modules to orchestrate the build and native conformance checks.

`web/conformance/index.json` declares every fixture, expected result, and SHA-256. `build/native-conformance.json` is produced by running those actual files. The source contains the browser interaction/conformance harness for a current Chrome and Node 22+ environment. Live-public readback and independent-implementation reports are separate receipts; a build result alone is not a deployment claim.

The published example key comes from the same distribution as the example. This demonstrates integrity against a pinned demonstration identity. Authenticate another publisher's public key through a separate trusted channel.
