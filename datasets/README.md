# Frozen public benchmark inputs

This directory contains only the public RTL, testbench, oracle, and dependency
files needed by the released frozen evaluators. It contains no model responses,
repair candidates, run logs, aggregate metrics, registry state, or other
experiment results.

## Frozen sets

| Manifest | Cases | Manifest SHA-256 |
|---|---:|---|
| `manifests/cirfix39.jsonl` | 39 | `b6cf4cbc93e600e5bf5ae4b52364a9b71f7450d9e6d9e24ab724423844ad05a4` |
| `manifests/literature32.jsonl` | 32 | `d4189d82efc418f2a8d17b69611ec1300db2e9ebfa7ad6d98e21439cd38c9661` |
| `manifests/strider14.jsonl` | 14 | `60315b1c2f2a5f719b3bcd84bd32f4ae8b39c396cae1722c1e5b97da91d44f21` |
| `manifests/rtlfixer50.jsonl` | 50 | `d7eec0bce9ed61b42b9ba4861e83bcca049176e0fa11af5a0095c28b47ab8de7` |

Every manifest path is repository-relative. Every referenced input records its
SHA-256 and byte size; every case records a canonical case hash. Verify the
complete package from the repository root:

```bash
python scripts/verify_datasets.py
```

`Literature-32` is an exact frozen comparison subset of the CirFix material.
It is a separate manifest but intentionally reuses identical files under
`cases/cirfix/`. Resource-limited cases remain present in the manifest; a
runner may record them as not run/unrepaired according to the declared fixed
denominator protocol, but must not silently remove them.

## Upstream provenance and licenses

- CirFix / Literature-32: `hammad-a/verilog_repair`, MIT.
- Strider-14: `hejy47/Strider`, GPL-3.0-only, frozen from commit
  `ab13ec8861cfe35d67183a40d0da5b4b631d9639`.
- RTLFixer-50: `NVlabs/RTLFixer`, MIT, frozen from commit
  `24ceebd9176d59bf302e935a800fcb46a10c634c`.

License texts are under `licenses/`. Copyright and contact notices already
present in upstream benchmark files are preserved verbatim for attribution;
they are not identities of this artifact's authors.

`scripts/package_public_datasets.py` documents the fail-closed conversion from
machine-local source manifests to this portable layout. It is a packaging
utility, not an experiment runner, and requires all source locations to be
passed explicitly.
