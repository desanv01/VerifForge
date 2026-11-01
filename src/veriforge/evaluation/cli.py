"""Offline command entry points. No provider/tool discovery or dispatch."""
import argparse
import json

from .collection import CollectionRequest, export_collection, read_json, validate_reproduction


def collect_main(argv=None):
    parser = argparse.ArgumentParser(description="Collect existing private evaluation ledgers; never run experiments.")
    parser.add_argument("--request", required=True, help="Frozen collection request JSON (not executable code)")
    parser.add_argument("--root", required=True, help="Trusted private input root")
    parser.add_argument("--output", required=True, help="New private output directory; must not exist")
    args = parser.parse_args(argv)
    try:
        request = CollectionRequest.model_validate(read_json(args.request))
        manifest, sha = export_collection(request, args.root, args.output)
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({"status": "collection_failed", "execution": "not_run",
                          "diagnostic": "invalid_or_changed_input_or_output", "partial_output": "preserve_if_present"}))
        return 1
    print(json.dumps({"status": "collected_not_scored", "execution": "not_run", "manifest_sha256": sha,
                      "disclosure": manifest.disclosure, "files": len(manifest.files) + 1}))
    return 0


def reproduce_main(argv=None):
    parser = argparse.ArgumentParser(description="Verify a release reproduction manifest and retained bytes; no tool execution.")
    parser.add_argument("--root", required=True, help="Private exported collection directory")
    parser.add_argument("--expected-manifest-sha256", required=True, help="Independently retained manifest hash")
    args = parser.parse_args(argv)
    try:
        result = validate_reproduction(args.root, expected_manifest_sha256=args.expected_manifest_sha256)
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({"status": "manifest_validation_failed", "execution": "not_run",
                          "diagnostic": "invalid_or_changed_package"}))
        return 1
    print(json.dumps(result))
    return 0
