# Historical planner-role source review

These two inert `.py.txt` files preserve the exact VerifForge source reviewed
before later repair support changed the active worker and dispatcher. Copied
byte-for-byte from the already public revision
[`d80b03f3ff7554e9c97ad5c298d99ed07529191c`](https://github.com/desanv01/VerifForge/commit/d80b03f3ff7554e9c97ad5c298d99ed07529191c)
through connected GitHub's base64 file response. Original source attribution and
headers are preserved. They are test data, not a second implementation.

| Snapshot | Original public source | Bytes | SHA256 |
|---|---|---:|---|
|dispatch.py.txt|[core/dispatch.py](https://github.com/desanv01/VerifForge/blob/d80b03f3ff7554e9c97ad5c298d99ed07529191c/src/veriforge/core/dispatch.py)|3182|60151d8e6bac9ae767653373f845af98cbf9d6a3b8b4b5c0b9ad431bb044c384|
|worker.py.txt|[core/worker.py](https://github.com/desanv01/VerifForge/blob/d80b03f3ff7554e9c97ad5c298d99ed07529191c/src/veriforge/core/worker.py)|7534|3724ee525b07ab119bad4780c392e7586e41d2269a605b87773761f9b21d0e4f|

Git blob identities are respectively
`560240c85efbafd755046d14a93b6649ee0dcce2` and
`6c8580d2e223bda82b9297f480b65d0087b81dbd`.

The historical freeze test hashes these snapshots and keeps the original
17-added-control-keyword AST proof over the unchanged public test baselines.
The unchanged runtime_configuration.py pin still checks active source. Never
import, execute or substitute these snapshots for active modules. All behavioral
control tests continue to exercise active worker/dispatch. Current repair source
has separate focused developer tests and deferred feature-batch independent
review; this fixture does not extend historical review to those new changes.
