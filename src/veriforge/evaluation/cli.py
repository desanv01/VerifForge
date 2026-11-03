"""Offline command entry points. No provider/tool discovery or dispatch."""
import argparse
import json


def freeze_corpus_main(argv=None):
    """Freeze installed case inputs, keeping trusted and planner files distinct."""
    from pathlib import Path
    import hashlib
    from veriforge.contracts import canonical_hash
    from .family_corpus import build_corpus, public_cases, validate_corpus

    parser = argparse.ArgumentParser(description='Freeze private three-family inputs; never execute or score experiments.')
    parser.add_argument('--output', required=True, help='New private directory; must not exist')
    args = parser.parse_args(argv)
    try:
        corpus = validate_corpus(build_corpus())
        private = json.dumps(corpus.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        public = json.dumps([row.model_dump(mode='json') for row in public_cases(corpus)],
                            sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        digest = hashlib.sha256(private).hexdigest()
        if digest != canonical_hash(corpus):
            raise ValueError('corpus_serialization_identity_changed')
        manifest = {'schema_version': 'evaluation.corpus-export.v1', 'execution': 'not_run',
            'disclosure': 'trusted_private_inputs_not_results', 'case_count': len(corpus.cases),
            'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                      for name, data in [('corpus.private.json', private), ('planner-cases.json', public)]}}
        output = Path(args.output)
        output.mkdir(exist_ok=False)
        for name, data in [('corpus.private.json', private), ('planner-cases.json', public),
                           ('manifest.json', json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode())]:
            with (output/name).open('xb') as stream:
                stream.write(data)
        print(json.dumps({'status': 'inputs_frozen_not_evaluated', 'execution': 'not_run',
            'corpus_sha256': digest, 'cases': len(corpus.cases), 'disclosure': 'keep_trusted_manifest_private'}))
        return 0
    except (OSError, ValueError, TypeError):
        print(json.dumps({'status': 'corpus_freeze_failed', 'execution': 'not_run',
            'diagnostic': 'invalid_or_changed_input_or_output', 'partial_output': 'preserve_if_present'}))
        return 1

from .collection import CollectionRequest, export_collection, parse_request, read_json, validate_reproduction


def collect_main(argv=None):
    parser = argparse.ArgumentParser(description="Collect existing private evaluation ledgers; never run experiments.")
    parser.add_argument("--request", required=True, help="Frozen collection request JSON (not executable code)")
    parser.add_argument("--root", required=True, help="Trusted private input root")
    parser.add_argument("--output", required=True, help="New private output directory; must not exist")
    args = parser.parse_args(argv)
    try:
        request = parse_request(read_json(args.request))
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
