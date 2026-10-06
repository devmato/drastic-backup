# Minimal local stand-in for the PVE calls used by the real recovery adapter.
use strict;
use warnings;
use JSON::PP ();
BEGIN {
    $INC{'JSON.pm'} = __FILE__;
    $INC{"PVE/$_.pm"} = __FILE__ for qw(Cluster RPCEnvironment VZDump QemuConfig
        QemuServer/Helpers QemuServer/Monitor QemuServer/QMPHelpers Storage ProcFSTools);
}
package JSON;
sub import {
    no strict 'refs';
    my $caller = caller;
    *{"${caller}::encode_json"} = \&JSON::PP::encode_json;
    *{"${caller}::decode_json"} = \&JSON::PP::decode_json;
}
sub false { JSON::PP::false() }
package RecoveryMock;
sub load {
    open(my $file, '<', $ENV{RECOVERY_STATE}) or die $!;
    local $/;
    return JSON::PP::decode_json(<$file>);
}
sub store {
    open(my $file, '>', $ENV{RECOVERY_STATE}) or die $!;
    print $file JSON::PP::encode_json($_[0]);
}
sub call {
    my ($command, %args) = @_;
    my $state = load();
    push @{$state->{calls}}, $command;
    my $result;
    if ($command eq 'query-block-exports') {
        $result = $state->{exports};
        if ($state->{stopping}) {
            $state->{exports} = [];
            $state->{stopping} = 0;
        }
    } elsif ($command eq 'query-named-block-nodes') {
        $result = $state->{nodes};
    } elsif ($command eq 'query-backup') {
        $result = {status => $state->{active} ? 'active' : 'done'};
    } elsif ($command eq 'nbd-server-stop') {
        die 'Injected NBD stop failure' if $state->{stop_error};
        $state->{stopping} = 1; # exports disappear after a subsequent query
    } elsif ($command eq 'backup-access-teardown') {
        die 'Busy exported bitmap: QEMU assertion' if @{$state->{exports}};
        die 'Cached target mismatch' if $args{'target-id'} ne $state->{target};
        $state->{nodes} = [];
        $state->{active} = 0;
    } elsif ($command eq 'cleanup-fleecing') {
        delete $state->{config}->{'special-sections'}->{fleecing};
    } elsif ($command eq 'unlock') {
        delete $state->{config}->{lock};
    }
    store($state);
    return $result;
}
package PVE::Cluster;
sub cfs_update { }
package PVE::RPCEnvironment;
sub setup_default_cli_env { }
package PVE::Tools;
sub file_get_contents { open(my $f, '<', $_[0]) or die $!; local $/; return <$f>; }
package PVE::QemuConfig;
sub load_config { RecoveryMock::load()->{config} }
sub cleanup_fleecing_images { RecoveryMock::call('cleanup-fleecing'); }
sub remove_lock { RecoveryMock::call('unlock'); }
package PVE::QemuServer::Helpers;
sub vm_running_locally { 42 }
package PVE::ProcFSTools;
sub read_proc_starttime { 7 }
package PVE::Storage;
sub config { {} }
package PVE::QemuServer::Monitor;
sub import { no strict 'refs'; *{caller() . '::mon_cmd'} = \&mon_cmd; }
sub mon_cmd { shift; RecoveryMock::call(@_); }
package PVE::QemuServer::QMPHelpers;
sub nbd_stop { RecoveryMock::call('nbd-server-stop'); }
1;
