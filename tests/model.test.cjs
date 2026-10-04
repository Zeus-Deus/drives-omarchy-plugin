const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function model(){const file=path.join(__dirname,'../Model.js');assert.ok(fs.existsSync(file),'display and state model is not implemented');let c={};vm.createContext(c);vm.runInContext(fs.readFileSync(file,'utf8'),c);return c;}
test('confirmation fragments cannot inject controls/bidi or extra lines',()=>{const m=model();assert.equal(m.display('disk\n\u202e name\t'),'disk name');});
test('default destructive selection is Cancel and wrong serial cannot proceed',()=>{const m=model();assert.equal(m.canProvision({serial:'TESTNEW0002',system:false,selectable:true},'Y003'),false);assert.equal(m.canProvision({serial:'TESTNEW0002',system:false,selectable:true},'0002'),true);assert.equal(m.canProvision({serial:'TESTNEW0002',system:true,selectable:true},'0002'),false);});
test('wheel is immediate and clamped at both ends',()=>{const m=model();assert.equal(m.scroll(0,-120,0,1000,200,28),84);assert.equal(m.scroll(790,-120,0,1000,200,28),800);assert.equal(m.scroll(20,120,0,1000,200,28),0);});
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
