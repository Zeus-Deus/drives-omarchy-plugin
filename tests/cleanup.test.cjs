const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function model(){let c={};vm.createContext(c);vm.runInContext(fs.readFileSync(path.join(__dirname,'../Model.js'),'utf8'),c);return c;}
const cleaning=(p)=>({id:'a',source:'/home/u/.big',destMount:'/data',state:'cleaning',bound:true,oldCopyAvailable:true,verification:{bytes:100e9,files:900,directories:100},stats:{bytes:20e9},...p});

test('deleting an old copy is its own stage, never "needs inspection" or a warning',()=>{
    const m=model(),mv=cleaning({cleanupProgress:{deleted:250,total:1000},cleanupRunning:true});
    assert.equal(m.moveStage(mv,null),'cleaning');
    assert.equal(m.warning({moves:[mv]}),false);
    assert.equal(m.moveText(mv,null)[0],'Deleting old copy');
    assert.equal(m.folderRows({moves:[mv]})[0].status,'deleting old copy · 25%');
    assert.equal(m.folderRows({moves:[mv]})[0].tone,'dim');
    // Older helpers reported it as paused/cleaning: same words, no red mark.
    const old={...mv,state:'paused',interruptedState:'cleaning'};
    assert.equal(m.moveStage(old,null),'cleaning');assert.equal(m.warning({moves:[old]}),false);
});

test('progress text and fraction follow the helper count',()=>{
    const m=model();
    assert.equal(m.cleanupText(cleaning({cleanupProgress:{deleted:400000,total:931323},cleanupRunning:true})),'Deleting · 400,000 of 931,323 items · 42%');
    assert.equal(m.cleanupText(cleaning({cleanup:{deleted:10,total:100}})),'Waiting to continue · 10 of 100 items · 10%');
    assert.equal(m.cleanupText(cleaning({})),'Waiting to continue…');
    assert.equal(m.cleanupProgress(cleaning({cleanupProgress:{deleted:2000,total:1000}})).fraction,1);
});

test('old-copy sizes use the verified size, not the stale planning estimate',()=>{
    const m=model(),moved={...cleaning({}),state:'rebooted'};
    assert.equal(m.reclaimable([moved]),100e9);
    assert.equal(m.movedFolders('/data',[moved])[0].bytes,100e9);
    assert.equal(m.reclaimable([cleaning({cleanupProgress:{deleted:750,total:1000}})]),25e9);
    assert.equal(m.reclaimable([{...moved,state:'cleaned'}]),0);
    assert.equal(m.reclaimable([{...moved,oldCopyAvailable:false}]),0);
    // Moves recorded before verification sizes existed fall back to the estimate.
    assert.equal(m.movedBytes({stats:{bytes:5}}),5);
});

test('no delete or undo is offered while the old copy is being deleted',()=>{
    const m=model(),acts=Array.from(m.moveActions(cleaning({canDelete:true,canMoveBack:false}),null));
    assert.ok(!acts.includes('delete_old_copy')&&!acts.includes('rollback_move')&&!acts.includes('resume_move'),acts.join());
});

test('the panel opens the folder with a live bar after Delete old copy',()=>{
    const qml=fs.readFileSync(path.join(__dirname,'../Panel.qml'),'utf8');
    assert.ok(qml.includes('req.op === "delete_old_copy"')&&qml.includes('Model.cleanupText(root.chosenMove)')&&qml.includes('cleanupBox.p.fraction'));
});
