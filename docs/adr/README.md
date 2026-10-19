# Architecture decisions

One file per decision that would be expensive to reverse. Each says what was true when it was taken, what was
decided, what follows from it, and what would make it worth revisiting. They are **records, not plans**: a
decision here has already been built, or has already been refused.

Where a decision came from a measurement, the measurement is in [`../lessons.md`](../lessons.md) and is linked
rather than repeated. Where it is the owner's and not ours, it says so and is listed in
[`../superset-studio.md` §13](../superset-studio.md).

| | Decision | Status |
|---|---|---|
| [0001](0001-superset-is-the-bi-application.md) | Superset is the BI application; SharpSight extends it | accepted |
| [0002](0002-native-first-then-the-extension-no-fork.md) | Native first, then the extension — and no fork | accepted |
| [0003](0003-the-engine-is-an-overlay.md) | The AI BI engine is an overlay the kit starts without | accepted |
| [0004](0004-superset-only-through-its-rest-api.md) | Superset is touched only through its REST API | accepted |
| [0005](0005-the-studio-is-a-page-not-a-panel.md) | The Studio is a page of Superset's own, not an extension panel | accepted |
| [0006](0006-the-canvas-edits-the-specification.md) | The canvas edits the specification, not the dashboard | accepted |
| [0007](0007-the-layout-belongs-to-the-specification.md) | The layout belongs to the specification, in Superset's own shape | accepted |
| [0008](0008-a-tile-is-a-photograph.md) | A tile is a photograph; Preview is the real dashboard | accepted |
| [0009](0009-the-draft-is-the-third-state.md) | The draft is the third state, and carries a token | accepted |
| [0010](0010-a-commit-goes-through-the-pipeline.md) | A commit goes through the pipeline; nothing in Superset approves | accepted |
| [0011](0011-the-gateway-token-has-an-allowlist.md) | Superset's own token opens an allowlist, not the engine | accepted |
| [0012](0012-the-copilot-is-deterministic-and-proposes.md) | The copilot is deterministic, and proposes rather than changes | accepted |
| [0013](0013-no-data-preview-in-the-studio.md) | The Studio shows no sample of the data | accepted |
| [0014](0014-no-build-step-and-no-second-react.md) | No build step, and no second React on the page | accepted |
| [0015](0015-a-section-owns-whole-rows.md) | A section owns whole rows, and a chart does not leave its section | accepted |
| [0016](0016-postgresql-only-for-governed-data.md) | Governed data is PostgreSQL only, and the matrix says so | accepted |
| [0017](0017-one-superset-so-promotion-is-a-gate.md) | One Superset, so promotion is a gate and a package | accepted |
