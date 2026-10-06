# Local adapter: keep the installed Proxmox VM backup lifecycle in charge.
use strict;
use warnings;
use JSON;
use IO::Handle;
use Fcntl qw(LOCK_EX LOCK_NB);
use Time::HiRes qw(time usleep);
use PVE::Cluster;
use PVE::RPCEnvironment;
use PVE::VZDump;
use PVE::QemuConfig;
use PVE::QemuServer::Helpers;
use PVE::QemuServer::Monitor qw(mon_cmd);
use PVE::QemuServer::QMPHelpers;
use PVE::Storage;
use PVE::ProcFSTools;

open(my $protocol, '>&', \*STDOUT) or die "Cannot preserve protocol output\n";
$protocol->autoflush(1);
open(STDOUT, '>&', \*STDERR) or die "Cannot redirect Proxmox logs\n";
PVE::RPCEnvironment->setup_default_cli_env();
PVE::Cluster::cfs_update();
my $request = decode_json(PVE::Tools::file_get_contents($ARGV[0]));
my ($vmid, $work, $target) = $request->@{qw(vmid work target)};
die "Invalid local native request\n" if $vmid !~ /^\d+$/ || $vmid < 100;
my $lifecycle = {};
sub save_lifecycle {
    my (%changes) = @_;
    $lifecycle = {%$lifecycle, %changes};
    PVE::Tools::file_set_contents("$work/lifecycle.json", encode_json($lifecycle), 0600);
}
if (!$request->{check} && ($ARGV[1] // '') ne 'recover') {
    PVE::Tools::file_set_contents("$work/controller.json", encode_json({pid => $$,
        process_start => '' . PVE::ProcFSTools::read_proc_starttime($$)}), 0600);
}
if (($ARGV[1] // '') eq 'recover') {
    exit if !-e "$work/lifecycle.json";
    my $state = decode_json(PVE::Tools::file_get_contents("$work/lifecycle.json"));
    exit if !$state->{locked};
    open(my $recovery_lock, '>>', '/run/vzdump.lock') or die "Cannot open recovery lock\n";
    flock($recovery_lock, LOCK_EX | LOCK_NB) or die "Another Proxmox backup is running; cleanup deferred\n";
    my $current = PVE::QemuConfig->load_config($vmid);
    exit if !$current->{lock} && !$current->{'special-sections'}->{fleecing};
    die "VM has a different lock; refusing cleanup\n" if ($current->{lock} // '') ne 'backup';
    my $pid = PVE::QemuServer::Helpers::vm_running_locally($vmid);
    die "QEMU session changed; refusing automatic cleanup\n" if $pid && $state->{session} ne "$pid:" . PVE::ProcFSTools::read_proc_starttime($pid);
    if ($pid) {
        my $exports = mon_cmd($vmid, 'query-block-exports');
        for my $export (@$exports) {
            my $owned = $state->{exports}->{$export->{id}};
            die "Foreign export; refusing cleanup\n"
                if !$owned || $owned->{'node-name'} ne $export->{'node-name'} || $export->{type} ne 'nbd';
        }
        my @access = grep { $_->{drv} eq 'snapshot-access' } @{mon_cmd($vmid, 'query-named-block-nodes')};
        my $active = (mon_cmd($vmid, 'query-backup')->{status} // '') eq 'active' || @access;
        die "Unowned active backup; refusing cleanup\n" if $active && ($state->{target} // '') ne "snapshot-access:$target";
        if ($state->{nodes}) {
            my %owned = map { $_ => 1 } @{$state->{nodes}};
            die "Foreign snapshot-access node; refusing cleanup\n" if grep { !$owned{$_->{'node-name'}} } @access;
        }
        # Exported bitmaps must no longer be busy before Proxmox merges/releases them.
        if (@$exports || $state->{nbd_started} || ($state->{nbd_path} && -S $state->{nbd_path})) {
            PVE::QemuServer::QMPHelpers::nbd_stop($vmid);
            my $deadline = time() + 25;
            while (@{mon_cmd($vmid, 'query-block-exports')}) {
                die "NBD exports still active; backup teardown deferred\n" if time() >= $deadline;
                usleep(100_000);
            }
        }
        if ($active) {
            mon_cmd($vmid, 'backup-access-teardown', 'target-id' => "snapshot-access:$target", success => JSON::false);
        }
        PVE::QemuServer::Blockdev::detach_tpm_backup_node($vmid) if $current->{tpmstate0};
    }
    PVE::QemuConfig::cleanup_fleecing_images($vmid, PVE::Storage::config(), sub { warn $_[1]; });
    my $after = PVE::QemuConfig->load_config($vmid);
    die "Fleecing cleanup remains incomplete\n" if $after->{'special-sections'}->{fleecing};
    PVE::QemuConfig->remove_lock($vmid, 'backup');
    exit;
}
die "Native Backup Access API missing; update Proxmox\n" if !PVE::VZDump::QemuServer->can('archive_external');
my $conf = PVE::QemuConfig->load_config($vmid);
die "VM is locked\n" if $conf->{lock};
my $pid = PVE::QemuServer::Helpers::vm_running_locally($vmid);
if ($pid) {
    die "Running QEMU lacks Backup Access API; update/restart it\n"
        if !mon_cmd($vmid, 'query-proxmox-support')->{'backup-access-api'};
    die "Another backup is active\n" if (mon_cmd($vmid, 'query-backup')->{status} // '') eq 'active';
    die "Existing NBD exports; finish the other backup first\n" if @{mon_cmd($vmid, 'query-block-exports')};
}
my $cfg = PVE::Storage::config();
my %storages;
for my $id ($request->{fleecing_storage} ? ($request->{fleecing_storage}) : PVE::Storage::storage_ids($cfg)) {
    my $scfg = PVE::Storage::storage_config($cfg, $id);
    next if $scfg->{type} ne 'lvmthin' || $scfg->{shared} || !$scfg->{content}->{images};
    next if !PVE::Storage::storage_check_enabled($cfg, $id, undef, 1);
    $storages{$id} = $scfg;
}
my %sources;
for my $volume (@{PVE::QemuConfig->get_backup_volumes($conf)}) {
    next if !$volume->{included};
    $sources{$volume->{key}} = $volume->{volume_config}->{file};
}
die "No backupable disks\n" if !keys %sources;
my $session = $pid ? "$pid:" . PVE::ProcFSTools::read_proc_starttime($pid) : 'stopped';
my $info = {sources => \%sources, session => $session,
    host => PVE::Tools::file_get_contents('/etc/machine-id'),
    vm_identity => {smbios1 => $conf->{smbios1}, vmgenid => $conf->{vmgenid}}};
if ($request->{check}) {
    $info->{disk_bytes} = 0;
    for my $volid (values %sources) {
        my ($size) = PVE::Storage::volume_size_info($cfg, $volid, 10);
        die "Cannot determine disk size for $volid\n" if !defined($size) || $size !~ /^\d+$/ || $size <= 0;
        $info->{disk_bytes} += $size;
    }
    my $status = PVE::Storage::storage_info({ids => \%storages}, 'images');
    $info->{storages} = [map { {id => $_, pool => "$storages{$_}->{vgname}/$storages{$_}->{thinpool}"} }
        grep { $status->{$_}->{active} } sort keys %storages];
    print $protocol encode_json($info), "\n";
    exit;
}
my $storage = $request->{fleecing_storage} // '';
my $scfg = $storages{$storage} or die "Temporary backup storage '$storage' is not an enabled local LVM-thin image storage\n";
$info->{fleecing_storage} = $storage;
$info->{pools} = [{pool => "$scfg->{vgname}/$scfg->{thinpool}"}];

package DrasticBackupProvider;
use JSON;
sub provider_name { 'Drastic native backup' }
sub backup_init { return {'archive-name' => 'drastic-native'}; }
sub backup_cleanup { return {stats => {'archive-size' => 0}}; }
sub backup_handle_log_file { }
sub backup_get_mechanism { 'nbd' }
sub exchange {
    my ($self, $message) = @_;
    print {$self->{protocol}} encode_json($message), "\n";
    my $line = <STDIN>;
    die "Drastic controller disconnected\n" if !defined($line);
    my $reply = decode_json($line);
    die ($reply->{error} // "Drastic backup failed\n") if !$reply->{ok};
    return $reply;
}
sub backup_vm_query_incremental {
    my ($self, $id, $devices) = @_;
    my $pid = PVE::QemuServer::Helpers::vm_running_locally($id);
    $self->{info}->{session} = "$pid:" . PVE::ProcFSTools::read_proc_starttime($pid);
    main::save_lifecycle(session => $self->{info}->{session}, phase => 'prepared');
    return $self->exchange({event => 'query', devices => $devices, info => $self->{info}})->{modes};
}
sub backup_vm {
    my ($self, $id, $config, $volumes, $extra) = @_;
    main::save_lifecycle(phase => 'ready');
    $self->exchange({event => 'ready', volumes => $volumes, config => $config,
                     firewall => $extra->{'firewall-config'}, info => $self->{info}});
}

package DrasticQemuBackup;
our @ISA = ('PVE::VZDump::QemuServer'); # already loaded by PVE::VZDump
sub lock_vm {
    my ($self, $id) = @_;
    $self->SUPER::lock_vm($id);
    my $pid = PVE::QemuServer::Helpers::vm_running_locally($id);
    my $session = $pid ? "$pid:" . PVE::ProcFSTools::read_proc_starttime($pid) : 'stopped';
    main::save_lifecycle(locked => 1, session => $session, phase => 'locked');
}

package main;
# This provider is process-local; it adds no permanent Proxmox storage/plugin.
my $dump = PVE::VZDump->new('Drastic native backup', {
    dumpdir => $work, tmpdir => $work, mode => 'snapshot',
    remove => 0, 'prune-backups' => {'keep-all' => 1},
    compress => 0, fleecing => {enabled => 1, storage => $request->{fleecing_storage}},
    script => '', 'notes-template' => '', protected => 0,
}, []);
$dump->{'backup-provider'} = bless {protocol => $protocol, info => $info, work => $work}, 'DrasticBackupProvider';
# archive_external uses this as the bitmap target ID; PVE retention/notes are disabled.
$dump->{opts}->{storage} = $target;
my ($plugin) = grep { $_->type() eq 'qemu' } @{$dump->{plugins}};
die "Proxmox QEMU backup plugin missing\n" if !$plugin;
bless $plugin, 'DrasticQemuBackup';
open(my $global_lock, '>>', '/run/vzdump.lock') or die "Cannot open backup lock\n";
flock($global_lock, LOCK_EX | LOCK_NB) or die "Another Proxmox backup is running\n";
my $task = {vmid => int($vmid), plugin => $plugin, mode => 'snapshot', state => 'todo'};
# Record intent before the native RPC, including its uncertain-outcome window.
my $native_mon_cmd = \&PVE::VZDump::QemuServer::mon_cmd;
{
    no warnings 'redefine';
    local *PVE::VZDump::QemuServer::mon_cmd = sub {
        my ($id, $command, %args) = @_;
        if ($command eq 'backup-access-setup') {
            save_lifecycle(phase => 'setup_requested', target => $args{'target-id'});
        } elsif ($command eq 'block-export-add') {
            $lifecycle->{exports}->{$args{id}} = {'node-name' => $args{'node-name'}};
            save_lifecycle();
        } elsif ($command eq 'nbd-server-start') {
            save_lifecycle(nbd_path => $args{addr}->{data}->{path});
        }
        my $result = $native_mon_cmd->(@_);
        if ($command eq 'backup-access-setup') {
            save_lifecycle(phase => 'access_ready', nodes => [map { $_->{'node-name'} } @$result]);
        } elsif ($command eq 'nbd-server-start') {
            save_lifecycle(nbd_started => 1);
        } elsif ($command eq 'backup-access-teardown') {
            save_lifecycle(phase => 'torn_down');
        }
        return $result;
    };
    $dump->exec_backup_task($task);
}
die ($task->{msg} // "Native backup failed\n") if $task->{state} ne 'ok';
my $after = PVE::QemuConfig->load_config($vmid);
die "Native cleanup incomplete\n" if $after->{lock} || $after->{'special-sections'}->{fleecing};
print $protocol encode_json({event => 'done'}), "\n";
