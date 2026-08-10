# AP-410 dispatch v3 Windows `cmd.exe` preflight

This draft, unmerged carrier exercises the dispatch v3 batch-launcher control flow on `windows-2025`. It checks native `cmd.exe` quoting and exit-code propagation, both Python resolution paths, the Python 3.11 floor, PowerShell SHA-256 refusal codes, and fresh/resume mode forwarding from a path containing spaces.

The workflow uses probe payload coordinates rather than the 1.4 MB physical operator payload. Its authority is therefore limited to Windows shell semantics. It does not execute the physical harness, observe the named Windows 11 host, produce a physical result, satisfy any AP-410 interaction or visual cell, accept AP-410 or G3, or change `[G0, G1, G2]`.
