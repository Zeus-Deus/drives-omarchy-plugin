function display(value) {
    return String(value || "").replace(/[\r\n\t\u2028\u2029]+/g, " ").replace(/[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u206f]/g, "").replace(/\s+/g," ").trim().slice(0,240);
}
function bytes(n) {
    n=Number(n)||0;var units=["B","KiB","MiB","GiB","TiB"], i=0;
    while(n>=1024 && i<4){n/=1024;i++;}
    return (i===0?Math.round(n):n.toFixed(1))+" "+units[i];
}
function canProvision(disk,fragment) {
    return !!disk && disk.selectable===true && !disk.system && String(disk.serial).length>=4 && fragment===String(disk.serial).slice(-4);
}
function scroll(y,angle,pixel,content,height,pitch) {
    return Math.max(0,Math.min(Math.max(0,content-height),y+(pixel?-pixel:-angle/120*pitch*3)));
}
function fullest(disks) {
    var max=0;for(var i=0;i<disks.length;i++)if(!disks[i].system)for(var j=0;j<(disks[i].usage||[]).length;j++)max=Math.max(max,disks[i].usage[j].percent||0);
    return max;
}
function boundedArgv(argv) {
    var script = 'o=$1; shift; set -o pipefail; "$@" 2>/dev/null | { head -c "$o"; [ "$(head -c 1 | wc -c)" -eq 0 ] || exit 90; }';
    return ["/usr/bin/bash","-c",script,"drives-bound","2097152"].concat(argv);
}
function configuredDriveState(drive,disks) {
    if(drive.state!=="ready")return "Unfinished setup";
    if(!drive.byId || !drive.serial || !drive.mountpoint)return "Inspect identity";
    var matches=disks.filter(function(d){return d.byId===drive.byId && d.serial===drive.serial;});
    if(matches.length===0)return "Missing drive";
    if(matches.length!==1)return "Inspect identity";
    var disk=matches[0];
    if(disk.system || disk.state==="unsupported" || disk.encrypted!==true)return "Inspect identity";
    if(disk.state==="locked")return "Locked";
    var mounts=disk.mounts||[];
    for(var i=0;i<mounts.length;i++)if(mounts[i].target===drive.mountpoint && mounts[i].fstype==="btrfs") {
        var options=String(mounts[i].options||"").split(",");
        return options.indexOf("rw")>=0 && options.indexOf("ro")<0 ? "Mounted" : "Read-only";
    }
    return "Not mounted";
}
// What the panel can do for a configured drive that is not fully available.
// Moved folders on it count: a mounted drive whose binds are down still needs
// reconnecting (e.g. it was unplugged and plugged back in).
function driveFix(drive,disks,moves) {
    var state=configuredDriveState(drive,disks);
    var down=(moves||[]).some(function(m){return m.destMount===drive.mountpoint&&["switched","rebooted","cleaning","cleaned"].indexOf(m.state)>=0&&!m.bound;});
    if(state==="Missing drive")return {action:"",hint:"Plug the drive back in. Folders moved onto it stay locked, so nothing is written to the OS disk."};
    if(state==="Locked"&&drive.keyfilePresent===false)return {action:"recover",hint:"This computer no longer has the drive's key (reinstall or new machine). Unlock it with the recovery passphrase."};
    if(state==="Locked"||state==="Not mounted"||(state==="Mounted"&&down))return {action:"reconnect",hint:"The drive is connected but not in use. Reconnect it with its own key; no passphrase needed."};
    return {action:"",hint:""};
}
// The ready Drives record for a present disk (exact by-id + serial), or null.
function configuredFor(disk,drives) {
    var m=(drives||[]).filter(function(d){return d.state==="ready"&&d.byId===disk.byId&&d.serial===disk.serial&&!!d.serial;});
    return m.length===1?m[0]:null;
}
// Folder moves run during a restart (helper/offline.py). These two helpers are
// the only place the panel decides what a move row says and offers.
function moveStage(move,pending) {
    var mine=pending && pending.valid && pending.moveId===move.id;
    if(mine)return pending.action==="rollback"?"undo-on-restart":"move-on-restart";
    if(move.needsAttention)return "attention";
    var s=move.state, was=move.interruptedState;
    if(s==="awaiting-maintenance"||(s==="paused"&&was==="awaiting-maintenance"))return move.restoredFrom?"interrupted":"ready";
    if(s==="paused"&&["quarantining","copying","verifying","switching","rolling-back"].indexOf(was)>=0)return "restore-on-restart";
    if(s==="paused")return "inspect";
    if(s==="switched")return "moved-await-reboot";
    if(s==="rebooted")return "moved";
    if(s==="rolled-back")return "undone";
    if(s==="cleaned")return "cleaned";
    return "inspect";
}
var MOVE_TEXT={
    "move-on-restart":["Moves on next restart","Save your work. During the restart nothing else runs: the folder is copied, every file is checked, and the familiar path then opens the drive."],
    "undo-on-restart":["Undo on next restart","During the restart the original folder is put back and the copy on the drive is removed."],
    "attention":["Needs attention","The move stopped safely. Nothing was deleted; both copies are kept."],
    "ready":["Ready to move","Checked and planned. Schedule it for the next restart; until then the original stays in use."],
    "interrupted":["Interrupted · original restored","The move was cut off, so your original folder was put back unchanged. Restart to try again."],
    "restore-on-restart":["Interrupted","The next maintenance restart puts your original folder back before anything else."],
    "inspect":["Needs inspection","An older or unusual move. Both copies are kept; nothing happens automatically."],
    "moved-await-reboot":["Moved","Files now live on the drive. The old copy can be deleted after one normal restart."],
    "moved":["Moved","Files live on the drive and open through the familiar path. The old copy is kept until you delete it."],
    "undone":["Undone","The original folder is back in place."],
    "cleaned":["Moved · old copy deleted","Snapshots may still hold the old data for a while."]
};
function moveText(move,pending){return MOVE_TEXT[moveStage(move,pending)];}
// Undone moves are history. Of several moves of one folder, show only the
// newest; the folder can only be in one place.
function visibleMoves(moves) {
    var latest={};
    for(var i=0;i<moves.length;i++){var m=moves[i];if(!latest[m.source]||(m.created||0)>(latest[m.source].created||0))latest[m.source]=m;}
    return moves.filter(function(m){return latest[m.source]===m && m.state!=="rolled-back";});
}
function moveActions(move,pending) {
    var stage=moveStage(move,pending), other=pending && pending.moveId && pending.moveId!==move.id;
    var a={"move-on-restart":["restart","cancel_restart"],"undo-on-restart":["restart","cancel_restart"],
        "ready":["resume_move","cancel_move"],"interrupted":["resume_move","cancel_move"],"restore-on-restart":["resume_move"],
        "moved-await-reboot":["rollback_move"],"moved":["rollback_move"],"attention":["resume_move"]}[stage]||[];
    if(stage==="moved" && move.canDelete===true)a=a.concat(["delete_old_copy"]);
    if(other)a=a.filter(function(x){return x==="cancel_move"||x==="delete_old_copy";});
    if(pending && pending.moveId===move.id && pending.thisSession===false)a=a.filter(function(x){return x!=="cancel_restart";});
    return a;
}
// SMART verdict for a disk from the helper's cached health report.
function health(disk,report) {
    var all=(report&&report.disks)||{};
    var h=all[disk.serial]||all["*"];
    if(!h)return {state:"unknown",label:"Health: checking…",reason:""};
    var label={passed:"Health: OK",warning:"Health: warning",failing:"Health: FAILING",unavailable:"Health: not available"}[h.state]||"Health: unknown";
    if(h.temperature)label+=" · "+h.temperature+"°C";
    return {state:h.state,label:label,reason:display(h.reason)};
}
// Read/write rates (bytes/s) per disk name from two status snapshots.
function ioRates(prev,cur) {
    var out={};
    if(!prev||!cur||!prev.sampledAt||!cur.sampledAt)return out;
    var dt=(cur.sampledAt-prev.sampledAt)/1000;
    if(!(dt>0.2))return out;
    var before={};(prev.disks||[]).forEach(function(d){if(d.io)before[d.name]=d.io;});
    (cur.disks||[]).forEach(function(d){
        var a=before[d.name],b=d.io;
        if(a&&b&&b.read>=a.read&&b.written>=a.written)out[d.name]={read:(b.read-a.read)/dt,write:(b.written-a.written)/dt};
    });
    return out;
}
// Key hints for the current view; only keys that actually do something there.
function footerHints(view) {
    if(view==="overview")return [["j/k","move"],["⏎","open"],["a","add drive"],["m","move folder"],["esc","close"]];
    if(view==="progress")return [["esc","back"]];
    return [["j/k","move"],["⏎","choose"],["esc","back"]];
}
// ---- Layout model for the panel (mockups A–F) -------------------------------
// Short sizes like the mockups: "350 G", "3.4 T", "56 M".
function compact(n) {
    n=Number(n)||0;var u=["B","K","M","G","T"],i=0;
    while(n>=1024&&i<4){n/=1024;i++;}
    return (n>=100||i===0?Math.round(n):n.toFixed(1).replace(/\.0$/,""))+" "+u[i];
}
// Home paths shown the way people type them.
function shortPath(p){return display(String(p||"").replace(/^\/home\/[^\/]+(?=\/|$)/,"~"));}
var FINISHED=["switched","rebooted","cleaning","cleaned"];
function _usage(disk,target){var u=(disk&&disk.usage)||[];for(var i=0;i<u.length;i++)if(u[i].target===target)return u[i];return u.length?u[0]:null;}
function _drivePill(state,drive) {
    if(state==="Mounted")return [drive.autoUnlock===false?"asks every time":"unlocks with OS","ok"];
    if(state==="Missing drive")return ["not connected","bad"];
    if(state==="Locked")return ["locked","bad"];
    if(state==="Not mounted")return ["not in use","bad"];
    if(state==="Read-only")return ["read-only","bad"];
    if(state==="Unfinished setup")return ["setup unfinished","bad"];
    return ["check identity","bad"];
}
// Rows for the DATA section: every configured drive (present or not) and any
// other mounted encrypted data disk. Each row carries its meter segments.
function dataDrives(snapshot) {
    var disks=snapshot.disks||[],drives=snapshot.drives||[],moves=snapshot.moves||[],used={},out=[];
    drives.forEach(function(d){
        var disk=null;disks.forEach(function(x){if(x.byId===d.byId&&x.serial===d.serial&&d.serial)disk=x;});
        if(disk)used[disk.id]=true;
        var state=configuredDriveState(d,disks),pill=_drivePill(state,d),u=_usage(disk,d.mountpoint);
        var folders=movedFolders(d.mountpoint,moves);
        var segs=[];if(u&&u.total>0)folders.forEach(function(f,i){segs.push({color:i%4,fraction:Math.min(1,f.bytes/u.total)});});
        var other=u&&u.total>0?Math.max(0,u.used/u.total-segs.reduce(function(a,s){return a+s.fraction;},0)):0;
        var h=disk?health(disk,snapshot.health):{state:"unknown",label:"",reason:""};
        var fix=driveFix(d,disks,moves);
        out.push({key:"drive:"+d.id,drive:d,disk:disk,title:disk?display(disk.model):"Drive “"+display(d.name)+"”",
            size:disk?compact(disk.size):"",pill:pill[0],tone:pill[1],state:state,usage:u,segments:segs,other:other,
            sub:u?display(d.mountpoint)+" · "+compact(u.used)+" used · "+compact(u.free)+" free · zstd":display(d.mountpoint)+" · "+(fix.hint||state),
            health:h,fix:fix,folders:folders,problem:pill[1]==="bad"||fix.action!==""||h.state==="failing"||h.state==="warning"||folders.some(function(f){return !f.bound;})});
    });
    disks.forEach(function(x){
        if(used[x.id]||x.system||!x.encrypted||!(x.mounts||[]).length)return;
        var u=_usage(x,"");
        out.push({key:"disk:"+x.id,drive:null,disk:x,title:display(x.model),size:compact(x.size),pill:"LUKS · not set up by Drives",tone:"dim",
            state:"Mounted",usage:u,segments:[],other:u&&u.total?u.used/u.total:0,sub:u?display(u.target)+" · "+compact(u.used)+" used · "+compact(u.free)+" free":"",
            health:health(x,snapshot.health),fix:{action:"",hint:""},folders:[],problem:false});
    });
    return out;
}
// The OS disk row(s).
function systemDisks(snapshot) {
    return (snapshot.disks||[]).filter(function(x){return x.system;}).map(function(x){
        var u=_usage(x,"/");
        return {key:"sys:"+x.id,disk:x,title:display(x.model),size:compact(x.size),pill:x.encrypted?"LUKS2 · boot":"not encrypted",tone:x.encrypted?"ok":"bad",
            usage:u,other:u&&u.total?u.used/u.total:0,sub:u?"/ · "+compact(u.used)+" used · "+compact(u.free)+" free":"",health:health(x,snapshot.health)};
    });
}
// Disks the add-drive wizard can offer, with why the others cannot be chosen.
function addCandidates(snapshot) {
    return (snapshot.disks||[]).map(function(x){
        var reason=x.system?"system disk · can't be selected"
            :(x.mounts||[]).length?"in use · "+display(x.mounts[0].target)+" · can't be selected"
            :x.state==="locked"?"encrypted disk in use elsewhere · can't be selected"
            :x.state==="unsupported"?"unsupported layout · can't be selected"
            :!x.selectable?"no stable ID or serial · can't be selected":"";
        return {id:x.id,disk:x,title:display(x.model),selectable:reason==="",
            sub:reason||((x.transport||"disk").toUpperCase()+" · "+compact(x.size)+" · serial …"+display(String(x.serial).slice(-4))+(x.state==="new"?" · no filesystem in use":""))};
    }).sort(function(a,b){return (b.selectable?1:0)-(a.selectable?1:0);});
}
function newDisks(snapshot){return addCandidates(snapshot).filter(function(c){return c.selectable;});}
// First free /data, /data2, … for a new drive, with its mapper name.
function suggestMount(snapshot) {
    var taken={};(snapshot.drives||[]).forEach(function(d){taken[d.mountpoint]=1;taken["n:"+d.name]=1;});
    (snapshot.mounts||[]).forEach(function(m){taken[m.target]=1;});
    for(var i=1;i<100;i++){var p=i===1?"/data":"/data"+i,n=i===1?"data":"data"+i;if(!taken[p]&&!taken["n:"+n])return {mountpoint:p,name:n};}
    return {mountpoint:"/data99",name:"data99"};
}
// Mounted Drives destinations a folder can move to.
function moveTargets(snapshot) {
    return dataDrives(snapshot).filter(function(r){return r.drive&&r.state==="Mounted"&&r.drive.autoUnlock!==false;})
        .map(function(r){return {mountpoint:r.drive.mountpoint,title:r.title,free:r.usage?r.usage.free:0};});
}
// Rows for the MOVED FOLDERS section, colour-matched to their drive's meter.
function folderRows(snapshot) {
    var moves=visibleMoves(snapshot.moves||[]),pending=snapshot.restartPending,idx={},width=0;
    moves.forEach(function(m){width=Math.max(width,shortPath(m.source).length);});
    dataDrives(snapshot).forEach(function(r){(r.folders||[]).forEach(function(f,i){idx[f.source+"\u0000"+(r.drive&&r.drive.mountpoint)]=i%4;});});
    return moves.map(function(m){
        var stage=moveStage(m,pending),c=idx[m.source+"\u0000"+m.destMount];
        var status=({"moved":m.bound?"● mounted":"● not reachable","moved-await-reboot":m.bound?"● mounted":"● not reachable","cleaned":m.bound?"● mounted":"● not reachable",
            "move-on-restart":"moves on restart","undo-on-restart":"undo on restart","ready":"ready to move","interrupted":"interrupted","restore-on-restart":"interrupted",
            "attention":"needs attention","inspect":"needs inspection","undone":"undone"})[stage]||stage;
        var tone=status==="● mounted"?"ok":(["● not reachable","needs attention","needs inspection","interrupted"].indexOf(status)>=0?"bad":"dim");
        // The font is monospaced: padding the source lines up the arrows.
        var src=shortPath(m.source),pad=src+new Array(Math.max(0,Math.min(width,28)-src.length)+1).join(" ");
        return {id:m.id,move:m,source:src,label:pad+" → "+display(m.destMount),dest:display(m.destMount),status:status,tone:tone,color:c===undefined?-1:c};
    });
}
// Flat keyboard order of the overview: data drives, new disks, folders.
function overviewCursor(snapshot) {
    var out=[];
    dataDrives(snapshot).forEach(function(r){out.push({kind:"drive",key:r.key});});
    newDisks(snapshot).forEach(function(c){out.push({kind:"new",key:"new:"+c.id,id:c.id});});
    folderRows(snapshot).forEach(function(f){out.push({kind:"move",key:"move:"+f.id,id:f.id});});
    return out;
}
// A missing drive gets its own card at the top (mock D).
function alerts(snapshot) {
    return dataDrives(snapshot).filter(function(r){return r.drive&&(r.state==="Missing drive"||r.fix.action!==""||r.folders.some(function(f){return !f.bound;}));}).map(function(r){
        var lost=r.folders.filter(function(f){return !f.bound;});
        var title=r.state==="Missing drive"?r.title+" isn't connected":r.state==="Locked"?r.title+" is locked":r.title+" isn't in use";
        var sub=(r.state==="Missing drive"?"Not detected":"Connected but not opened")+(lost.length?" · "+lost.length+(lost.length===1?" folder can't":" folders can't")+" be reached":"");
        return {key:r.key,title:title,sub:sub,folders:lost.map(function(f){return shortPath(f.source);}),fix:r.fix,drive:r.drive};
    });
}
// Overview subtitle, e.g. "3 drives · 40 G total · all encrypted".
function overviewMeta(snapshot) {
    var d=(snapshot.disks||[]).filter(function(x){return x.system||!!x.encrypted;});
    var total=d.reduce(function(a,x){return a+(x.size||0);},0);
    var all=d.length>0&&d.every(function(x){return x.encrypted;});
    return d.length+(d.length===1?" drive":" drives")+" · "+compact(total)+" · "+(all?"all encrypted":(snapshot.rootEncrypted?"some unencrypted":"OS unencrypted"));
}
// Total size of the data drives that are present, for the DATA header.
function dataTotal(snapshot){return compact(dataDrives(snapshot).reduce(function(a,r){return a+((r.disk&&r.disk.size)||0);},0));}
// Hero meta for one move: "Moved · on /data", "Ready to move · to /data2".
function moveMeta(m,pending) {
    var stage=moveStage(m,pending),done=["moved","moved-await-reboot","cleaned"].indexOf(stage)>=0;
    return moveText(m,pending)[0]+" · "+(done?"on ":"to ")+display(m.destMount);
}
// Facts for a move that has not happened yet: size, files, room on the drive.
function moveFacts(m,snapshot) {
    if(FINISHED.indexOf(m.state)>=0||!m.stats)return "";
    var free=null;dataDrives(snapshot||{}).forEach(function(r){if(r.drive&&r.drive.mountpoint===m.destMount&&r.usage)free=r.usage.free;});
    return compact(m.stats.bytes||0)+" · "+(m.stats.files||0).toLocaleString()+" files"+(free===null?"":" · "+compact(free)+" free on "+display(m.destMount));
}
// What the move detail screen lists as proof (mock F).
function moveChecks(m) {
    if(FINISHED.indexOf(m.state)<0)return [];
    var files=(m.verification&&m.verification.files)||(m.stats&&m.stats.files)||0;
    return [
        {ok:!!m.verified,text:m.verified?"All "+files.toLocaleString()+" files matched at switch-over":"Not verified"},
        {ok:!!m.bound,text:m.bound?"Opens through "+shortPath(m.source)+" now":"Not reachable right now (drive missing or locked)"},
        {ok:m.state==="rebooted"||m.state==="cleaned"||m.state==="cleaning",pending:m.state==="switched",text:m.state==="switched"?"Restart once to confirm it comes back":"Still mounted after a restart"}
    ];
}
// Human words for a helper job.
function jobText(job) {
    if(!job)return {title:"Waiting for the helper…",state:"running"};
    var names={ProvisionDrive:"Setting up the drive",ResumeDrive:"Finishing drive setup",UnlockDrive:"Unlocking the drive",ReconnectDrive:"Reconnecting the drive",
        StartMove:"Planning the move",ResumeMove:"Scheduling the move",RollbackMove:"Scheduling the undo",CancelRestart:"Withdrawing the restart request",
        CancelMove:"Cancelling the move",RestartMove:"Starting over",DeleteOldCopy:"Deleting the old copy",ExportHeaderBackup:"Exporting the header backup"};
    var t=names[job.method]||display(job.method);
    if(job.state==="done")t=t.replace(/^(\w+)ing/,function(m,w){return {Setting:"Set",Finishing:"Finished",Unlocking:"Unlocked",Reconnecting:"Reconnected",Planning:"Planned",Scheduling:"Scheduled",Withdrawing:"Withdrew",Cancelling:"Cancelled",Starting:"Started",Deleting:"Deleted",Exporting:"Exported"}[w]+"";});
    return {title:t,state:job.state,error:display(job.error)};
}
// A refusal that only said the drive was missing is stale once that drive is
// mounted again; any other error (or attention state) is always shown.
function showMoveError(m,snapshot) {
    if(!m||!m.error)return false;
    if(m.needsAttention||!/not connected/.test(m.error))return true;
    return !dataDrives(snapshot||{}).some(function(r){return r.drive&&r.drive.mountpoint===m.destMount&&r.state==="Mounted";});
}
// Turn "~/x" into an absolute path under the user's home.
function expandHome(p,home){p=String(p||"").trim();return p==="~"?home:(p.indexOf("~/")===0?home+p.slice(1):p);}
function rate(n){return bytes(n)+"/s";}
function ioText(r){return r?"Read "+rate(r.read)+" · Write "+rate(r.write):"";}
// Folders moved onto a mounted drive, with their size when moved.
// Sizes come from the move record (apparent bytes at move time), not a live du.
function movedFolders(target,moves) {
    return (moves||[]).filter(function(m){return m.destMount===target&&["switched","rebooted","cleaning","cleaned"].indexOf(m.state)>=0;})
        .map(function(m){return {source:m.source,bytes:(m.stats&&m.stats.bytes)||0,bound:!!m.bound,oldCopy:m.state!=="cleaned"};});
}
// Old copies still kept on the OS disk (deleted only by an explicit step).
function reclaimable(moves) {
    var n=0;(moves||[]).forEach(function(m){if(m.state==="switched"||m.state==="rebooted")n+=(m.stats&&m.stats.bytes)||0;});
    return n;
}
function warning(snapshot) {
    var ds=snapshot.drives||[],ms=snapshot.moves||[];
    var disks=snapshot.disks||[];
    for(var k=0;k<disks.length;k++){var h=health(disks[k],snapshot.health).state;if(h==="failing"||h==="warning")return true;}
    for(var i=0;i<ds.length;i++)if(configuredDriveState(ds[i],snapshot.disks||[])!=="Mounted")return true;
    for(var j=0;j<ms.length;j++){
        var stage=moveStage(ms[j],snapshot.restartPending);
        if(["attention","restore-on-restart","inspect","interrupted"].indexOf(stage)>=0)return true;
        // A finished move whose bind is missing means the drive is gone.
        if((stage==="moved"||stage==="moved-await-reboot")&&!ms[j].bound)return true;
    }
    return fullest(snapshot.disks||[])>=90;
}
