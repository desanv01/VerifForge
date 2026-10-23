# Controlled project failures

`counter.v` is the trusted source mirrored in the adapter. Both fault recipes
use these same source bytes. `wrong_top` selects `deliberately_wrong_top` instead
of `vf_counter`; `missing_source` reads `missing.v` instead of `counter.v`.
The candidate repairs only the selected project recipe and leaves source intact.
These are deliberate fixture misconfigurations, not discovered vendor defects.

Preparation writes a complete source snapshot, Tcl recipe, hashed manifest and
prepared status in a unique directory for each baseline and candidate. No vendor
execution or HDL functional check has been performed. See `docs/project/VIVADO.md`.
