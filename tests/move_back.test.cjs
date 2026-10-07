const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function model(){const c=vm.createContext({});vm.runInContext(fs.readFileSync(path.join(__dirname,'../Model.js'),'utf8'),c);return c;}
test('a helper-qualified bind offers Move back even after the original was deleted',()=>{
    const m=model(),move={id:'a',state:'cleaned',bound:true,canMoveBack:true};
    assert.deepEqual(Array.from(m.moveActions(move,null)),['move_back']);
});
test('Move back is never offered for unqualified or unavailable binds',()=>{
    const m=model();
    for(const extra of [{},{canMoveBack:false},{canMoveBack:true,bound:false},{canMoveBack:true,needsAttention:true}])
        assert.ok(!m.moveActions({id:'a',state:'cleaned',bound:true,...extra},null).includes('move_back'));
    assert.ok(!m.moveActions({id:'b',state:'cleaned',bound:true,canMoveBack:true},{valid:true,moveId:'a',action:'continue'}).includes('move_back'));
});
test('an armed Move back offers only restart or withdrawal and names the correct direction',()=>{
    const m=model(),move={id:'a',state:'cleaned',bound:true,canMoveBack:true};
    const pending={valid:true,moveId:'a',action:'return',thisSession:true};
    assert.equal(m.moveStage(move,pending),'return-on-restart');
    assert.deepEqual(Array.from(m.moveActions(move,pending)),['restart','cancel_restart']);
    assert.match(m.moveText(move,pending)[1],/latest/i);
});
test('completed Move back is labelled as local data with its retained SSD copy',()=>{
    const m=model(),move={id:'a',state:'returned',source:'/home/u/Profile',sourceMount:'/home',destMount:'/data',retainedSSD:'/data/drives-a/content'};
    assert.equal(m.moveStage(move,null),'returned');
    assert.match(m.moveText(move,null)[1],/SSD copy.*kept/i);
    assert.match(m.moveMeta(move,null),/back.*\/home/i);
    assert.ok(!m.moveActions(move,null).includes('rollback_move'));
    assert.equal(m.warning({moves:[move]}),false);
});
test('interrupted Move back never routes to the forward-copy Resume API',()=>{
    const m=model(),move={id:'a',state:'paused',interruptedState:'return-switching',needsAttention:true,canMoveBack:true};
    assert.deepEqual(Array.from(m.moveActions(move,null)),['move_back']);
});
test('a returned local folder can be selected for another move without a stale SSD arrow',()=>{
    const m=model(),move={id:'back',state:'returned',source:'/home/u/Profile',sourceMount:'/home',destMount:'/data'};
    const snapshot={moves:[move],sizes:{home:'/home/u',entries:[{path:move.source,bytes:1024}],extras:{},homeBytes:1024,complete:true}};
    const row=m.spaceRows(snapshot,0).find(r=>r.path===move.source);
    assert.equal(row.movable,true);assert.equal(row.moveId,'');
    // Moved back means it is an ordinary local folder again: not listed (or
    // counted) under MOVED FOLDERS, but its record stays openable from history.
    assert.equal(m.folderRows(snapshot).length,0);
    assert.equal(m.moveStage(move,null),'returned');
});
