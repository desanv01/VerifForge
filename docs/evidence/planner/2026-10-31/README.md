# Bounded planner lifecycle qualification

The retained [run 40958744363](https://github.com/desanv01/VerifForge/actions/runs/40958744363), at commit `abee04ac21869396dd47388efac837dc45b4fe88`, passed independent evidence review on 31 October 2026. It executed PostgreSQL, Valkey, RQ and controlled processes with **mock HTTP**. It made no real provider calls and executed no engineering tools.

| Case | Checked outcome |
|---|---|
| Duplicate dispatch | Two queue deliveries produced one mock request, with cooperative cleanup and an idle resource. |
| Resource fencing | Both recorded database waiters reached the owned barrier through the observed lock chain. Only one proceeded to HTTP; the other remained unstarted. An old owner's stale grant was rejected. |
| Known and unknown transport | Mock HTTP 429 and a disconnected response remained distinct; duplicate delivery did not resend either request. |
| Fixture PAUSED control | Held HTTP was interrupted, the remote outcome remained unknown, and local cooperative cleanup settled before releasing capacity. |
| Late cancellation | The campaign stayed cancelled; late completion did not replace the unknown outcome with a fabricated response. |
| Worker death and replacement | The identified worker was killed. Its resource remained quarantined as unknown, and the replacement refused work. This proves the bounded quarantine case, not successful automatic recovery. |

The audit verified 74 retained files totaling 363,252 bytes, 232 committed source hashes and 220 installed identities. Eight rounds used synthetic seed attempts; twelve actual queue deliveries produced seven mock requests. The seed attempt rows are fixture inputs, not simulated hardware executions. Recorded mock token usage is synthetic and billing fields are null.

Six non-root evidence-mount checks passed. The empty ephemeral PostgreSQL schema migrated to `13engineeringprojects`, with no pending model/schema operations. This does not qualify migration or restore of a populated application database. Preserved cleanup receipts and their matched controller source establish owned cleanup; a separate final raw Docker inventory was not retained.

Reproduction uses `.github/workflows/planner-postgres-acceptance.yml` at the stated commit and its fixed six-case fixture. The four earlier failed qualification runs remain failures; the narrow fixture correction in [PR #55](https://github.com/desanv01/VerifForge/pull/55) did not relax the design requirements or execution budgets.

Evidence identity:

- Sorted retained filename/hash/size vector SHA-256: `c0bab1a98c90e79bbf86102201dcc4abaa60506bf43c7dd0de15306f7d4e4592`.
- Raw source manifest SHA-256: `04ff590d67eb2665bbcad39f67fb95d91fc745569f41543b56a13f0b65acb31c`.
- Executed image: `sha256:b0a24cb79908ca9c2201635ffdc85fef26deaf8f191d24173c6efcbc3693fbda`.

The workflow configured one-day artifact retention; the public run currently lists no artifact download, and its logs require sign-in. The extracted evidence is preserved locally. This public page is an audit summary, not a replacement raw bundle. Actual application adoption, populated-database recovery, browser acceptance and real Meta-to-engineering-tool feedback remain separate open gates.
