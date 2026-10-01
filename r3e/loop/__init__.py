"""Closed red–blue loop for RTL repair (spec: docs/design/R3E_Core_Contributions_Spec.md).

Modules:
- ``corpus``: carriers, challenges, clone-aware splits.
- ``sim``: runner-owned Icarus verdict tiers.
- ``blue``: knowledge-conditioned repair with visible feedback and escalation.
- ``operators``, ``red``: hybrid Red (hypothesis + catalog edit + admission).
- ``qualification``: paired replay promotion and the usage monitor.
- ``orchestrator``: the round loop with ledgers and checkpoints.
- ``evaluate``: post-chain holdout evaluation in none/static/matched/shuffled modes.
- ``env``, ``budget``: credentials read from an ``.llm`` file, plus a hard call cap.
- ``fakes``: scripted local transports, for plumbing checks only.
"""
