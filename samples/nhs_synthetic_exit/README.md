# SYNTHETIC NHS-shaped ontology sample

**This directory contains NO real data.** It is an *invented* NHS-flavoured
ontology in Palantir's published Foundry Ontology API v2 wire shape, used to
demonstrate the exit-readiness channel (see
[`../foundry_exit/EXIT_READINESS_NHS.md`](../../foundry_exit/EXIT_READINESS_NHS.md)).

- Patient references are `SYN-`-prefixed and **deliberately not valid NHS
  numbers**. Names (`Ada Testcase`, `Grace Placeholder`, …) are obvious
  placeholders. No real person, practice, ward, or clinician is represented.
- Layout matches the exit's capture-dir convention:
  - `objectTypes.json` — `ListObjectTypesV2Response` (Patient, Encounter, Ward, Clinician)
  - `linkTypes/Encounter.json` — `ListOutgoingLinkTypesResponseV2` (Encounter → Patient, Encounter → Ward)
  - `objects/Patient.json` — `ListObjectsResponseV2` (5 synthetic rows; `totalCount` 1200 to exercise the honest declared-vs-captured path)

Run it:

```
python -m foundry_exit.run_ontology_exit samples/nhs_synthetic_exit --out ./nhs_exit_out
```

The channel activates on real data only when a data controller with lawful
authority decides — never covertly, never here.
