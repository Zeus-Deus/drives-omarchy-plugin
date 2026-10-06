const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function model(){const file=path.join(__dirname,'../Model.js');assert.ok(fs.existsSync(file),'display and state model is not implemented');let c={};vm.createContext(c);vm.runInContext(fs.readFileSync(file,'utf8'),c);return c;}
test('confirmation fragments cannot inject controls/bidi or extra lines',()=>{const m=model();assert.equal(m.display('disk\n\u202e name\t'),'disk name');});
test('default destructive selection is Cancel and wrong serial cannot proceed',()=>{const m=model();assert.equal(m.canProvision({serial:'TESTNEW0002',system:false,selectable:true},'Y003'),false);assert.equal(m.canProvision({serial:'TESTNEW0002',system:false,selectable:true},'0002'),true);assert.equal(m.canProvision({serial:'TESTNEW0002',system:true,selectable:true},'0002'),false);});
test('wheel is immediate and clamped at both ends',()=>{const m=model();assert.equal(m.scroll(0,-120,0,1000,200,150),150);assert.equal(m.scroll(790,-120,0,1000,200,150),800);assert.equal(m.scroll(20,120,0,1000,200,150),0);assert.equal(m.scroll(100,-120,-37,1000,200,150),137);});
function configured(){return {state:'ready',name:'archive',serial:'GUIARCHIVE0002',byId:'/dev/disk/by-id/GUIARCHIVE0002',mountpoint:'/data2'};}
function present(){return {serial:'GUIARCHIVE0002',byId:'/dev/disk/by-id/GUIARCHIVE0002',state:'mounted',encrypted:true,system:false,mounts:[{target:'/data2',fstype:'btrfs',options:'rw,relatime'}],usage:[]};}
for(const [label,disks] of [
    ['absent',[]],
    ['wrong stable path',[{...present(),byId:'/dev/disk/by-id/GUI-DECOY'}]],
    ['wrong serial',[{...present(),serial:'GUI-DECOY'}]],
    ['ambiguous identity',[present(),present()]],
    ['locked',[{...present(),state:'locked',mounts:[]}]],
    ['unmounted',[{...present(),state:'unlocked',mounts:[]}]],
    ['read-only',[{...present(),mounts:[{target:'/data2',fstype:'btrfs',options:'ro,relatime'}]}]],
    ['wrong mounted path',[{...present(),mounts:[{target:'/data3',fstype:'btrfs',options:'rw'}]}]],
    ['system disk',[{...present(),system:true}]],
    ['unencrypted disk',[{...present(),encrypted:false}]],
    ['unsupported disk',[{...present(),state:'unsupported'}]]
])test('saved ready journal cannot hide '+label+' drive warning',()=>{assert.equal(model().warning({drives:[configured()],disks}),true);});
test('matching mounted configured disk has no warning until full',()=>{const m=model();assert.equal(m.warning({drives:[configured()],disks:[present()]}),false);assert.equal(m.warning({drives:[configured()],disks:[{...present(),usage:[{percent:90}]}]}),true);});
const pend=(id,action,thisSession=true)=>({valid:true,moveId:id,action,thisSession});
test('a planned move offers only "move on restart" and cancel',()=>{const m=model();const mv={id:'a',state:'awaiting-maintenance'};assert.equal(m.moveStage(mv,null),'ready');assert.deepEqual(Array.from(m.moveActions(mv,null)),['resume_move','cancel_move']);});
test('an armed request offers restart and withdraw, never a second request',()=>{const m=model();const mv={id:'a',state:'awaiting-maintenance'};assert.equal(m.moveStage(mv,pend('a','continue')),'move-on-restart');assert.deepEqual(Array.from(m.moveActions(mv,pend('a','continue'))),['restart','cancel_restart']);assert.deepEqual(Array.from(m.moveActions(mv,pend('a','continue',false))),['restart']);});
test('another move armed blocks requests for this one',()=>{const m=model();assert.deepEqual(Array.from(m.moveActions({id:'b',state:'awaiting-maintenance'},pend('a','continue'))),['cancel_move']);assert.deepEqual(Array.from(m.moveActions({id:'b',state:'rebooted',canDelete:true,bound:true},pend('a','continue'))),['delete_old_copy']);});
test('interrupted moves that were restored say so and warn',()=>{const m=model();const mv={id:'a',state:'paused',interruptedState:'awaiting-maintenance',restoredFrom:'copying'};assert.equal(m.moveStage(mv,null),'interrupted');assert.equal(m.warning({moves:[mv]}),true);});
test('delete is offered only after the reboot check',()=>{const m=model();assert.ok(!m.moveActions({id:'a',state:'switched',bound:true},null).includes('delete_old_copy'));assert.ok(m.moveActions({id:'a',state:'rebooted',bound:true,canDelete:true},null).includes('delete_old_copy'));assert.ok(!m.moveActions({id:'a',state:'rebooted',bound:true,canDelete:false},null).includes('delete_old_copy'));});
test('a moved folder whose drive is missing warns',()=>{const m=model();assert.equal(m.warning({moves:[{id:'a',state:'rebooted',bound:true}]}),false);assert.equal(m.warning({moves:[{id:'a',state:'rebooted',bound:false}]}),true);});
test('needs-attention moves warn and every stage has words',()=>{const m=model();assert.equal(m.warning({moves:[{id:'a',state:'switched',bound:true,needsAttention:true}]}),true);for(const s of ['awaiting-maintenance','switched','rebooted','rolled-back','cleaned','paused','weird'])assert.equal(m.moveText({id:'x',state:s},null).length,2);});
test('panel never offers the removed live-copy controls',()=>{const qml=fs.readFileSync(path.join(__dirname,'../Panel.qml'),'utf8');for(const s of ['Continue (full verify)','Start over…','restart_move','.pre-move until'])assert.ok(!qml.includes(s),s);});
test('undone moves and older runs of the same folder are hidden',()=>{const m=model();const v=m.visibleMoves([{id:'1',source:'/h/V',state:'cleaned',created:1},{id:'2',source:'/h/V',state:'rolled-back',created:3},{id:'3',source:'/h/P',state:'rebooted',created:2}]);assert.deepEqual(Array.from(v,x=>x.id),['3']);assert.deepEqual(Array.from(m.visibleMoves([{id:'1',source:'/h/V',state:'cleaned',created:5},{id:'2',source:'/h/V',state:'rolled-back',created:3}]),x=>x.id),['1']);});
test('recovery unlock targets only the exact configured drive record',()=>{const m=model();const d={byId:'/dev/disk/by-id/X',serial:'S1'};const rec={id:'a'.repeat(32),state:'ready',byId:'/dev/disk/by-id/X',serial:'S1'};assert.equal(m.configuredFor(d,[rec]).id,rec.id);assert.equal(m.configuredFor(d,[{...rec,serial:'S2'}]),null);assert.equal(m.configuredFor(d,[{...rec,state:'unfinished'}]),null);assert.equal(m.configuredFor(d,[rec,{...rec,id:'b'.repeat(32)}]),null);});
test('health labels come from the helper verdict and never guess healthy',()=>{const m=model();const d={serial:'S1'};
 assert.equal(m.health(d,null).label,'Health: checking…');
 assert.equal(m.health(d,{disks:{S1:{state:'passed',temperature:41,reason:''}}}).label,'Health: OK · 41°C');
 assert.equal(m.health(d,{disks:{S1:{state:'unavailable',reason:'SMART is not available through this USB adapter'}}}).label,'Health: not available');
 assert.equal(m.health(d,{disks:{'*':{state:'unavailable',reason:'smartmontools is not installed'}}}).reason,'smartmontools is not installed');});
test('a failing or warning disk turns the bar red',()=>{const m=model();const disks=[{serial:'S1',usage:[]}];
 assert.equal(m.warning({disks,health:{disks:{S1:{state:'failing'}}}}),true);assert.equal(m.warning({disks,health:{disks:{S1:{state:'warning'}}}}),true);
 assert.equal(m.warning({disks,health:{disks:{S1:{state:'passed'}}}}),false);assert.equal(m.warning({disks,health:{disks:{S1:{state:'unavailable'}}}}),false);});
test('io rates come from two samples and ignore counter resets',()=>{const m=model();
 const a={sampledAt:1000,disks:[{name:'/dev/sda',io:{read:0,written:1000}}]},b={sampledAt:3000,disks:[{name:'/dev/sda',io:{read:4096,written:3000}}]};
 assert.deepEqual(JSON.parse(JSON.stringify(m.ioRates(a,b))),{'/dev/sda':{read:2048,write:1000}});
 assert.deepEqual(JSON.parse(JSON.stringify(m.ioRates(b,a))),{});assert.deepEqual(JSON.parse(JSON.stringify(m.ioRates(null,b))),{});
 assert.equal(m.ioText({read:2048,write:0}),'Read 2.0 KiB/s · Write 0 B/s');});
test('moved folders are listed under their drive, and reclaimable counts only kept old copies',()=>{const m=model();
 const moves=[{source:'/home/u/Videos',destMount:'/data',state:'rebooted',bound:true,stats:{bytes:2048}},{source:'/home/u/Music',destMount:'/data',state:'cleaned',bound:false,stats:{bytes:10}},{source:'/home/u/X',destMount:'/data',state:'awaiting-maintenance',stats:{bytes:5}},{source:'/home/u/Y',destMount:'/data2',state:'switched',stats:{bytes:7}}];
 assert.deepEqual(Array.from(m.movedFolders('/data',moves),f=>f.source),['/home/u/Videos','/home/u/Music']);
 assert.equal(m.reclaimable(moves),2048+7);});
test('footer hints only list keys that work in that view',()=>{const m=model();const keys=v=>m.footerHints(v).map(h=>h[0]);assert.ok(keys('overview').includes('a'));assert.ok(keys('overview').includes('m'));for(const v of ['drive','add','move','resume','progress']){assert.ok(!keys(v).includes('a'),v);assert.ok(!keys(v).includes('m'),v);}assert.deepEqual([...keys('progress')],['esc']);});
// ---- layout model (mockups A–F) ----
function snap(){return {rootEncrypted:false,testFixtureMode:true,
  disks:[{id:'vda',name:'vda',model:'Lexar NQ790 1TB',size:1e12,system:true,encrypted:true,serial:'SYS1',mounts:[{target:'/'}],usage:[{target:'/',total:1000,used:400,free:600}],state:'mounted'},
         {id:'sdb',name:'sdb',model:'Lexar NM990 4TB',size:4e12,system:false,encrypted:true,serial:'TESTDATA0001',byId:'/dev/disk/by-id/a',mounts:[{target:'/data',fstype:'btrfs',options:'rw'}],usage:[{target:'/data',total:1000,used:300,free:700}],state:'mounted',selectable:false,transport:'usb'},
         {id:'sdc',name:'sdc',model:'Crucial T705 2TB',size:2e12,system:false,encrypted:false,serial:'TESTNEW4F1A',byId:'/dev/disk/by-id/c',mounts:[],usage:[],state:'new',selectable:true,transport:'nvme'}],
  drives:[{id:'d1',name:'data',state:'ready',serial:'TESTDATA0001',byId:'/dev/disk/by-id/a',mountpoint:'/data',autoUnlock:true}],
  moves:[{id:'m1',source:'/home/u/Videos',destMount:'/data',state:'rebooted',bound:true,verified:true,stats:{bytes:200,files:3}},
         {id:'m2',source:'/home/u/Music',destMount:'/data',state:'cleaned',bound:true,verified:true,stats:{bytes:100,files:2}}],mounts:[{target:'/'},{target:'/data'}]};}
test('overview splits system, data drives with folder meter segments, and new disks',()=>{const m=model(),s=snap();
  const sys=m.systemDisks(s);assert.equal(sys.length,1);assert.equal(sys[0].pill,'LUKS2 · boot');
  const d=m.dataDrives(s);assert.equal(d.length,1);assert.equal(d[0].pill,'unlocks with OS');assert.equal(d[0].tone,'ok');assert.equal(d[0].problem,false);
  assert.deepEqual(JSON.parse(JSON.stringify(d[0].segments)),[{color:0,fraction:0.2},{color:1,fraction:0.1}]);assert.ok(Math.abs(d[0].other-0)<1e-9);
  assert.match(d[0].sub,/^\/data · 300 B used · 700 B free/);
  const n=m.newDisks(s);assert.equal(n.length,1);assert.match(n[0].sub,/serial …4F1A/);
  assert.equal(m.overviewMeta(s),'2 drives · 4.5 T · all encrypted');});
test('folder rows read like the mockup and share their drive colour',()=>{const m=model(),f=m.folderRows(snap());
  assert.deepEqual(f.map(r=>[r.source,r.dest,r.status,r.color]),[['~/Videos','/data','● mounted',0],['~/Music','/data','● mounted',1]]);});
test('add wizard never offers the system disk or a disk in use, and says why',()=>{const m=model(),c=m.addCandidates(snap());
  assert.equal(c[0].id,'sdc');const by=Object.fromEntries(c.map(x=>[x.id,x]));
  assert.equal(by.vda.selectable,false);assert.match(by.vda.sub,/system disk/);assert.equal(by.sdb.selectable,false);assert.match(by.sdb.sub,/in use · \/data/);
  assert.deepEqual(JSON.parse(JSON.stringify(m.suggestMount(snap()))),{mountpoint:'/data2',name:'data2'});});
test('a missing drive becomes an alert card listing its locked folders',()=>{const m=model(),s=snap();s.disks.splice(1,1);s.moves[0].bound=false;s.moves[1].bound=false;
  const a=m.alerts(s);assert.equal(a.length,1);assert.match(a[0].title,/isn't connected/);assert.deepEqual([...a[0].folders],['~/Videos','~/Music']);
  assert.equal(m.dataDrives(s)[0].pill,'not connected');assert.equal(m.moveTargets(s).length,0);});
test('a stale "not connected" refusal is hidden once the drive is mounted again',()=>{const m=model(),s=snap();
  const mv={id:'u',source:'/home/u/Unplug',destMount:'/data',state:'awaiting-maintenance',error:'Not moved this time; nothing was changed. (the destination drive is not connected)'};
  assert.equal(m.showMoveError(mv,s),false);s.disks.splice(1,1);assert.equal(m.showMoveError(mv,s),true);
  assert.equal(m.showMoveError({...mv,error:'an app has files open'},snap()),true);assert.equal(m.showMoveError({...mv,needsAttention:true},snap()),true);});
test('moved-folder checks and short sizes',()=>{const m=model();assert.equal(m.compact(350*1024**3),'350 G');assert.equal(m.compact(3.4*1024**4),'3.4 T');assert.equal(m.shortPath('/home/u/Videos'),'~/Videos');
  const c=m.moveChecks({state:'switched',verified:true,bound:true,source:'/home/u/Videos',stats:{files:41203}});assert.equal(c.length,3);assert.match(c[0].text,/41,203 files/);assert.equal(c[2].pending,true);
  assert.equal(m.moveChecks({state:'awaiting-maintenance'}).length,0);assert.equal(m.expandHome('~/Videos','/home/u'),'/home/u/Videos');});
test('configured drive offers the right fix: reconnect, recovery passphrase, or plug it in',()=>{const m=model();
 const d={...configured(),id:'a'.repeat(32),keyfilePresent:true};
 assert.equal(m.driveFix(d,[],[]).action,'');assert.match(m.driveFix(d,[],[]).hint,/Plug the drive back in/);
 const locked=[{...present(),state:'locked',mounts:[]}];
 assert.equal(m.driveFix(d,locked,[]).action,'reconnect');
 assert.equal(m.driveFix({...d,keyfilePresent:false},locked,[]).action,'recover');
 assert.equal(m.driveFix(d,[present()],[]).action,'');
 const down=[{destMount:'/data2',state:'rebooted',bound:false}];
 assert.equal(m.driveFix(d,[present()],down).action,'reconnect');});
test('folder arrows line up and move headers read cleanly',()=>{const m=model(),f=m.folderRows(snap());
  assert.equal(f[0].label.indexOf('→'),f[1].label.indexOf('→'));assert.equal(f[1].label,'~/Music  → /data');
  const mv=snap().moves[0];assert.equal(m.moveMeta(mv,null),'Moved · on /data');
  const plan={id:'p',source:'/home/z/Pics',destMount:'/data',state:'awaiting-maintenance',stats:{bytes:2048,files:1200}};
  assert.equal(m.moveMeta(plan,null),'Ready to move · to /data');assert.equal(m.moveFacts(plan,snap()),'2 K · 1,200 files · 700 B free on /data');assert.equal(m.moveFacts(mv,snap()),'');
  assert.equal(m.dataTotal(snap()),'3.6 T');});
const J=x=>JSON.parse(JSON.stringify(x));
// ---- hand-made setups, what takes space, apps ----
function manual(){return {mounts:[{target:'/',source:'/dev/mapper/root'}],apps:{steam:'/home/u/.local/share/Steam/steamapps',docker:true},
  disks:[{id:'os',model:'Lexar NQ790',system:true,encrypted:true,size:1e12,mounts:[{target:'/',source:'/dev/mapper/root',fsroot:'/@'}],usage:[{target:'/',total:1000,used:700,free:300}]},
   {id:'bulk',model:'Lexar NM990',system:false,encrypted:true,size:4e12,bootUnlock:'keyfile',mapper:'bulk',
    mounts:[{target:'/data',source:'/dev/mapper/bulk',fsroot:'/',fstype:'btrfs',options:'rw'},{target:'/home/u/Projects',source:'/dev/mapper/bulk',fsroot:'/Projects'},
            {target:'/var/lib/docker',source:'/dev/mapper/bulk',fsroot:'/docker'},{target:'/var/lib/docker/overlay2/x/merged',source:'/dev/mapper/bulk',fsroot:'/docker/overlay2/x'},
            {target:'/data/docker/overlay2/x/merged',source:'/dev/mapper/bulk',fsroot:'/docker/overlay2/x'}],
    usage:[{target:'/data',total:1000,used:200,free:800,rootOwned:false}]}],drives:[],moves:[]};}
test('a drive set up by hand shows how it unlocks and the folders bound from it',()=>{const m=model(),s=manual();
  s.mounts=s.mounts.concat(s.disks[1].mounts);
  const r=m.dataDrives(s)[0];assert.equal(r.pill,'unlocks with OS');assert.equal(r.manual,true);assert.match(r.sub,/set up by hand/);
  assert.deepEqual(J(r.folders.map(f=>[f.source,f.from])),[['/var/lib/docker','/data/docker'],['/home/u/Projects','/data/Projects']]);
  assert.equal(m.dataDrives({...s,disks:[s.disks[0],{...s.disks[1],bootUnlock:''}]})[0].pill,'opened by hand');
  const rows=m.manualFolderRows(s);assert.deepEqual(J(rows.map(r=>r.path)),['/home/u/Projects']);assert.equal(rows[0].label,'~/Projects → /data/Projects');
  const apps=m.appRows(s);assert.deepEqual(J(apps.map(a=>[a.id,a.status,a.movable])),[['steam','on the OS disk',true],['docker','● on /data',false]]);});
test('a hand-made drive is a move target only when it unlocks with a keyfile and its mount is root-owned',()=>{const m=model(),s=manual();
  assert.equal(m.moveTargets(s).length,0);assert.match(m.blockedTargets(s)[0].issue,/belongs to your user/);assert.equal(m.blockedTargets(s)[0].prepare,true);
  s.disks[1].usage[0].rootOwned=true;assert.deepEqual(J(m.moveTargets(s).map(t=>t.mountpoint)),['/data']);
  s.disks[1].bootUnlock='prompt';assert.equal(m.moveTargets(s).length,0);assert.match(m.blockedTargets(s)[0].issue,/keyfile/);});
test('sizes stream in line by line and fill the OS disk breakdown biggest first',()=>{const m=model();let z=m.emptySizes(true);
  for(const l of ['{"steam":"/home/u/.local/share/Steam/steamapps","home":"/home/u"}','not json','{"kind":"entry","path":"/home/u/Videos","bytes":300}',
    '{"kind":"entry","path":"/home/u/.cache","bytes":100}','{"kind":"entry","path":"/home/u/.local","bytes":200}','{"kind":"home","path":"/home/u","bytes":650}',
    '{"kind":"extra","path":"/home/u/.local/share/Steam/steamapps","bytes":150}','{"done":true,"complete":true}'])z=m.addSizeLine(z,l);
  const s=m.withSizes(manual(),z);const rows=m.spaceRows(s);
  assert.deepEqual(J(rows.map(r=>[r.title,r.movable])),[['~/Videos',true],['~/.local',false],['~/.cache',false],['System, apps & snapshots',false]]);
  assert.match(rows[1].why,/Steam/);assert.equal(rows[3].bytes,50);
  assert.equal(m.appRows(s)[0].size,'150 B');assert.equal(m.systemSegments(s).length,3);
  assert.equal(m.moveBlocker('/home/u/.ssh/x'),'app profile or settings · stays on the OS disk');assert.equal(m.moveBlocker('/var/lib/docker'),'outside your home folder');});
test('the panel protected list matches the helper',()=>{const m=model();const py=fs.readFileSync(path.join(__dirname,'../helper/moves.py'),'utf8');
  const set=py.match(/^PROTECTED=\{([^}]*)\}/m)[1].split(',').map(x=>x.trim().replace(/^'|'$/g,''));assert.deepEqual([...m.PROTECTED].sort(),set.sort());});
test('OS disk is the first stop on the overview and opens the space view',()=>{const qml=fs.readFileSync(path.join(__dirname,'../Panel.qml'),'utf8');
  assert.match(qml,/if \(sysRow\) out.push\(\{kind: "sys"/);assert.match(qml,/onChosen: root.openSystem\(\)/);});
test('a user-owned hand-made drive offers Prepare for moves; a prompt-unlock one does not',()=>{const m=model(),s=manual();
  const b=m.blockedTargets(s)[0];assert.equal(b.prepare,true);assert.equal(b.mountpoint,'/data');assert.match(b.issue,/One step first/);
  s.disks[1].bootUnlock='prompt';assert.equal(m.blockedTargets(s)[0].prepare,false);
  const qml=fs.readFileSync(path.join(__dirname,'../Panel.qml'),'utf8');assert.match(qml,/op: "prepare_drive", mountpoint: mountpoint/);
  const br=fs.readFileSync(path.join(__dirname,'../bridge.py'),'utf8');assert.match(br,/'prepare_drive':\('PrepareDrive',\('mountpoint',\)\)/);});
test('space list shows 12 then all, with live progress and a typed folder',()=>{const m=model();let z=m.emptySizes(true);
  for(let i=0;i<20;i++)z=m.addSizeLine(z,JSON.stringify({kind:'entry',path:'/home/u/F'+i,bytes:1000-i}));
  const s=m.withSizes(manual(),z);
  assert.equal(m.spaceRows(s,12).length,12);assert.equal(m.spaceRows(s,0).length,20);
  assert.deepEqual(J(m.spaceCount(s,12)),{total:20,hidden:8});
  assert.equal(m.sizeProgress(z,83000),'Measuring your home folder · 20 folders so far · 1:23');
  assert.equal(m.sizeProgress(m.emptySizes(false),0),'');
  assert.equal(m.typedFolder('~/Games/Lib','/home/u').path,'/home/u/Games/Lib');
  assert.match(m.typedFolder('~/.ssh','/home/u').why,/stays on the OS disk/);
  assert.match(m.typedFolder('Games','/home/u').why,/full path/);
  assert.match(m.typedFolder('/etc','/home/u').why,/outside your home/);
  assert.equal(m.typedFolder('','/home/u').why,'');});
test('wheel: high-resolution notches add up, and a resting pointer cannot grab the highlight',()=>{const m=model();
  let y=0;for(let i=0;i<8;i++)y=m.scroll(y,-15,0,1000,200,150);assert.equal(y,150);
  const qml=fs.readFileSync(path.join(__dirname,'../Panel.qml'),'utf8');
  assert.match(qml,/pointerGate\.moved\(row, mouse\)/);assert.doesNotMatch(qml,/onContainsMouseChanged:[^\n]*root\.cursor/);});
