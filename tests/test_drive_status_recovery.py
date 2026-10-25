"""Keep real journal stage eligibility through Status into the shipped UI model."""
import json,pathlib,subprocess,types
import pytest
from helper import provisioning as p

ROOT=pathlib.Path(__file__).resolve().parents[1]
STAGES=('planned','partitioning','encrypting','encrypted','formatted','configuring','finalizing','unknown')


def action(row):
    # Execute the actual model against inspect's actual JSON, not a test copy.
    script="const fs=require('node:fs'),vm=require('node:vm'),c=vm.createContext({});vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);const row=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(c.driveFix(row,[],[]).action);"
    result=subprocess.run(['node','-e',script,str(ROOT/'Model.js')],input=json.dumps(row).encode(),capture_output=True,timeout=5,check=True)
    return result.stdout.decode()


@pytest.mark.parametrize('kind',('legacy-manual','secret-only','auto'))
@pytest.mark.parametrize('stage',STAGES)
def test_abrupt_stage_survives_inspect_and_reaches_real_model(kind,stage):
    journal={'id':'a'*32,'state':stage,'autoUnlock':kind=='auto','manualSecretOnly':kind=='secret-only'}
    original=dict(journal)
    row=p.inspect(types.SimpleNamespace(records=lambda kind:[journal]))[0]
    assert journal==original, 'read-only Status must not rewrite the journal'
    assert row['state']=='unfinished'
    assert row.get('interruptedState')==stage
    finishable=stage in ('formatted','configuring','finalizing')
    expected='resume_drive' if kind=='auto' and finishable else 'recover' if kind!='auto' and (finishable or kind=='secret-only' and stage in ('encrypting','encrypted')) else ''
    assert action(row)==expected


def test_current_direct_stage_overrides_stale_interrupted_stage_without_mutation():
    journal={'state':'formatted','interruptedState':'partitioning','autoUnlock':False}
    row=p.inspect(types.SimpleNamespace(records=lambda kind:[journal]))[0]
    assert row['interruptedState']=='formatted' and action(row)=='recover'
    assert journal['interruptedState']=='partitioning'


def test_existing_unfinished_stage_and_ambiguous_record_remain_distinct():
    for journal,expected in [({'state':'unfinished','interruptedState':'formatted','autoUnlock':False},'recover'),({'state':'unfinished','autoUnlock':False},'')]:
        original=dict(journal)
        row=p.inspect(types.SimpleNamespace(records=lambda kind:[journal]))[0]
        assert action(row)==expected and journal==original


def test_ready_status_does_not_gain_interrupted_stage(monkeypatch):
    monkeypatch.setattr(p,'keyfile_ok',lambda row:True)
    journal={'state':'ready','autoUnlock':True}
    row=p.inspect(types.SimpleNamespace(records=lambda kind:[journal]))[0]
    assert row==dict(journal,keyfilePresent=True) and 'interruptedState' not in row


@pytest.mark.parametrize('auto',(False,True))
@pytest.mark.parametrize('present',(False,True))
def test_ready_status_reports_actual_key_presence_without_mutating_journal(monkeypatch,auto,present):
    calls=[]
    def check(row):
        calls.append(dict(row))
        return present
    monkeypatch.setattr(p,'keyfile_ok',check)
    journal={'state':'ready','autoUnlock':auto,'keyfilePresent':not present}
    original=dict(journal)
    row=p.inspect(types.SimpleNamespace(records=lambda kind:[journal]))[0]
    assert row['keyfilePresent'] is present
    assert calls==[original] and journal==original
    assert 'interruptedState' not in row


# Execute the real Panel navigation and dispatch bodies over actual inspect JSON.
# Only external launch/submit are replaced; no GTK, D-Bus or storage is invoked.
PANEL_PROBE=r"""
const fs=require('node:fs'),vm=require('node:vm');
const root=process.argv[1],input=JSON.parse(fs.readFileSync(0,'utf8'));
const Model=vm.createContext({});vm.runInContext(fs.readFileSync(root+'/Model.js','utf8'),Model);
const qml=fs.readFileSync(root+'/Panel.qml','utf8');
function body(marker){const start=qml.indexOf(marker);if(start<0)throw Error(marker);const open=qml.indexOf('{',start);let depth=1,i=open+1;for(;depth&&i<qml.length;i++){if(qml[i]==='{')depth++;if(qml[i]==='}')depth--;}return qml.slice(open+1,i-1);}
const chosenDrive=Model.dataDrives(input.snapshot)[0],calls=[];
const c=vm.createContext({Model,chosenDrive,cdConfigured:true,chosenMove:null,cdMount:'/data',canWrite:input.write,view:'drive',folderRows:[],targets:[],calls,service:{launch:a=>calls.push(['launch',Array.from(a)]),submit:r=>calls.push(['submit',JSON.parse(JSON.stringify(r))])}});
vm.runInContext('function actionAvailable(op){'+body('function actionAvailable(op)')+'}\nfunction act(op){'+body('function act(op)')+'}',c);
const nav=vm.runInContext('(function(){'+body('readonly property var nav:')+'})()',c);
const offered=Array.from(nav).filter(x=>['recover','reconnect','resume_drive'].includes(x.id)).map(x=>x.id);
for(const op of offered)c.act(op);
process.stdout.write(JSON.stringify({fix:chosenDrive.fix,offered,calls}));
"""


@pytest.mark.parametrize('kind',('legacy-manual','secret-only','auto'))
@pytest.mark.parametrize('present',(False,True))
@pytest.mark.parametrize('state',('locked','unmounted','mounted','idle','read-only','missing','identity'))
@pytest.mark.parametrize('down',(False,True))
def test_ready_inspect_to_model_to_actual_panel(monkeypatch,kind,present,state,down):
    monkeypatch.setattr(p,'keyfile_ok',lambda row:present)
    journal={'id':'a'*32,'name':'data','mountpoint':'/data','byId':'/dev/disk/by-id/TEST','serial':'TEST0001','state':'ready','autoUnlock':kind=='auto','manualSecretOnly':kind=='secret-only'}
    original=dict(journal)
    row=p.inspect(types.SimpleNamespace(records=lambda kind:[journal]))[0]
    assert journal==original
    disk={'id':'d','model':'Test','byId':row['byId'],'serial':row['serial'],'encrypted':True,'state':'locked' if state=='locked' else 'unlocked','system':False,'mounts':[]}
    if state in ('mounted','read-only'):disk['mounts']=[{'target':'/data','fstype':'btrfs','options':'rw' if state=='mounted' else 'ro'}]
    if state=='identity':disk['serial']='OTHER'
    snap={'drives':[row],'disks':[] if state=='missing' else [disk],'mounts':[{'target':'/data','fstype':'autofs'}] if state=='idle' else [],'moves':[{'id':'b'*32,'source':'/home/test/Files','destMount':'/data','state':'rebooted','bound':False}] if down else []}
    needed=state in ('locked','unmounted') or state in ('mounted','idle') and down
    expected=('recover' if kind!='auto' or not present else 'reconnect') if needed else ''
    for write in (True,False):
        result=subprocess.run(['node','-e',PANEL_PROBE,str(ROOT)],input=json.dumps({'snapshot':snap,'write':write}).encode(),capture_output=True,timeout=5,check=True)
        out=json.loads(result.stdout)
        assert out['fix']['action']==expected
        assert out['offered']==([expected] if expected else [])
        calls=[]
        if write and expected=='recover':calls=[['launch',['unlock','--drive',row['id'],'--label','data · /data']]]
        if write and expected=='reconnect':calls=[['submit',{'op':'reconnect_drive','id':row['id']}]]
        assert out['calls']==calls
