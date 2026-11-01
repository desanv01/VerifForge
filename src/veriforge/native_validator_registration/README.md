# Native validator candidate registration proposal

This package contains reviewed first-party templates/overlays and an immutable retained legacy source tree. It does not activate compatibility in the current application. The registrar materializes a **new local source candidate** for later qualification. It never downloads, installs, imports wheel code, starts processes, connects databases or launches tools.

The recipe targets HEAD `3e997f62ee62c0078b759d61c168c6c4d747008a`, its exact 108 application Python files, its exact dependency lock and the five reviewed app overlays. Four changed/added evaluation files are preserved from that HEAD. Any source or lock drift requires a new reviewed recipe. Legacy checker `083e9202...` and current checker `1152215f...` stay distinct. The old retained source namespace is not rewritten.

## Public package versus local output

Public material: this registrar, recipe with relative paths and locked upstream wheel provenance, templates, five first-party source overlays and 124 retained source/support files. The retained PicoRV32 source keeps its original copyright/permission header. No dependency binaries, virtual environment, host interpreter, host absolute paths, recorded evidence, credentials or databases are included.

Local ignored material: a separately generated `.runtime/native-validator/<candidate-id>/` source tree containing the selected target-specific dependency code/binary, licenses/notices, pinned interpreter path/hash, generated runtime manifests/policy and `COMPLETED.json` receipt. The existing qualified source snapshot/database are not inputs to rewrite or outputs to replace. Only source files named in the recipe are copied from the checkout; `.env` and evidence/data directories are never copied.

The initial recipe supports only native CPython 3.12.13 on Windows AMD64, with the five exact wheels from `recipe.json`. This is a target-specific candidate, not a portable runtime claim. Linux/container or other Python versions require a separately reviewed recipe, dependencies, interpreter and target qualification. No root dependency/lock change is proposed.

Registration also requires the running implementation to be CPython and its interpreter-specific `sys.maxsize` to equal `2**63 - 1`. This rejects a 32-bit interpreter on a 64-bit host; machine type alone does not qualify the fixed `cp312-win_amd64` ABI. Python documents interpreter word size through [sys.maxsize](https://docs.python.org/3.12/library/sys.html#sys.maxsize). This source preflight still does not substitute for actual target/archive qualification.

Input files are read through a binary descriptor after checking initial regular-file size. Descriptor identity is compared with the initial path stat before and after a bounded read; final path identity and exact length are rechecked. At most the initial size plus one sentinel byte is requested, always within the caller's limit plus one. Growth is rejected without materializing the growing remainder. These checks do not claim an OS-enforced RAM cap or hostile concurrent-writer sandbox.

## Reproducible registration interface — not executed by this proposal

After separate authorization, obtain the five exact wheel files listed in `recipe.json` through the approved dependency workflow. The registrar performs no network operation. Keep them in a private input directory. The active app dependency directory must byte-match the selected runtime files from those wheels. Archive SHA256, identity/version, target tags, complete SHA256 RECORD inventory, declared license expression/files and extra licenses/notices are checked before output is created. No wheel extraction API or installer is invoked; only validated selected bytes are written.

From an authorized checkout, the intended command is:

```powershell
& <native-cpython-3.12.13> -I -S -B src/veriforge/native_validator_registration/registration.py `
  --source-root <checkout> --host-site <qualified-site-packages> `
  --wheel-dir <private-locked-wheels> `
  --output <checkout>/.runtime/native-validator/<new-candidate-id>
```

Angle-bracket values are explicit operator inputs, not stored/project-controlled execution commands. Output must be a new immediate child of that checkout's ignored runtime directory. Existing output is refused. All verification precedes output creation except final output hashing; a partial output is retained and has no `COMPLETED.json`. Completion records source/runtime/dependency/license hashes, with execution `not_run` and operational acceptance `pending`. A receipt is not a service-start authorization or authenticity signature. The installed public package/recipe are trusted operator code; artifact claims never choose a module, path or checker.

The generated policy retains the accepted eight-second checked operation deadline, four-record native pages, aggregate 17 MiB frame cap, at most one child and `-I -S -B` startup. It pins all current non-core/non-API application Python sources, including merged evaluation additions. It separately verifies the exact retained legacy and private dependency trees. Extra license data are also pinned. Public API/core changes are explicit five-file overlays, not an old-tree replacement. The active application's ten-second UI timeout and v1 pagination behavior remain unchanged.

No package build, real wheel registration, generated-candidate import, worker execution, maximum-size/concurrent request test or API/DB/client qualification was performed for this proposal. Existing accepted revision-2 source evidence does not automatically qualify this registration seam or a new generated runtime. Synchronous filesystem/DB/OS operations are not preempted by the checked deadline. OS trust, host runtime, process ownership, RAM/CPU/concurrency and recovery remain separate gates.

## Packaging and notices

The existing Hatch wheel selection is `packages = ["src/veriforge"]`; this source/assets subtree fits that selection without a new backend, build hook, entry point or root lock. File selection and final wheel/sdist contents still require an explicitly authorized artifact build/inspection. No Docker/compose change or automatic registration is proposed. Existing `.gitignore` ignores `.runtime/`; Docker's source allowlist does not include that output directory.

Hatch documents package-directory selection and VCS-aware file selection at [build configuration](https://hatch.pypa.io/latest/config/build/). The registrar's strict fixed-wheel subset follows the [PyPA wheel format](https://packaging.python.org/en/latest/specifications/binary-distribution-format/) for identity, tags, RECORD and license-file locations; it is not a general wheel installer.

`recipe.json` records four MIT-declared distributions and PSF-2.0-declared typing-extensions, as observed in installed metadata and pinned wheel expectations. Required texts/notices come from the verified archives and are retained locally; no dependency code/binary is vendored into this public proposal. Publication must retain applicable first-party/vendor notices and inspect actual target archives and transitive binary notices. The repository has no root first-party LICENSE file in the inspected inputs; this proposal does not invent one or grant publication rights. Publication remains separately authorized.
