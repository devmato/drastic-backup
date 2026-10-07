"""Read Proxmox-owned LVM-thin snapshots and stream a deterministic, ordinary TAR."""

import io
import json
import sys
import tarfile
from pathlib import Path

from drastic_common.process import run_process

# Use the installed Proxmox storage implementation, not guessed /dev paths.
SNAPSHOT_INFO = r'''
use strict;
use warnings;
use JSON;
use PVE::Cluster;
use PVE::QemuConfig;
use PVE::QemuServer;
use PVE::RPCEnvironment;
use PVE::Storage;
PVE::RPCEnvironment->setup_default_cli_env();
PVE::Cluster::cfs_update();
my ($vmid, $name, $owner, $mode) = @ARGV;
open(my $result, '>&', \*STDOUT) or die "Cannot preserve stdout\n";
open(STDOUT, '>', '/dev/null') or die "Cannot redirect tool output\n";
my $current = PVE::QemuConfig->load_config($vmid);
my $conf = $name ? $current->{snapshots}->{$name} : $current;
if ($mode eq 'check') {
    print $result encode_json({exists => $conf ? JSON::true : JSON::false,
        description => $conf ? $conf->{description} : '', lock => $current->{lock}});
    exit;
}
die "VM configuration is locked\n" if $current->{lock};
die "Snapshot missing or ownership mismatch\n"
    if !$conf || ($name && ($conf->{description} // '') ne $owner);
die "Snapshot did not complete\n" if $conf->{snapstate};
my $cfg = PVE::Storage::config();
die "VM storage does not support snapshots\n"
    if !PVE::QemuConfig->has_feature('snapshot', $conf, $cfg);
my @volumes;
for my $item (@{PVE::QemuConfig->get_backup_volumes($conf)}) {
    next if !$item->{included};
    my $disk = $item->{key};
    die "Unsupported disk $disk\n" if $disk !~ /^(?:(?:ide|sata|scsi|virtio)\d+|efidisk0|tpmstate0)$/;
    my $volid = $item->{volume_config}->{file};
    my ($storeid) = PVE::Storage::parse_volume_id($volid);
    my $scfg = PVE::Storage::storage_config($cfg, $storeid);
    die "Disk $disk requires LVM-thin storage\n" if $scfg->{type} ne 'lvmthin';
    my $entry = {disk => $disk, volume => $volid, pool => "$scfg->{vgname}/$scfg->{thinpool}"};
    if ($name) {
        PVE::Storage::activate_volumes($cfg, [$volid], $name);
        my $path = PVE::Storage::path($cfg, $volid, $name);
        die "Snapshot is not a block device\n" if !-b $path;
        my $size;
        PVE::Tools::run_command(['/sbin/blockdev', '--getsize64', $path],
            outfunc => sub { $size = int(shift); });
        die "Invalid disk size\n" if !$size;
        $entry->{path} = $path;
        $entry->{size} = $size;
    } else {
        # Progress metadata is optional; do not prevent a backup if size discovery fails.
        my ($size) = eval { PVE::Storage::volume_size_info($cfg, $volid, 10) };
        $entry->{size} = int($size) if defined($size) && $size =~ /^\d+$/ && $size > 0;
    }
    push @volumes, $entry;
}
die "VM has no backupable volumes\n" if !@volumes;
my %saved = %$conf;
delete @saved{qw(snapshots pending special-sections lock parent snaptime snapstate vmstate digest)};
delete $saved{$_} for grep { /^unused\d+$/ } keys %saved;
$saved{description} = $current->{description} if $name;
print $result encode_json({config => PVE::QemuServer::write_vm_config("qemu-server/$vmid.conf", \%saved),
    volumes => \@volumes});
'''


def snapshot_info(vmid, name="", owner="", *, check=False):
    return json.loads(run_process(
        ["perl", "-e", SNAPSHOT_INFO, str(vmid), name, owner, "check" if check else "read"],
        timeout=60,
    ))


def write_archive(plan, output):
    """Headers and disk order stay stable; tarfile detects short source reads."""
    with tarfile.open(fileobj=output, mode="w|", format=tarfile.PAX_FORMAT,
                      bufsize=1024 * 1024, copybufsize=1024 * 1024) as archive:
        # Keep variable metadata after disk data so it cannot perturb its chunks.
        for volume in sorted(plan["volumes"], key=lambda item: item["disk"]):
            info = tarfile.TarInfo(f"disks/disk-drive-{volume['disk']}.raw")
            info.size = volume["size"]
            info.mode = 0o600
            with open(volume["path"], "rb", buffering=0) as source:
                archive.addfile(info, source)
        configs = {"qemu-server.conf": plan["config"]}
        if plan.get("firewall") is not None:
            configs["qemu-server.fw"] = plan["firewall"]
        manifest = {"version": 1, "vmid": plan["vmid"], "volumes": [
            {"disk": item["disk"], "size": item["size"]} for item in plan["volumes"]
        ]}
        configs["manifest.json"] = json.dumps(manifest, sort_keys=True)
        for name, text in sorted(configs.items()):
            data = text.encode()
            if len(data) > 65535:
                raise ValueError(f"Snapshot metadata {name} is too large")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(data))


if __name__ == "__main__":
    write_archive(json.loads(Path(sys.argv[1]).read_text()), sys.stdout.buffer)
