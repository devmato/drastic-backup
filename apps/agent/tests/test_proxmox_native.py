import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from drastic_agent.proxmox_blocks import (
    BLOCK_SIZE,
    block_path,
    dirty_blocks,
    make_manifest,
    validate_manifest,
)
from drastic_agent.services.proxmox_restore import restore_native_blocks
from drastic_common.restic.client import ResticApi
from drastic_common.restic.repository import ResticRepository


@pytest.mark.parametrize("scenario", ["exported-bitmap", "pre-setup", "foreign-export", "stop-failure"])
def test_recovery_adapter_orders_cleanup_and_handles_previous_target(tmp_path, scenario):
    if not shutil.which("perl"):
        pytest.skip("Perl is required for the real adapter recovery check")
    adapter = Path(__file__).parents[1] / "src/drastic_agent/proxmox_native.pl"
    source = adapter.read_text().replace("'/run/vzdump.lock'", repr(str(tmp_path / "vzdump.lock")))
    (tmp_path / "adapter.pl").write_text(source)
    lifecycle = {"locked": 1, "session": "42:7", "phase": "prepared"}
    export = {"id": "drive-scsi0", "node-name": "owned-snapshot", "type": "nbd"}
    active = scenario != "pre-setup"
    if active:
        lifecycle.update(phase="ready", target="snapshot-access:job-b", nodes=["owned-snapshot"],
                         exports={"drive-scsi0": {"node-name": "owned-snapshot"}}, nbd_started=True)
    (tmp_path / "lifecycle.json").write_text(json.dumps(lifecycle))
    (tmp_path / "request.json").write_text(json.dumps({"vmid": 103, "work": str(tmp_path), "target": "job-b"}))
    state = {"active": active, "target": "snapshot-access:job-b" if active else "snapshot-access:job-a",
             "exports": [export] if active else [], "calls": [], "stop_error": scenario == "stop-failure",
             "nodes": [{"node-name": "owned-snapshot", "drv": "snapshot-access"}] if active else [],
             "config": {"lock": "backup", "special-sections": {"fleecing": {"images": "owned"}}}}
    if scenario == "foreign-export":
        state["exports"][0] = {**export, "node-name": "foreign-snapshot"}
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state))
    stub = Path(__file__).with_name("native_recovery_stub.pm")
    result = subprocess.run(["perl", "-e", 'require $ARGV[0]; shift @ARGV; my $file = shift @ARGV; do $file; die $@ if $@;',
                             str(stub), str(tmp_path / "adapter.pl"), str(tmp_path / "request.json"), "recover"],
                            env={**os.environ, "RECOVERY_STATE": str(state_path)}, capture_output=True, text=True, timeout=10)
    after = json.loads(state_path.read_text())
    calls = after["calls"]
    if scenario in ("foreign-export", "stop-failure"):
        assert result.returncode != 0
        assert "backup-access-teardown" not in calls and "unlock" not in calls
    else:
        assert result.returncode == 0, result.stderr
        assert not after["config"].get("lock")
        if active:
            assert calls.index("nbd-server-stop") < calls.index("backup-access-teardown") < calls.index("cleanup-fleecing")
            assert calls[calls.index("backup-access-teardown") - 1] == "query-block-exports"
        else:
            assert "backup-access-teardown" not in calls and "nbd-server-stop" not in calls


def test_generations_reuse_only_confirmed_dirty_blocks_and_handle_reset_and_tail():
    volume = {"disk": "scsi0", "size": 2 * BLOCK_SIZE + 512, "dirty": {0, 1, 2}, "bitmap-mode": "new"}
    first, count = make_manifest(103, [volume])
    assert count == volume["size"]
    changed, count = make_manifest(103, [{**volume, "dirty": {1}, "bitmap-mode": "reuse"}], first)
    assert count == BLOCK_SIZE
    assert changed["volumes"][0]["generations"][::2] == first["volumes"][0]["generations"][::2]
    assert changed["volumes"][0]["generations"][1] != first["volumes"][0]["generations"][1]
    unchanged, count = make_manifest(103, [{**volume, "dirty": set(), "bitmap-mode": "reuse"}], changed)
    assert count == 0 and unchanged["metadata_generation"] > changed["metadata_generation"]
    reset, count = make_manifest(103, [volume], changed)
    assert count == volume["size"] and reset["volumes"][0]["generations"] != changed["volumes"][0]["generations"]
    with pytest.raises(ValueError):
        validate_manifest({**first, "volumes": [{**first["volumes"][0], "disk": "../escape"}]})


def test_bitmap_extent_validation_does_not_treat_missing_maps_as_clean():
    handle = SimpleNamespace(block_status=lambda size, offset, callback: callback(
        "qemu:dirty-bitmap:own", offset, [BLOCK_SIZE, 0, BLOCK_SIZE, 1], None))
    assert dirty_blocks(handle, 2 * BLOCK_SIZE, "own") == {1}
    handle.block_status = lambda size, offset, callback: None
    with pytest.raises(ValueError, match="Incomplete"):
        dirty_blocks(handle, 2 * BLOCK_SIZE, "own")


def test_native_layout_restores_latest_after_prune_and_rejects_missing_blocks(tmp_path):
    binary = shutil.which("restic") or str(Path(__file__).parents[1] / "data/bin/restic_0.18.1_linux_amd64")
    if not Path(binary).is_file():
        pytest.skip("Install restic for the native layout round trip")
    source = tmp_path / "source"
    source.mkdir()
    data = b"a" * BLOCK_SIZE + b"b" * 512
    manifest, _ = make_manifest(103, [{"disk": "scsi0", "size": len(data), "dirty": {0, 1}, "bitmap-mode": "new"}])
    (source / "manifest.json").write_text(json.dumps(manifest))
    (source / "qemu-server.conf").write_text("memory: 256\n")
    for index in range(2):
        path = source / block_path("scsi0", index)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data[index * BLOCK_SIZE:(index + 1) * BLOCK_SIZE])
    api = ResticApi(binary, repository=ResticRepository(location=str(tmp_path / "repo"), password="native-test"))
    api.init()
    tags = ["vmid:103", "source:proxmox", "guest_type:qemu", "backup_method:native"]
    first = api.backup(["."], cwd=str(source), tags=tags)
    second = api.backup(["."], cwd=str(source), parent=first["snapshot_id"], tags=tags)
    assert second["data_added"] == 0
    api.forget_snapshots([first["snapshot_id"]], prune=True)
    target = tmp_path / "restored"
    target.mkdir()
    report = SimpleNamespace(uuid="test")
    assert restore_native_blocks(SimpleNamespace(resticapi=api), report, api.snapshots()[0], target, lambda: False) == len(data)
    assert (target / "disks/disk-drive-scsi0.raw").read_bytes() == data
    (source / block_path("scsi0", 1)).unlink()
    broken = api.backup(["."], cwd=str(source), tags=tags)
    target = tmp_path / "broken"
    target.mkdir()
    snapshot = next(s for s in api.snapshots() if s["id"] == broken["snapshot_id"])
    with pytest.raises(ValueError, match="missing or invalid disk block"):
        restore_native_blocks(SimpleNamespace(resticapi=api), report, snapshot, target, lambda: False)


@pytest.mark.skipif(os.environ.get("DRASTIC_TEST_NATIVE_ROUNDTRIP") != "1", reason="Explicit Proxmox test-host opt-in required")
def test_real_native_proxmox_roundtrip(tmp_path):
    binary = shutil.which("restic")
    assert binary and os.geteuid() == 0, "Run on a Proxmox test host as root with restic installed"
    shutil.copy2(binary, tmp_path / "restic")
    subprocess.run([os.sys.executable, str(Path(__file__).with_name("native_roundtrip.py"))],
                   env={**os.environ, "NATIVE_EVAL_ROOT": str(tmp_path)}, check=True, timeout=600)
