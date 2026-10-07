const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const repo = path.join(__dirname, '..');
const plain = value => JSON.parse(JSON.stringify(value));
function model() {
    const c = vm.createContext({});
    vm.runInContext(fs.readFileSync(path.join(repo, 'Model.js'), 'utf8'), c);
    return c;
}
// Execute the shipped QML function bodies, not a duplicate state machine.
function functions(source) {
    const out = [];
    const re = /^    function (\w+)\([^)]*\)\s*\{/gm;
    let match;
    while ((match = re.exec(source))) {
        let i = re.lastIndex, depth = 1, quote = '', comment = '';
        for (; i < source.length && depth; i++) {
            const ch = source[i], next = source[i + 1];
            if (comment === '//') { if (ch === '\n') comment = ''; continue; }
            if (comment === '/*') { if (ch === '*' && next === '/') { comment = ''; i++; } continue; }
            if (quote) { if (ch === '\\') { i++; continue; } if (ch === quote) quote = ''; continue; }
            if (ch === '/' && next === '/') { comment = '//'; i++; continue; }
            if (ch === '/' && next === '*') { comment = '/*'; i++; continue; }
            if (ch === '"' || ch === "'" || ch === '`') { quote = ch; continue; }
            if (ch === '{') depth++;
            if (ch === '}') depth--;
        }
        out.push(source.slice(match.index, i));
    }
    return out.join('\n');
}
function panel() {
    const source = fs.readFileSync(path.join(repo, 'Panel.qml'), 'utf8');
    const c = vm.createContext({Model: model(), console});
    vm.runInContext(`
        var root=this, hostWidget=null, bar={activePopout:null};
        var controller={open:true,popoutSwitchClosing:false};
        var view='move',home='/home/u',moveTarget='/data',watchJob='',cursor=0;
        var assessment=null,assessmentEpoch=0,scheduledRequest=null,confirmationAction=null;
        var selectedMoveId='',selectedKey='',showAllSpace=false,sourceDestination='';
        var confirm={opened:false,selectedIndex:1,forceActiveFocus:function(){}};
        var folderField={text:'~/.config',forceActiveFocus:function(){}};
        var otherField={text:''},pointerGate={reset:function(){}};
        var flick={contentY:0},catcher={forceActiveFocus:function(){}};
        var storage={helperAvailable:true,seamlessMoves:true,jobs:[],moves:[]};
        var targets=[{mountpoint:'/data',title:'SSD'}];
        var requests=[],service={opened:true,mutating:false,error:'',scanSizes:function(){},submit:function(req){requests.push(req);return true;}};
        var later=[],Qt={callLater:function(fn){later.push(fn);}};
        var Quickshell={execDetached:function(){throw Error('must not restart automatically');}};
    `, c);
    vm.runInContext('var systemRows=[],driveRows=[],newRows=[],folderRows=[],manualRows=[],apps=[],spaceRows=[],candidates=[],spaceCount={hidden:0},sysRow=null,restartArmed=false;',c);
    for (const name of ['opened','moves','pendingRestart','moveSource','canWrite','seamlessMoves','ownsInteraction','nav','chosenDrive','chosenMove','watchedJob','heading','jobDone','cdConfigured','typed']) {
        const expr = source.match(new RegExp('^    readonly property \\w+ ' + name + ': (.+)$', 'm'))[1];
        const body = expr === '{' ? source.match(new RegExp('^    readonly property \\w+ ' + name + ': \\{([\\s\\S]*?)^    \\}', 'm'))[1] : '';
        Object.defineProperty(c, name, {get: () => vm.runInContext(body ? '(()=>{'+body+'})()' : expr, c)});
    }
    vm.runInContext(functions(source), c);
    return c;
}
function assessed(c, prep = true) {
    c.reviewMove();
    const req = c.requests[0];
    c.handleFinished({ok:true,jobId:'assessment-1'}, req);
    c.handleAssessment({id:'assessment-1',method:'AssessMove',state:'done',result:{ok:true,source:'/home/u/.config',destMount:'/data',needsPreparation:prep,stats:{bytes:2048,files:7}}});
    return req;
}
test('SSD-first flow offers home folders and retains the selected SSD through selection',()=>{
    const c=panel();
    c.selectedKey='disk:bulk';
    c.driveRows=[{key:'disk:bulk',usage:{target:'/data2'},drive:null,fix:{action:''}}];
    c.targets=[{mountpoint:'/data'},{mountpoint:'/data2'}];
    c.service.scanSizes=()=>{};
    c.act('move_here');
    assert.equal(c.view,'system');
    assert.equal(c.sourceDestination,'/data2');
    c.spaceRows=[{path:'/home/u/.cache',movable:true}];
    c.openSpace('/home/u/.cache');
    assert.equal(c.view,'move');
    assert.equal(c.moveSource,'/home/u/.cache');
    assert.equal(c.moveTarget,'/data2');
    c.openSystem();
    assert.equal(c.sourceDestination,'','normal OS browsing must not inherit a previous destination');
});
test('read-only safety assessment precedes Cancel-default review and one scheduling request', () => {
    const c = panel();
    c.reviewMove();
    assert.equal(c.confirm.opened, false, 'confirmation must wait for assessment');
    assert.deepEqual(plain(c.requests), [{op:'assess_move',src:'/home/u/.config',destMount:'/data'}]);
    assert.equal(c.assessment.state, 'running');
    const req = c.requests[0];
    c.handleFinished({ok:true,jobId:'assessment-1'}, req);
    c.handleAssessment({id:'assessment-1',method:'AssessMove',state:'done',result:{ok:true,source:'/home/u/.config',destMount:'/data',needsPreparation:true,stats:{bytes:2048,files:7}}});
    assert.equal(c.confirm.opened, true);
    assert.equal(c.confirm.selectedIndex, 0);
    assert.match(c.confirm.message, /7 files/);
    assert.match(c.confirm.message, /next restart/);
    assert.match(c.confirm.message, /old copy stays until you delete it/);
    assert.match(c.confirm.message, /First move to \/data: its top folder becomes system-owned for good\. Your files stay yours\./);
    assert.ok(c.confirm.message.length<420,'review stays short enough to fit the panel');
    assert.match(c.confirm.message, /for good/i,'the one-way ownership change is still disclosed');
    c.confirmationAction();
    assert.deepEqual(plain(c.requests[1]), {op:'schedule_move',src:'/home/u/.config',destMount:'/data',prepareDestination:true});
    assert.equal(c.requests.length, 2, 'no separate PrepareDrive or ResumeMove');
});
const resultJob = (prep=false, overrides={}) => ({id:'assessment-1',method:'AssessMove',state:'done',result:{ok:true,source:'/home/u/.config',destMount:'/data',needsPreparation:prep,stats:{bytes:2048,files:7},...overrides}});
for (const [label, change] of [
    ['close', c => c.close()], ['navigation', c => c.go('overview')],
    ['source edit', c => {c.folderField.text='~/Other';}], ['destination edit', c => {c.moveTarget='/data2';}],
    ['foreign popout', c => {c.bar.activePopout={foreign:true};}],
    ['popout handoff', c => c.closeForPopoutSwitch()]
]) test('late assessment cannot open a review after '+label, () => {
    const c=panel();c.reviewMove();const req=c.requests[0];change(c);
    c.handleFinished({ok:true,jobId:'assessment-1'},req);c.handleAssessment(resultJob());
    assert.equal(c.confirm.opened,false);assert.equal(c.requests.length,1);
});
for (const [label, change] of [
    ['source edit',c=>{c.folderField.text='~/Other';}], ['target edit',c=>{c.moveTarget='/data2';}],
    ['close and reopen',c=>{c.close();c.controller.open=true;}], ['navigation',c=>c.go('drive')],
    ['foreign popout',c=>{c.bar.activePopout={foreign:true};}]
]) test('captured confirmation cannot schedule after '+label, () => {
    const c=panel();assessed(c);const fn=c.confirmationAction;change(c);fn();assert.equal(c.requests.length,1);
});
test('no preparation consent is sent for an already safe mount',()=>{
    const c=panel();assessed(c,false);assert.doesNotMatch(c.confirm.message,/for good|system-owned/);c.confirmationAction();
    assert.equal(c.requests[1].prepareDestination,false);c.confirmationAction && c.confirmationAction();assert.equal(c.requests.length,2);
});
test('a failed assessment explains actual use and Retry reassesses',()=>{
    const c=panel();c.reviewMove();c.handleFinished({ok:true,jobId:'assessment-1'},c.requests[0]);
    c.handleAssessment({id:'assessment-1',method:'AssessMove',state:'failed',error:'Files open in editor (pid 123)'});
    assert.equal(c.assessment.state,'failed');assert.equal(c.assessment.error,'Files open in editor (pid 123)');assert.equal(c.confirm.opened,false);
    c.reviewMove();assert.equal(c.requests.length,2);assert.equal(c.requests[1].op,'assess_move');
});
test('a superseded assessment is ignored even when it finishes after the retry',()=>{
    const c=panel();c.reviewMove();const old=c.requests[0];c.go('overview');c.openMoveFolder('/home/u/.config','/data');c.reviewMove();const current=c.requests[1];
    c.handleFinished({ok:true,jobId:'old'},old);assert.equal(c.assessment.jobId,'');
    c.handleFinished({ok:true,jobId:'assessment-1'},current);c.handleAssessment(resultJob());assert.equal(c.confirm.opened,true);
});
for(const overrides of [{source:'/home/u/Other'},{destMount:'/data2'},{needsPreparation:undefined},{stats:null},{stats:{files:-1,bytes:0}}])test('malformed/mismatched assessment result fails closed '+JSON.stringify(overrides),()=>{
    const c=panel();c.reviewMove();c.handleFinished({ok:true,jobId:'assessment-1'},c.requests[0]);c.handleAssessment(resultJob(false,overrides));
    assert.equal(c.confirm.opened,false);assert.equal(c.assessment.state,'failed');assert.match(c.assessment.error,/invalid|match/i);
});
test('old helper has an explicit update message, never legacy fallback',()=>{
    const c=panel();c.storage.seamlessMoves=false;c.reviewMove();assert.equal(c.requests.length,0);assert.match(c.service.error,/update.*helper/i);
});
test('invalid/empty sources and vanished destinations cannot begin assessment',()=>{
    for(const src of ['', '/etc', '/home/other/.config','~','~/../Other']) {const c=panel();c.folderField.text=src;c.reviewMove();assert.equal(c.requests.length,0,src);}
    const c=panel();c.targets=[];c.reviewMove();assert.equal(c.requests.length,0);
});
test('selected folder names retain meaningful trailing spaces instead of targeting a different folder',()=>{
    const c=panel();c.openMoveFolder('/home/u/Folder ','/data');c.reviewMove();
    assert.equal(c.moveSource,'/home/u/Folder ');
    assert.equal(c.requests[0].src,'/home/u/Folder ');
    assert.equal(c.Model.expandHome('~/Folder ','/home/u'),'/home/u/Folder ');
    assert.equal(c.Model.typedFolder('~/Folder/','/home/u').path,'','noncanonical paths are explained, not silently repaired');
});
test('schedule acceptance does not navigate or popup when the panel lost ownership',()=>{
    const c=panel();assessed(c);c.confirmationAction();const req=c.requests[1];c.close();c.handleFinished({ok:true,jobId:'schedule-1'},req);
    assert.equal(c.view,'move');assert.equal(c.confirm.opened,false);
});
function serviceHarness() {
    const source=fs.readFileSync(path.join(repo,'Service.qml'),'utf8');
    const c=vm.createContext({Model:model(),console});
    vm.runInContext(`var root=this,opened=true,busy=false,loaded=false,suppress=false,pending=null,error='';
        var request={op:'status'},snapshot={disks:[]},rates={},requestEpoch=0,timedOut=false;
        var bridgePath='/fixture/bridge.py',worker={running:false,signal:function(){},stdinEnabled:false},output={text:''};
        var deadline={restart:function(){},stop:function(){},interval:0};
        var later=[],Qt={callLater:function(fn){later.push(fn);}},events=[];
        function finished(result,req){events.push({result:result,req:req});}
    `,c);
    vm.runInContext(functions(source),c);
    c.stopped=()=>vm.runInContext(source.match(/^        onRunningChanged: (.+)$/m)[1],c);
    c.running=false;
    return c;
}
test('service carries the original request through success and errors for assessment lifecycle',()=>{
    for(const [code,text,message] of [[0,'{"ok":true,"jobId":"a"}',null],[0,'{"ok":false,"error":"helper busy"}','helper busy'],[127,'','could not run'],[90,'','size limit'],[0,'bad json','Invalid storage']]) {
        const c=serviceHarness();const req={op:'assess_move',src:'/home/u/.config',destMount:'/data'};
        assert.equal(c.submit(req),true);c.output.text=text;c.complete(code);
        assert.equal(c.events.length,1);assert.equal(c.events[0].req,req);
        if(message){assert.equal(c.events[0].result.ok,false);assert.match(c.events[0].result.error,new RegExp(message));}
        else assert.equal(c.events[0].result.jobId,'a');
        c.complete(code);assert.equal(c.events.length,1,'completion is once only');
    }
});
test('stopped fallback from an old process cannot complete a newer request',()=>{
    const c=serviceHarness();c.submit({op:'assess_move',src:'/home/u/.config',destMount:'/data'});
    c.worker.running=false;c.stopped();c.output.text='{"ok":true,"jobId":"old"}';c.complete(0);
    c.submit({op:'status'});c.worker.running=false;c.later.shift()();
    assert.equal(c.busy,true,'an older callLater must not finish this generation');assert.equal(c.events.length,1);
});
test('service reports startup failure even when Process emits no exit',()=>{
    const c=serviceHarness();c.submit({op:'assess_move',src:'/home/u/.config',destMount:'/data'});c.worker.running=false;c.stopped();c.later.shift()();
    assert.equal(c.busy,false);assert.equal(c.events.length,1);assert.equal(c.events[0].result.ok,false);
});
test('write requests are refused while mutating rather than silently losing review',()=>{
    const c=serviceHarness();c.submit({op:'schedule_move',src:'/home/u/.config',destMount:'/data',prepareDestination:true});
    assert.equal(c.submit({op:'assess_move',src:'/home/u/X',destMount:'/data'}),false);
});
test('drive detail Move folder here preselects that SSD without a preparation step',()=>{
    const c=panel();c.driveRows=[{key:'disk:ssd',title:'SSD',drive:null,usage:{target:'/data'},fix:{action:''}}];c.selectedKey='disk:ssd';c.view='drive';
    assert.ok(c.nav.some(n=>n.kind==='act' && n.id==='move_here'));assert.ok(!c.nav.some(n=>n.id==='prepare'));
    c.act('move_here');assert.equal(c.view,'system');assert.equal(c.sourceDestination,'/data');assert.equal(c.requests.length,0);
});
test('scheduled result says next restart; preparation failures never claim nothing changed',()=>{
    const c=panel();c.view='progress';c.watchJob='s';c.storage.jobs=[{id:'s',method:'ScheduleMove',state:'failed',error:'preparation completed; latch failed'}];
    assert.doesNotMatch(c.heading[2],/nothing was changed/);assert.match(c.heading[2],/preparation|inspect/i);
    c.storage.jobs=[{id:'s',method:'ScheduleMove',state:'done',result:{ok:true,id:'m',state:'restart-required',action:'continue'}}];
    assert.match(c.heading[1],/next restart/i);
});
test('completed helper jobs keep meaningful labels instead of undefined gerunds',()=>{
    const m=model();
    for(const [method,title] of [['ProvisionDrive','Set up the drive'],['CancelMove','Cancelled the move'],['DeleteOldCopy','Deleted the old copy'],['RollbackMove','Scheduled the undo']])
        assert.equal(m.jobText({method,state:'done'}).title,title);
});
test('Move back reviews latest-file preservation and defaults to Cancel before scheduling',()=>{
    const c=panel();c.view='resume';c.selectedMoveId='back';
    c.storage.moves=[{id:'back',source:'/home/u/Profile',state:'cleaned',bound:true,canMoveBack:true}];
    c.act('move_back');assert.equal(c.confirm.opened,true);assert.equal(c.confirm.selectedIndex,0);
    assert.match(c.confirm.message,/latest files/i);assert.match(c.confirm.message,/copy.*kept/i);
    assert.equal(c.requests.length,0);c.confirmationAction();
    assert.deepEqual(plain(c.requests),[{op:'move_back',id:'back'}]);
});
test('untouched incomplete plans offer Cancel rather than a Resume that cannot run',()=>{
    const m=model();
    const stopped={id:'failed',state:'paused',needsAttention:true,interruptedState:'planned',canCancelIncomplete:true};
    assert.deepEqual(plain(m.moveActions(stopped,null)),['cancel_move']);
    assert.deepEqual(plain(m.moveActions({...stopped,canCancelIncomplete:false},null)),['resume_move']);
});
test('planned rows open their existing move, aggregate rows explain why, all remain cursor reachable',()=>{
    const c=panel();c.view='system';c.spaceRows=[{path:'/home/u/Videos',movable:false,moveId:'m1',why:'move already planned'},{path:'',movable:false,why:'outside your home folder'}];
    assert.equal(c.nav.filter(n=>n.kind==='space').length,2);c.run(c.nav[0]);assert.equal(c.view,'resume');assert.equal(c.selectedMoveId,'m1');
    c.view='system';c.run(c.nav[1]);assert.match(c.service.error,/outside your home/);
});
test('actual move UI has assessment progress/retry, no name blacklist claim or separate preparation buttons',()=>{
    const source=fs.readFileSync(path.join(repo,'Panel.qml'),'utf8');
    assert.match(source,/Safety check/);assert.match(source,/Check again/);
    assert.match(source,/assessment\.state === "running"/);
    assert.doesNotMatch(source,/Open files, profiles, keyrings, databases/);
    assert.doesNotMatch(source,/selectable: r\.movable === true|opacity: r\.movable \? 1 : 0\.7/);
    assert.match(source,/onMoveSourceChanged: invalidateAssessment\(\)/);
    assert.match(source,/onMoveTargetChanged: invalidateAssessment\(\)/);
    assert.match(source,/onOwnsInteractionChanged: if \(!ownsInteraction\) invalidateAssessment\(\)/);
});
test('row-selected folder paths are sent byte-exact, never display-sanitized',()=>{
    const c=panel();const src='/home/u/F\n\u202e  weird';c.openMoveFolder(src,'/data');c.reviewMove();assert.equal(c.requests[0].src,src);
});
test('an edit away and back invalidates the old review through actual change handlers',()=>{
    const source=fs.readFileSync(path.join(repo,'Panel.qml'),'utf8');
    for(const name of ['MoveSource','MoveTarget','OwnsInteraction']) {
        const c=panel();assessed(c);const old=c.confirmationAction;
        vm.runInContext(source.match(new RegExp('^    on'+name+'Changed: (.+)$','m'))[1],c);
        if(name==='OwnsInteraction') {c.bar.activePopout={foreign:true};vm.runInContext(source.match(/^    onOwnsInteractionChanged: (.+)$/m)[1],c);c.bar.activePopout=null;}
        assert.equal(c.confirm.opened,false);old();assert.equal(c.requests.length,1);
    }
});
test('interrupted assessment jobs cannot silently leave the user waiting',()=>{
    const c=panel();c.reviewMove();c.handleFinished({ok:true,jobId:'assessment-1'},c.requests[0]);
    c.handleAssessment({id:'assessment-1',method:'AssessMove',state:'interrupted',error:'Helper restarted before assessment finished'});
    assert.equal(c.assessment.state,'failed');assert.match(c.assessment.error,/restarted/);
});
test('Move folder here visibility does not dereference a missing drive usage row',()=>{
    const c=panel();c.driveRows=[{key:'missing',usage:null,drive:{id:'gone'},fix:{action:''}}];c.selectedKey='missing';c.view='drive';
    const source=fs.readFileSync(path.join(repo,'Panel.qml'),'utf8');
    const expr=source.match(/ActionRow \{ visible: (.+); op: "move_here" \}/)[1];
    vm.runInContext('var cd=chosenDrive;',c);
    assert.equal(vm.runInContext(expr,c),false);
});
test('helper-update copy gives the actual installer command',()=>{
    const c=panel();c.storage.seamlessMoves=false;c.reviewMove();
    assert.match(c.service.error,/sudo bash ~\/\.config\/omarchy\/plugins\/io\.github\.zeus-deus\.drives\/helper\/install\.sh/);
});
test('bridge deadline drains the old Process before permitting a fresh request',()=>{
    const c=serviceHarness(),source=fs.readFileSync(path.join(repo,'Service.qml'),'utf8');
    c.submit({op:'assess_move',src:'/home/u/.config',destMount:'/data'});
    vm.runInContext(source.match(/id:deadline\s+onTriggered: (.+)/)[1],c);
    assert.equal(c.busy,true,'timed-out worker must finish stopping before reuse');
    assert.equal(c.submit({op:'assess_move',src:'/home/u/Other',destMount:'/data'}),false);
    c.worker.running=false;c.output.text='{"ok":true,"jobId":"late"}';c.complete(0);
    assert.equal(c.events[0].result.ok,false,'a response after the deadline is not success');
    assert.equal(c.events[0].req.src,'/home/u/.config');
});
test('a queued scheduling request is already gated as mutating during status preemption',()=>{
    const c=serviceHarness(),source=fs.readFileSync(path.join(repo,'Service.qml'),'utf8');
    c.submit({op:'status'});const schedule={op:'schedule_move',src:'/home/u/.config',destMount:'/data',prepareDestination:false};
    assert.equal(c.submit(schedule),true);
    assert.equal(vm.runInContext(source.match(/^    readonly property bool mutating: (.+)$/m)[1],c),true);
    c.worker.running=false;c.complete(15);assert.equal(c.events.length,0,'preempted status is not an action completion');
    c.later.shift()();assert.equal(c.request,schedule);c.worker.running=false;c.output.text='{"ok":true,"jobId":"s"}';c.complete(0);
    assert.equal(c.events.length,1);assert.equal(c.events[0].req,schedule);
});
test('Enter on the actual Cancel-default kit dialog creates no scheduling request',t=>{
    const kit='/usr/share/omarchy/shell/Ui/ConfirmDialog.qml';
    if(!fs.existsSync(kit)){t.skip('Omarchy kit is not installed');return;}
    const c=panel();assessed(c);
    const source=fs.readFileSync(path.join(repo,'Panel.qml'),'utf8');
    const canceled=source.match(/^                onCanceled: (.+)$/m)[1];
    const confirmed=source.match(/^                onConfirmed: (.+)$/m)[1];
    const dialog=vm.createContext({root:{},Qt:{Key_Return:1,Key_Enter:2,Key_Escape:3,Key_Left:4,Key_Right:5,Key_Tab:6,Key_Backtab:7}});
    dialog.root=c.confirm;
    dialog.root.canceled=()=>vm.runInContext('with(confirm) '+canceled,c);
    dialog.root.confirmed=()=>vm.runInContext('with(confirm) '+confirmed,c);
    vm.runInContext(functions(fs.readFileSync(kit,'utf8').replace(/^  function /gm,'    function ')),dialog);
    dialog.handleKey({key:1});assert.equal(c.confirm.opened,false);assert.equal(c.requests.length,1);
    c.reviewMove();assert.equal(c.confirm.selectedIndex,0,'reopened review defaults to Cancel again');
    dialog.root.selectedIndex=1;dialog.handleKey({key:1});assert.equal(c.requests.length,2);assert.equal(c.requests[1].op,'schedule_move');
});

// ---- seamless UX: start on selection, in-use is a heads-up, live progress ----
test('choosing a drive starts the safety check at once; no hidden second button',()=>{
    const c=panel();c.targets=[{mountpoint:'/data',title:'A'},{mountpoint:'/data2',title:'B'}];
    c.folderField.text='~/Videos';c.moveTarget='';c.view='move';
    c.chooseTarget('/data2');
    assert.equal(c.requests.length,1);assert.deepEqual(plain(c.requests[0]),{op:'assess_move',src:'/home/u/Videos',destMount:'/data2'});
    assert.equal(c.assessment.state,'running');assert.equal(typeof c.assessment.started,'number');
    c.chooseTarget('/data2');assert.equal(c.requests.length,1,'a running check is not restarted');
});
test('opening the move screen with folder and drive known starts checking immediately',()=>{
    const c=panel();c.openMoveFolder('/home/u/Videos','/data');
    assert.equal(c.folderField.text,'~/Videos','shown in the familiar ~ form');
    assert.equal(c.moveSource,'/home/u/Videos','sent byte-exact');
    assert.equal(c.requests.length,1);assert.equal(c.requests[0].op,'assess_move');
});
test('half-typed or empty folders never start a check or raise an error',()=>{
    for(const t of ['','/etc','~/../x','Videos']){const c=panel();c.folderField.text=t;c.view='move';c.autoReview();assert.equal(c.requests.length,0,t);assert.equal(c.service.error,'',t);}
});
test('tilde display keeps exotic paths byte-exact',()=>{
    for(const src of ['/home/u/F\n\u202e  weird','/home/u/Folder ','/home/u/~odd','/home/other/x']){
        const c=panel();c.openMoveFolder(src,'/data');
        if(c.requests.length)assert.equal(c.requests[0].src,src);
        assert.equal(c.moveSource,src);
    }
});
test('apps using the folder are named in the review instead of refusing',()=>{
    const m=model();
    const inUse=[{pid:1,name:'python3',unit:'hermes-serve.service'},{pid:2,name:'npm exec vite',unit:'app-org.chromium.Chromium-403278.scope'},
        {pid:3,name:'node-MainThread',unit:'app-org.chromium.Chromium-403278.scope'},{pid:4,name:'esbuild',unit:'app-org.chromium.Chromium-403278.scope'},{pid:5,name:'hermes'}];
    const g=m.inUseGroups(inUse);
    assert.equal(g.length,3);assert.equal(g[0].label,'hermes-serve');assert.equal(g[0].kind,'service');
    assert.equal(g[1].label,'Chromium');assert.equal(g[1].count,3);
    const text=m.inUseText(inUse);
    assert.equal(text,'Open now: hermes-serve, Chromium and hermes. The restart closes them cleanly.');
    assert.doesNotMatch(text,/pid|cwd|mmap/);
    const review=m.moveReview({source:'/home/u/.hermes',destMount:'/data',stats:{bytes:1,files:2},inUse});
    assert.match(review,/Open now:/);assert.doesNotMatch(review,/Restart now is optional/);
    const launched=m.inUseText([{pid:1,name:'hermes',unit:'app-Hyprland-xdg\\x2dterminal\\x2dexec-9.scope'},{pid:2,name:'Hermes',unit:'app-Hyprland-gtk\\x2dlaunch-12.scope'}]);
    assert.equal(launched,'Open now: a terminal and Hermes. The restart closes them cleanly.','launcher scopes are unescaped and named by what they run');
    assert.equal(m.inUseText([]),'');assert.doesNotMatch(m.moveReview({source:'/home/u/V',destMount:'/data',stats:{}}),/Open now/);
});
test('in-use names are sanitized and the list is bounded',()=>{
    const m=model();const many=[];for(let i=0;i<20;i++)many.push({pid:i,name:'x\u202e'+i+'\nline'});
    const t=m.inUseText(many);assert.doesNotMatch(t,/[\u202e\n]/);assert.match(t,/17 more/);
});
test('assessment progress line shows items checked and elapsed time',()=>{
    const m=model();
    assert.equal(m.assessProgress('/home/u/.hermes',null,4200),'Checking ~/.hermes · 0:04');
    assert.equal(m.assessProgress('/home/u/.hermes',{progress:{entries:41200}},65000),'Checking ~/.hermes · '+(41200).toLocaleString()+' items · 1:05');
});
test('a scheduled restart-time step is never called done',()=>{
    const m=model();
    for(const [method,action] of [['ScheduleMove','continue'],['RollbackMove','rollback'],['MoveBack','return'],['ResumeMove','continue']]){
        const job={method,state:'done',result:{ok:true,state:'restart-required',action}};
        assert.equal(m.jobText(job).scheduled,true,method);assert.equal(m.jobMeta(job),'scheduled · happens on restart');
    }
    assert.equal(m.jobMeta({method:'DeleteOldCopy',state:'done',result:{ok:true,state:'cleaned'}}),'done');
    const source=fs.readFileSync(path.join(repo,'Panel.qml'),'utf8');
    assert.match(source,/Model\.jobMeta\(watchedJob\)/);assert.match(source,/Nothing has moved yet/);
    assert.doesNotMatch(source,/hidden folders and dormant profiles are not refused just for their names/);
});
test('an idle automounted drive is a move target, not "not in use"',()=>{
    const m=model();
    const drive={id:'a'.repeat(32),state:'ready',byId:'/dev/disk/by-id/X',serial:'S1',mountpoint:'/data',name:'bulk',autoUnlock:true};
    const disk={id:'X',byId:'/dev/disk/by-id/X',serial:'S1',encrypted:true,state:'unlocked',mounts:[],usage:[]};
    const snap={drives:[drive],disks:[disk],moves:[],mounts:[{target:'/data',fstype:'autofs',source:'systemd-1'}]};
    assert.equal(m.configuredDriveState(drive,[disk],snap.mounts),'Idle');
    const row=m.dataDrives(snap)[0];assert.equal(row.pill,'unlocks with OS');assert.equal(row.fix.action,'');assert.match(row.sub,/mounts when first used/);
    const t=m.moveTargets(snap);assert.equal(t.length,1);assert.equal(t[0].free,null);assert.match(m.targetSub(t[0]),/mounts when first used/);
    assert.equal(m.warning(snap),false,'an idle automount is not a warning');
    // Without the autofs row the same unlocked-but-unmounted drive still needs Reconnect.
    assert.equal(m.configuredDriveState(drive,[disk],[]),'Not mounted');
    assert.equal(m.dataDrives({...snap,mounts:[]})[0].fix.action,'reconnect');
});
test('smartmontools install is a fixed Omarchy command in a visible terminal',()=>{
    const source=fs.readFileSync(path.join(repo,'Panel.qml'),'utf8');
    assert.match(source,/execDetached\(\["omarchy-launch-floating-terminal-with-presentation", Model\.SMART_INSTALL\]\)/);
    // No privileged program is ever an argv element the panel executes.
    assert.doesNotMatch(source,/"(pkexec|sudo|pacman|yay)"/);
});
