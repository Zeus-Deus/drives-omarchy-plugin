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
function warning(snapshot) {
    var ds=snapshot.drives||[],ms=snapshot.moves||[];
    for(var i=0;i<ds.length;i++)if(configuredDriveState(ds[i],snapshot.disks||[])!=="Mounted")return true;
    for(var j=0;j<ms.length;j++)if(ms[j].state==="paused"||(!ms[j].bound&&ms[j].oldCopyAvailable))return true;
    return fullest(snapshot.disks||[])>=90;
}
