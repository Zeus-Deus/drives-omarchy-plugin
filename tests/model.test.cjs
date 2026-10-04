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
