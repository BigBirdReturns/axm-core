# AXM intake-floor invariants

These invariants govern the pre-shard compatibility layer. They do not modify or weaken any Genesis invariant.

1. **Exact representation first.** Every observation binds the exact input bytes or a locally verifiable external locator before parsed projections are trusted.
2. **Three identities remain distinct.** Observation identity, content identity, and logical-object/version identity are never collapsed.
3. **Replay is idempotent.** `recorded_at` does not alter observation identity; re-admission may produce another receipt without creating another source event.
4. **Adapters possess no authority.** Every observation and adapter manifest declares `authority = observation_only`.
5. **Coverage is scoped.** `complete` is invalid without a denominator, reconciled arithmetic, and named exclusions.
6. **Security uncertainty is visible.** Unknown sensitivity, personal-data presence, or credential presence prevents C5 conformance.
7. **Source code is attributable.** C4 requires adapter source revision and license.
8. **Translation is lossless at the payload boundary.** Parsed mappings supplement exact source bytes; they never replace them.
9. **No implicit network access.** Built-in validation and bridges do not fetch locators, follow source URLs, or call external services.
10. **No implicit process execution.** Core may validate a stdio command declaration, but an operator or orchestrator explicitly launches the adapter under policy.
11. **No Genesis impersonation.** `obs1_` and `cnt1_` are pre-shard SHA-256 identities and are never represented as `sh1_`, `e1_`, `c1_`, `s1_`, or `p1_`.
12. **A domain spoke decides admission.** C5 means the source handoff is explicit enough to evaluate; it does not mean that any domain claim should be compiled.
