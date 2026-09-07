"""Application operation shared by the CLI and REST worker."""

from . import archive, collector, policies


def evaluate_snapshot(store: archive.Archive, snapshot_id: str, minimum_gain: float | None = None) -> dict:
    cfg, state = collector.replay(store, snapshot_id)
    challengers = [] if minimum_gain is None else [policies.PickupFloor(minimum_gain)]
    report = policies.compare(cfg, state, challengers)
    fingerprint = collector.engine_fingerprint()
    manifest = store.manifest(snapshot_id)
    report["engine_fingerprint"] = fingerprint
    report["capture_engine_fingerprint"] = manifest["engine_fingerprint"]
    report["same_engine_as_capture"] = fingerprint == manifest["engine_fingerprint"]
    evaluation_id = store.evaluate(snapshot_id, report, fingerprint)
    return {"evaluation_id": evaluation_id, "report": report}
