# Recorded import fixtures

These are bounded redacted extracts of the separately granted October 29, 2026
native query and failed post-synthesis attempts. Tests import recordings; they do
not execute Vivado. The synthesis attempt stopped on its non-cache storage cap,
with synthesis incomplete and no generated reports. No timing or CDC result is
included or inferred.

Only literal VF version/part/stage markers are copied from stdout. Receipt fields
are allowlisted; argv, working directories, PID, user paths, raw diagnostics,
environment, source bytes and scratch outputs are excluded. The stdout hash is
recomputed for this redacted extract. `original_private_receipt_sha256` identifies
the preserved private receipt without copying it or authenticating its claims.
Source/XDC hashes and the unchanged 10 ns clock come from the recorded plan and
matching receipt entries. They are identities, not a fresh source inspection.

Synthetic report text appears only in temporary test mutations and is explicitly
marked synthetic. Fixtures contain no synthesis-passed or report-success marker.
