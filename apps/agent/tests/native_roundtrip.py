"""Opt-in native round trip, including crash recovery, on OWN disposable VMs only."""
import hashlib
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

BASE = Path(os.environ['NATIVE_EVAL_ROOT'])
ROOT = BASE / ('attempt-' + uuid4().hex[:8])
ROOT.mkdir()
os.environ['DRASTIC_AGENT_DATA_DIR'] = str(ROOT / 'agent-data')
VMID = 990103
DESTINATION = 990104
NAME = 'drastic-native-build-test'


def run(*args):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=90).stdout


resources=json.loads(run('pvesh','get','/cluster/resources','--type','vm','--output-format','json'))
assert not any(item['vmid'] in (VMID,DESTINATION) for item in resources), 'Test VMID occupied'
try:
    run('qm','create',str(VMID),'--name',NAME,'--memory','256','--cores','1','--bios','ovmf',
        '--machine','q35','--scsihw','virtio-scsi-single','--scsi0','local-lvm:0.032',
        '--efidisk0','local-lvm:0,efitype=4m,pre-enrolled-keys=1','--tpmstate0','local-lvm:0,version=v2.0')
    run('qm','start',str(VMID))
    from drastic_agent.agent.report import AgentReport
    from drastic_agent.jobs.proxmox_backup import ProxmoxBackupJobHandler
    from drastic_agent.jobs.proxmox_native import run_native
    from drastic_agent.services.guest_linux import LinuxGuestFileBackend
    from drastic_agent.services.proxmox_restore import run_proxmox_restore
    from drastic_agent.storage.database import proxmox_checkpoints, proxmox_native_runs
    from drastic_common.restic.client import ResticApi
    from drastic_common.restic.repository import ResticRepository
    api=ResticApi(str(BASE/'restic'),repository=ResticRepository(location=str(ROOT/'repo'),password='isolated-native-evaluation'))
    api.init()
    agent=SimpleNamespace(identifier='native-evaluation',resticapi=api)
    job={'id':1,'uuid':str(uuid4()),'config':{'backup_mode':'native_cbt','fleecing_storage':''}}
    handler=ProxmoxBackupJobHandler(agent,job,1)
    for operation_id,stage in enumerate(('initial','unchanged','changed','failed','retry','native-full','cbt-reseed','bitmap-lost',
                                       'crash','crash-reseed','helper-crash','helper-reseed',
                                       'pre-setup-crash','pre-setup-reseed','unchanged-final'),1):
        job['config']['backup_mode'] = 'native' if stage=='native-full' else 'native_cbt'
        if stage in ('changed','failed'):
            code='use JSON;use PVE::QemuServer::Monitor; print encode_json(PVE::QemuServer::Monitor::mon_cmd(990103,"human-monitor-command","command-line"=>q(qemu-io -d scsi0 "write -P 0xab 16777216 4096")));'
            output=run('perl','-e',code)
            print('Synthetic test write:',output,flush=True)
            assert not any(word in output.lower() for word in ('error','not found','invalid','conflict')),output
        if stage=='bitmap-lost':
            code='use JSON;use PVE::QemuServer::Monitor;my $n=PVE::QemuServer::Monitor::mon_cmd(990103,"query-named-block-nodes");for my $x (@$n){for my $b (@{$x->{"dirty-bitmaps"}//[]}){if(($b->{name}//"")=~/^snapshot-access:drastic-/){PVE::QemuServer::Monitor::mon_cmd(990103,"block-dirty-bitmap-remove",node=>$x->{"node-name"},name=>$b->{name});}}}'
            run('perl','-e',code)
        report=AgentReport.command_report()
        handler.operation={'id':operation_id,'uuid':report.uuid}
        report.set_data('guests',[VMID])
        report.set_data('backup_items_total',1)
        if stage in ('crash','helper-crash','pre-setup-crash'):
            marker = ROOT / 'crash-ready'
            marker.unlink(missing_ok=True)
            before_pid = json.loads(run('pvesh','get',f'/nodes/pve/qemu/{VMID}/status/current','--output-format','json'))['pid']
            child_job = {**job, 'uuid': str(uuid4())} if stage == 'pre-setup-crash' else job
            child_code='''import json,os,time,subprocess
from pathlib import Path
from types import SimpleNamespace
from drastic_agent.agent.report import AgentReport
from drastic_agent.jobs.proxmox_backup import ProxmoxBackupJobHandler
from drastic_agent.jobs.proxmox_native import run_native
import drastic_agent.jobs.proxmox_native as native
from drastic_common.restic.client import ResticApi
from drastic_common.restic.repository import ResticRepository
root=Path(os.environ["CRASH_ROOT"])
api=ResticApi(os.environ["CRASH_RESTIC"],repository=ResticRepository(location=str(root/"repo"),password="isolated-native-evaluation"))
def wait(**kwargs):
 (root/"crash-ready").write_text("mounted")
 time.sleep(120)
api.backup=wait
if os.environ["CRASH_POINT"]=="pre-setup-crash":
 original=subprocess.Popen
 class QueryBarrier:
  def __init__(self,stream): self.stream=stream
  def fileno(self): return self.stream.fileno()
  def readline(self):
   line=self.stream.readline()
   if line and json.loads(line).get("event")=="query":
    (root/"crash-ready").write_text("pre-setup")
    time.sleep(120)
   return line
 def launch(command,*args,**kwargs):
  process=original(command,*args,**kwargs)
  if command[0]=="perl" and kwargs.get("stdin")==subprocess.PIPE:
   process.stdout=QueryBarrier(process.stdout)
  return process
 native.subprocess.Popen=launch
handler=ProxmoxBackupJobHandler(SimpleNamespace(identifier="native-evaluation",resticapi=api),json.loads(os.environ["CRASH_JOB"]),1)
report=AgentReport.command_report();handler.operation={"id":99,"uuid":report.uuid};report.set_data("guests",[990103]);report.set_data("backup_items_total",1)
run_native(handler,report,{"vmid":990103,"name":"drastic-native-build-test"},1)
'''
            child=subprocess.Popen([os.sys.executable,'-c',child_code],env={**os.environ,'CRASH_ROOT':str(ROOT),
                'CRASH_RESTIC':str(BASE/'restic'),'CRASH_JOB':json.dumps(child_job),'CRASH_POINT':stage})
            try:
                for _ in range(100):
                    if (ROOT/'crash-ready').exists():
                        break
                    if child.poll() is not None:
                        raise AssertionError('Crash worker failed before mount')
                    time.sleep(.2)
                assert (ROOT/'crash-ready').exists()
                if stage != 'crash':
                    row = proxmox_native_runs.find_one()
                    state = json.loads((Path(row['data']['work'])/'lifecycle.json').read_text())
                    assert state['phase'] == ('prepared' if stage == 'pre-setup-crash' else 'ready')
                    os.kill(row['data']['pid'], signal.SIGKILL)
                child.kill()
                child.wait(timeout=10)
                time.sleep(2)
                from drastic_agent.jobs.proxmox_native import recover_runs
                recover_runs()
                assert not proxmox_native_runs.count()
                assert any(not row['data'].get('valid') for row in proxmox_checkpoints.all())
                after = json.loads(run('pvesh','get',f'/nodes/pve/qemu/{VMID}/status/current','--output-format','json'))
                assert after['status']=='running' and after['pid']==before_pid
                assert 'lock: backup' not in run('qm','config',str(VMID))
                print(f'PASS: {stage} recovery keeps QEMU alive and removes own resources',flush=True)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
            continue
        if stage=='failed':
            from drastic_common.restic.exceptions import ResticFailedError
            original=api.backup
            def fail(**kwargs):
                raise ResticFailedError('injected isolated upload failure')
            api.backup=fail
            try:
                try:
                    run_native(handler,report,{'vmid':VMID,'name':NAME},1)
                except ResticFailedError:
                    pass
                else:
                    raise AssertionError('Injected upload failure was ignored')
            finally:
                api.backup=original
            assert not proxmox_native_runs.count()
            assert proxmox_checkpoints.find_one()['data']['valid'] is False
            print('PASS: failed upload leaves checkpoint invalid and resources cleaned',flush=True)
            continue
        run_native(handler,report,{'vmid':VMID,'name':NAME},1)
        assert not proxmox_native_runs.count()
        artifact=report.artifacts[0]
        print(json.dumps({'stage':stage,'artifact':artifact}),flush=True)
        assert artifact['state']=='success'
        assert proxmox_checkpoints.find_one()['data']['valid'] is (stage!='native-full')
        if stage in ('unchanged','changed','unchanged-final'):
            assert artifact['data']['bytes_read'] < artifact['data']['disk_bytes']
            if stage=='changed':
                assert artifact['data']['bytes_read'] >= 4*1024*1024+4734976
        elif stage != 'pre-setup-reseed':
            assert artifact['data']['bytes_read'] == artifact['data']['disk_bytes']
    snapshot_id=artifact['snapshot_id']
    old=[s['id'] for s in api.snapshots() if s['id']!=snapshot_id]
    api.forget_snapshots(old,prune=True)
    snapshot=api.snapshots()[0]
    work=ROOT/'raw-roundtrip'
    work.mkdir()
    with LinuxGuestFileBackend().disk_view(api, {'format':'native','vmid':VMID,'snapshot_id':snapshot_id}, work,
                                          cancelled=lambda:False, timeout=120) as disks:
        expected={}
        for disk in disks:
            with (work/'disks'/f'disk-drive-{disk}.raw').open('rb') as source:
                expected[disk]=(disks[disk],hashlib.file_digest(source,'sha256').hexdigest())
    print(json.dumps({'latest_only_raw_restore_sha256':expected}),flush=True)
    os.environ['DRASTIC_RESTORE_WORK_DIR']=str(ROOT/'restore-work')
    report=AgentReport.command_report()
    run_proxmox_restore(agent,report,snapshot,mode='proxmox_vm',identity={'job_uuid':job['uuid'],'snapshot_id':snapshot_id},
                        vmid=DESTINATION,storage='local-lvm',unique=True)
    print('PASS: native full/CBT + independent latest snapshot + real native VM import',flush=True)
    assert 'stopped' in run('qm','status',str(DESTINATION))
    restored_config=run('qm','config',str(DESTINATION))
    assert all(f'{disk}:' in restored_config for disk in expected), 'Missing restored disk/config mapping'
    for disk,(size,checksum) in expected.items():
        volid=re.search(rf'^{disk}: ([^,\n]+)', restored_config, re.MULTILINE)[1]
        path=run('pvesm','path',volid).strip()
        with open(path,'rb') as target:
            restored=hashlib.sha256()
            for offset in range(0,size,1024*1024):
                restored.update(target.read(min(1024*1024,size-offset)))
            assert restored.hexdigest()==checksum, f'Restored disk mismatch: {disk}'
    api.check(read_data=True)
finally:
    for vmid in (DESTINATION,VMID):
        result=subprocess.run(['qm','config',str(vmid)],capture_output=True,text=True)
        if result.returncode==0 and (f'name: {NAME}' in result.stdout or not result.stdout.strip()):
            subprocess.run(['qm','stop',str(vmid)],capture_output=True,timeout=60)
            subprocess.run(['qm','destroy',str(vmid),'--purge','1'],check=True,capture_output=True,timeout=60)
            print('Removed OWN test VM:',vmid,flush=True)
