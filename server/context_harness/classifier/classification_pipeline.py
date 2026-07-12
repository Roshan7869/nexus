"""Higher-level classification pipeline with bundle assignment.

P0-S3 extension: Takes raw classifications and assigns each resource
to its canonical bundle for downstream routing.
"""

from server.context_harness.classifier import Classifier, Classification


def classify_and_assign_bundles(
    db_path: str,
    bundle_registry,
) -> tuple[dict[int, Classification], dict[int, str]]:
    classifier = Classifier(db_path=db_path)
    classifications = classifier.classify_all()
    canonical_bundle_map: dict[int, str] = {}
    for rid in classifications:
        canonical_bundle_map[rid] = bundle_registry.get_canonical_bundle(rid)
    return classifications, canonical_bundle_map
