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
// step is the themed per-notch distance, like the other plugin handlers.
// Touchpad pixel deltas retain their 1:1 movement.
function scroll(y,angle,pixel,content,height,step) {
    return Math.max(0,Math.min(Math.max(0,content-height),y+(pixel?-pixel:-angle/120*step)));
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
    if(view==="system")return [["j/k","move"],["⏎","move folder"],["r","measure again"],["esc","back"]];
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
// ---- drives and folders set up by hand ----------------------------------------
// Same list as PROTECTED in helper/moves.py (a Node test keeps them equal).
var PROTECTED=[".hermes",".claude",".codex",".codemux",".opencode",".config",".ssh",".gnupg",".mozilla",".password-store","keyrings","chromium","google-chrome","firefox","postgres","postgresql","mysql","mariadb"];
// Why a home folder can't be moved, or "" when the helper would consider it.
function moveBlocker(path) {
    var parts=String(path||"").split("/").filter(function(x){return x!=="";});
    if(parts.length<3||parts[0]!=="home")return "outside your home folder";
    for(var i=0;i<parts.length;i++)if(PROTECTED.indexOf(parts[i].toLowerCase())>=0)return "app profile or settings · stays on the OS disk";
    return "";
}
// Folder sizes stream in from `bridge.py --sizes`, one JSON line at a time.
function emptySizes(started){return {started:!!started,home:"",steam:"",entries:[],extras:{},homeBytes:null,done:false,complete:false,error:""};}
function addSizeLine(sizes,line) {
    var o;try{o=JSON.parse(line);}catch(e){return sizes;}
    if(!o||typeof o!=="object")return sizes;
    var s={started:true,home:sizes.home,steam:sizes.steam,entries:sizes.entries,extras:sizes.extras,homeBytes:sizes.homeBytes,done:sizes.done,complete:sizes.complete,error:sizes.error};
    if(typeof o.home==="string"&&o.kind===undefined){s.home=o.home;s.steam=String(o.steam||"");}
    else if(o.kind==="entry"&&typeof o.path==="string"&&typeof o.bytes==="number"&&s.entries.length<512)s.entries=s.entries.concat([{path:o.path,bytes:o.bytes}]);
    else if(o.kind==="home"&&typeof o.bytes==="number")s.homeBytes=o.bytes;
    else if(o.kind==="extra"&&typeof o.path==="string"){var x={};for(var k in s.extras)x[k]=s.extras[k];x[o.path]=typeof o.bytes==="number"?o.bytes:null;s.extras=x;}
    else if(o.done){s.done=true;s.complete=!!o.complete;s.error=display(o.error||"");}
    return s;
}
// The status snapshot with the latest folder sizes attached.
function withSizes(snapshot,sizes) {
    var out={};for(var k in snapshot)out[k]=snapshot[k];
    out.sizes=sizes||emptySizes();return out;
}
function _sizeOf(snapshot,path) {
    var s=snapshot.sizes||{};
    if(s.extras&&Object.prototype.hasOwnProperty.call(s.extras,path))return s.extras[path];
    var e=s.entries||[];for(var i=0;i<e.length;i++)if(e[i].path===path)return e[i].bytes;
    return undefined;
}
// Folders the size scan should measure besides home: hand-made binds on drives.
function sizePaths(snapshot) {
    var out=[];(snapshot.disks||[]).forEach(function(d){manualBinds(snapshot,d).forEach(function(b){if(out.length<16)out.push(b.source);});});
    return out;
}
// Bind mounts from a data disk that Drives did not create (e.g. a hand-made
// fstab line `/data/Projects ~/Projects none bind`). Read from the kernel's
// mount table, so they show exactly what is mounted now.
function manualBinds(snapshot,disk) {
    var mounts=(disk&&disk.mounts)||[],u=_usage(disk,"");
    if(!u||disk.system)return [];
    var main=null;mounts.forEach(function(m){if(m.target===u.target)main=m;});
    if(!main)return [];
    var managed={};(snapshot.moves||[]).forEach(function(m){if(m.bound&&FINISHED.indexOf(m.state)>=0)managed[m.source]=1;});
    var seen={},kept=[];
    function inside(t,p){return t.indexOf(p.replace(/\/$/,"")+"/")===0;}
    // Shortest paths first, so mounts nested inside another bind (or inside
    // the drive itself, e.g. Docker's own layers) are not listed as folders.
    return mounts.slice().sort(function(a,b){return a.target.length-b.target.length;}).filter(function(m){
        if(m.target===main.target||managed[m.target]||seen[m.target]||m.source!==main.source)return false;
        if(inside(m.target,main.target)||kept.some(function(k){return inside(m.target,k);}))return false;
        seen[m.target]=1;kept.push(m.target);return true;
    }).map(function(m){
        var rel=String(m.fsroot||"").replace(/\/+$/,""),base=String(main.fsroot||"/").replace(/\/+$/,"");
        var from=rel.indexOf(base)===0?display(u.target.replace(/\/$/,"")+rel.slice(base.length)):display(rel);
        var b=_sizeOf(snapshot,m.target);
        return {source:m.target,from:from,bytes:typeof b==="number"?b:0,sized:typeof b==="number",bound:true,oldCopy:false,manual:true,destMount:u.target};
    });
}
// What fills the OS disk: top-level folders of home (measured on the OS disk
// only, so folders already on a drive are not counted), biggest first, then
// everything outside home. Hidden folders are app data: shown, not offered.
function spaceRows(snapshot,limit) {
    var s=snapshot.sizes||{},mounted={},planned={},sys=systemDisks(snapshot)[0],steam=s.steam||"";
    visibleMoves(snapshot.moves||[]).forEach(function(m){if(FINISHED.indexOf(m.state)<0)planned[m.source]=1;});
    (snapshot.mounts||[]).forEach(function(m){mounted[m.target]=1;});
    var all=(s.entries||[]).filter(function(e){return !mounted[e.path]&&e.bytes>0;}).sort(function(a,b){return b.bytes-a.bytes;});
    var rows=all.slice(0,limit===0?all.length:(limit||12)).map(function(e,i){
        var name=e.path.split("/").pop(),why=moveBlocker(e.path);
        if(!why&&name.charAt(0)===".")why=steam&&steam.indexOf(e.path+"/")===0?"holds Steam · move the games under Apps":"hidden app folder · stays on the OS disk";
        if(!why&&planned[e.path])why="move already planned";
        return {path:e.path,title:shortPath(e.path),bytes:e.bytes,size:compact(e.bytes),color:i%4,movable:why==="",why:why};
    });
    var home=all.reduce(function(a,e){return a+e.bytes;},0);
    if(sys&&sys.usage&&s.done){
        var rest=Math.max(0,sys.usage.used-(typeof s.homeBytes==="number"?s.homeBytes:home));
        if(rest>0)rows.push({path:"",title:"System, apps & snapshots",bytes:rest,size:compact(rest),color:-1,movable:false,why:"outside your home folder"});
    }
    return rows;
}
// How many home folders were measured, and how many the short list hides.
function spaceCount(snapshot,limit) {
    var s=snapshot.sizes||{},mounted={};(snapshot.mounts||[]).forEach(function(m){mounted[m.target]=1;});
    var n=(s.entries||[]).filter(function(e){return !mounted[e.path]&&e.bytes>0;}).length;
    return {total:n,hidden:Math.max(0,n-(limit||12))};
}
// Progress line while sizes stream in: "Measuring… 14 folders so far · 0:23".
function sizeProgress(sizes,elapsedMs) {
    if(!sizes||!sizes.started)return "";
    var n=(sizes.entries||[]).length,t=Math.max(0,Math.floor((elapsedMs||0)/1000));
    var clock=Math.floor(t/60)+":"+(t%60<10?"0":"")+(t%60);
    if(!sizes.done)return "Measuring your home folder · "+n+(n===1?" folder":" folders")+" so far · "+clock;
    if(sizes.error)return "Couldn't finish measuring: "+sizes.error;
    return sizes.complete?"Measured "+n+" folders in "+clock+". Folders already on a data drive aren't counted.":"Measured "+n+" folders before the time limit; some may be missing. Press r to measure again.";
}
// A folder typed by hand on the OS-disk screen: absolute path, or "" + why not.
function typedFolder(text,home) {
    var p=expandHome(text,home).replace(/\/+$/,"");
    if(p==="")return {path:"",why:""};
    if(p.charAt(0)!=="/")return {path:"",why:"Type a full path, like ~/Videos or /home/you/Games."};
    var why=moveBlocker(p);
    return {path:why?"":p,why:why?shortPath(p)+": "+why:""};
}
// OS-disk meter: one segment per listed home folder.
function systemSegments(snapshot) {
    var sys=systemDisks(snapshot)[0];if(!sys||!sys.usage||!sys.usage.total)return [];
    return spaceRows(snapshot).filter(function(r){return r.color>=0;}).map(function(r){return {color:r.color,fraction:Math.min(1,r.bytes/sys.usage.total)};});
}
// The data drive a path is bind-mounted from, or null when it is on the OS disk.
function _driveMount(d){var u=_usage(d,"");return u?display(u.target):display(d.model);}
function driveFor(snapshot,path) {
    var mounts=snapshot.mounts||[];
    for(var i=0;i<mounts.length;i++)if(mounts[i].target===path){
        var src=mounts[i].source,d=(snapshot.disks||[]).filter(function(x){return !x.system&&(x.mounts||[]).some(function(m){return m.source===src;});})[0];
        if(d)return d;
    }
    return null;
}
// Big app data Omarchy installs: Steam's game library and Docker's data root.
function appRows(snapshot) {
    var out=[],apps=snapshot.apps||{},s=snapshot.sizes||{};
    var steam=apps.steam||s.steam||"";
    if(steam){
        var b=_sizeOf(snapshot,steam),d=driveFor(snapshot,steam);
        out.push({id:"steam",icon:"󰓓",title:"Steam games",path:steam,size:typeof b==="number"?compact(b):"",
            sub:shortPath(steam)+(typeof b==="number"?" · "+compact(b):(s.started&&!s.done?" · measuring…":"")),
            status:d?"● on "+_driveMount(d):"on the OS disk",tone:d?"ok":"dim",movable:!d&&moveBlocker(steam)==="",
            hint:d?"":"Quit Steam, then move the library here. Steam keeps using the same folder."});
    }
    if(apps.docker){
        var dk=driveFor(snapshot,"/var/lib/docker"),db=_sizeOf(snapshot,"/var/lib/docker");
        out.push({id:"docker",icon:"󰡨",title:"Docker",path:"/var/lib/docker",size:typeof db==="number"?compact(db):"",
            sub:"/var/lib/docker"+(typeof db==="number"&&db>0?" · "+compact(db):""),status:dk?"● on "+_driveMount(dk):"on the OS disk",tone:dk?"ok":"dim",movable:false,
            hint:dk?"Bound from the data drive.":"Moving Docker's data from the panel isn't supported yet."});
    }
    return out;
}
// Rows for the DATA section: every configured drive (present or not) and any
// other mounted encrypted data disk. Each row carries its meter segments.
function dataDrives(snapshot) {
    var disks=snapshot.disks||[],drives=snapshot.drives||[],moves=snapshot.moves||[],used={},out=[];
    drives.forEach(function(d){
        var disk=null;disks.forEach(function(x){if(x.byId===d.byId&&x.serial===d.serial&&d.serial)disk=x;});
        if(disk)used[disk.id]=true;
        var state=configuredDriveState(d,disks),pill=_drivePill(state,d),u=_usage(disk,d.mountpoint);
        var folders=movedFolders(d.mountpoint,moves).concat(manualBinds(snapshot,disk));
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
        var u=_usage(x,""),folders=manualBinds(snapshot,x);
        var segs=[];if(u&&u.total>0)folders.forEach(function(f,i){if(f.bytes>0)segs.push({color:i%4,fraction:Math.min(1,f.bytes/u.total)});});
        var other=u&&u.total>0?Math.max(0,u.used/u.total-segs.reduce(function(a,s){return a+s.fraction;},0)):0;
        // Opened at boot from a keyfile in crypttab = the same thing Drives sets up.
        var pill=x.bootUnlock==="keyfile"?"unlocks with OS":(x.bootUnlock==="prompt"?"asks at boot":"opened by hand");
        out.push({key:"disk:"+x.id,drive:null,disk:x,manual:true,title:display(x.model),size:compact(x.size),pill:pill,tone:x.bootUnlock==="keyfile"?"ok":"dim",
            state:"Mounted",usage:u,segments:segs,other:other,sub:u?display(u.target)+" · "+compact(u.used)+" used · "+compact(u.free)+" free · set up by hand":"",
            health:health(x,snapshot.health),fix:{action:"",hint:""},folders:folders,problem:false});
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
    return dataDrives(snapshot).filter(function(r){return r.state==="Mounted"&&(r.drive?r.drive.autoUnlock!==false:targetIssue(r)==="");})
        .map(function(r){return {mountpoint:r.drive?r.drive.mountpoint:r.usage.target,title:r.title,free:r.usage?r.usage.free:0};});
}
// Why folders can't be moved onto a drive set up by hand, or "". The helper
// checks the same things again (helper/moves.py preflight).
function targetIssue(r) {
    if(!r||r.drive)return "";
    var d=r.disk||{},u=r.usage;
    if(!u)return "This drive isn't mounted.";
    if(d.bootUnlock!=="keyfile")return "Folders can only move onto a drive that unlocks with the OS (a keyfile in crypttab).";
    if(u.rootOwned!==true)return "One step first: "+display(u.target)+" belongs to your user, and folders are only moved into a drive folder owned by the system (so no other program can swap things mid-move).";
    return "";
}
// The one issue the panel can fix itself: a user-owned top folder.
function canPrepare(r){return !!r&&!r.drive&&!!r.usage&&r.disk&&r.disk.bootUnlock==="keyfile"&&r.usage.rootOwned!==true;}
// Drives that are mounted but can't take a moved folder, with the reason.
function blockedTargets(snapshot) {
    return dataDrives(snapshot).filter(function(r){return !r.drive&&r.state==="Mounted"&&targetIssue(r)!=="";})
        .map(function(r){return {key:r.key,title:r.title,mount:r.usage?display(r.usage.target):"",mountpoint:r.usage?r.usage.target:"",issue:targetIssue(r),prepare:canPrepare(r)};});
}
// Rows for the MOVED FOLDERS section, colour-matched to their drive's meter.
function folderRows(snapshot) {
    var moves=visibleMoves(snapshot.moves||[]),pending=snapshot.restartPending,idx={},width=_folderWidth(snapshot);
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
// Widest source path over every folder row, so all arrows share one column.
function _folderWidth(snapshot) {
    var w=0,apps={};appRows(snapshot).forEach(function(a){apps[a.path]=1;});
    visibleMoves(snapshot.moves||[]).forEach(function(m){w=Math.max(w,shortPath(m.source).length);});
    (snapshot.disks||[]).forEach(function(d){manualBinds(snapshot,d).forEach(function(b){if(!apps[b.source])w=Math.max(w,shortPath(b.source).length);});});
    return Math.min(w,28);
}
// Folders on drives that were bound by hand, for the MOVED FOLDERS list.
function manualFolderRows(snapshot) {
    var out=[],width=_folderWidth(snapshot),apps={};
    appRows(snapshot).forEach(function(a){apps[a.path]=1;});
    // App data (Docker, Steam) is listed once, under APPS.
    (snapshot.disks||[]).forEach(function(d){manualBinds(snapshot,d).forEach(function(b){if(!apps[b.source]){b.drive=d;out.push(b);}});});
    return out.map(function(b,i){
        var src=shortPath(b.source),pad=src+new Array(Math.max(0,Math.min(width,28)-src.length)+1).join(" ");
        return {id:"manual:"+b.source,path:b.source,diskId:b.drive.id,source:src,label:pad+" → "+b.from,from:b.from,size:b.sized?compact(b.bytes):"",
            status:"● mounted",tone:"ok",color:i%4,manual:true};
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
