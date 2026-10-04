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
    "ready":["Ready to move","Planned. The original stays in use until you restart to move it."],
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
function warning(snapshot) {
    var ds=snapshot.drives||[],ms=snapshot.moves||[];
    for(var i=0;i<ds.length;i++)if(configuredDriveState(ds[i],snapshot.disks||[])!=="Mounted")return true;
    for(var j=0;j<ms.length;j++){
        var stage=moveStage(ms[j],snapshot.restartPending);
        if(["attention","restore-on-restart","inspect","interrupted"].indexOf(stage)>=0)return true;
        // A finished move whose bind is missing means the drive is gone.
        if((stage==="moved"||stage==="moved-await-reboot")&&!ms[j].bound)return true;
    }
    return fullest(snapshot.disks||[])>=90;
}
