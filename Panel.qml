pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

BarWidget {
    id: root
    moduleName: "io.github.zeus-deus.drives"
    property string view: "overview"
    property string selectedDiskId: ""
    property string selectedMoveId: ""
    property int cursor: 0
    property var confirmationAction: null
    readonly property bool opened: controller.open
    readonly property bool popoutSwitchClosing: controller.popoutSwitchClosing
    readonly property var storage: service.snapshot
    readonly property var disks: storage.disks || []
    readonly property var moves: Model.visibleMoves(storage.moves || [])
    readonly property var configuredDrives: storage.drives || []
    readonly property var chosenDisk: {
        for (var i=0;i<disks.length;i++) if(disks[i].id===selectedDiskId)return disks[i];
        return null;
    }
    readonly property var chosenMove: {
        for(var i=0;i<moves.length;i++)if(moves[i].id===selectedMoveId)return moves[i];
        return null;
    }
    readonly property var lastJob: (storage.jobs || []).length ? storage.jobs[storage.jobs.length-1] : null
    readonly property string jobError: lastJob && lastJob.state==="failed" ? Model.display(lastJob.error) : ""
    readonly property color ink: bar ? bar.foreground : Color.foreground
    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    function open() { controller.open=true; service.opened=true; }
    function close() { confirm.opened=false;controller.open=false;service.opened=false; }
    function closeForPopoutSwitch() { controller.popoutSwitchClosing=true;close();Qt.callLater(function(){controller.popoutSwitchClosing=false;}); }
    function go(where) { view=where;cursor=0;flick.contentY=0;catcher.forceActiveFocus(); }
    function ask(message,action) { confirmationAction=action;confirm.message=message;confirm.selectedIndex=0;confirm.opened=true;confirm.forceActiveFocus(); }
    function selectDisk(disk) { selectedDiskId=disk.id;go(disk.state==="new"?"add":"manage"); }
    function selectMove(move) {selectedMoveId=move.id;go("resume");}
    onChosenDiskChanged: if(view==="add" && chosenDisk && !chosenDisk.selectable) go("manage")
    function moveCursor(dy) {
        var n=view==="overview"?disks.length+moves.length:disks.length;
        cursor=Math.max(0,Math.min(Math.max(0,n-1),cursor+dy));
        var row=diskRows.itemAt(cursor);
        if(view==="overview" && cursor>=disks.length)row=moveRows.itemAt(cursor-disks.length);
        if(row)ensureVisible(row);
    }
    function ensureVisible(item) {
        var p=item.mapToItem(content,0,0);
        if(p.y<flick.contentY)flick.contentY=p.y;
        else if(p.y+item.height>flick.contentY+flick.height)flick.contentY=Math.min(flick.contentHeight-flick.height,p.y+item.height-flick.height);
    }
    function activate() {
        if(view==="overview") {
            if(cursor<disks.length)selectDisk(disks[cursor]);
            else if(cursor-disks.length<moves.length)selectMove(moves[cursor-disks.length]);
        }
    }
    function provision() {
        if(!Model.canProvision(chosenDisk,serialField.text))return;
        if(!storage.helperAvailable){service.error="Install the storage helper first.";return;}
        var args=["provision","--by-id",chosenDisk.byId,"--serial",chosenDisk.serial,"--name",nameField.text,"--mountpoint",mountField.text,"--confirmation",serialField.text];
        if(eraseToggle.checked)args.push("--erase");
        if(!autoToggle.checked)args.push("--manual");
        service.launch(args);
    }
    readonly property var pendingRestart: storage.restartPending || null
    function startMove() {
        var src=sourceField.text,dest=destinationField.text;
        ask("Plan moving "+Model.display(src)+" to "+Model.display(dest)+"?\nNothing changes yet. The move itself happens during a restart, when nothing else is using the folder. Apps keep using the same path afterwards.",function(){service.submit({op:"start_move",src:src,destMount:dest});go("progress");});
    }
    function restartNow() {
        ask("Restart now?\nOpen windows are closed. The restart takes a little longer while the folder is handled, then your desktop comes back.",function(){close();Quickshell.execDetached(["omarchy-system-reboot"]);});
    }
    function action(op) {
        if(!chosenMove)return;
        var id=chosenMove.id;
        if(op==="restart")return restartNow();
        var words={resume_move:"Move this folder on the next restart?",rollback_move:"Undo this move on the next restart? It is refused if files changed after the move, so no work is lost.",delete_old_copy:"Delete the old copy? Undo will no longer be possible. Btrfs snapshots may retain the data; reclaimed space is not guaranteed.",cancel_move:"Cancel this move? The copy on the drive is removed and the original stays in use.",cancel_restart:"Keep the folder as it is and skip this on the next restart?"};
        if(words[op])ask(words[op],function(){service.submit({op:op,id:id});});
        else service.submit({op:op,id:id});
    }
    readonly property var actionLabels: ({resume_move:"Move on next restart…",restart:"Restart now…",cancel_restart:"Don't do it on restart",rollback_move:"Undo move…",delete_old_copy:"Delete old copy…",cancel_move:"Cancel move…"})
    Service { id:service; onLaunching:root.close(); onFinished:function(result){if(result.jobId)root.go("progress");} }
    WidgetButton {
        id:button
        anchors.fill:parent
        bar:root.bar
        text: "󰋊" + (Model.warning(root.storage)?" !":(!root.vertical && root.setting("showUsage",true)?" "+Model.fullest(root.disks)+"%":""))
        foreground:Model.warning(root.storage)?Color.urgent:root.ink
        onPressed: function(b){if(root.opened)root.close();else root.open();}
    }
    KeyboardPanel {
        id:controller
        anchorItem:button
        bar:root.bar
        owner:root
        focusTarget:catcher
        contentWidth:fittedContentWidth(Style.space(490))
        contentHeight:fittedContentHeight(content.implicitHeight,Style.space(660))
        PanelKeyCatcher {
            id:catcher
            anchors.fill:parent
            blocked:confirm.opened || sourceField.activeFocus || destinationField.activeFocus || serialField.activeFocus || nameField.activeFocus || mountField.activeFocus || exportField.activeFocus
            onCloseRequested: {if(root.view!=="overview")root.go("overview");else root.close();}
            onMoveRequested:function(dx,dy){root.moveCursor(dy);}
            onActivateRequested:root.activate()
            onTextKey:function(t){if(t==="r")service.refresh();else if(t==="a")root.go("add");else if(t==="m")root.go("move");}
            Flickable {
                id:flick
                anchors.fill:parent
                clip:true
                contentWidth:width
                contentHeight:content.implicitHeight
                boundsBehavior:Flickable.StopAtBounds
                WheelHandler {
                    onWheel:function(event){flick.contentY=Model.scroll(flick.contentY,event.angleDelta.y,event.pixelDelta.y,flick.contentHeight,flick.height,Style.spacing.popupRowHeight);event.accepted=true;}
                }
                Column {
                    id:content
                    width:flick.width
                    spacing:Style.spacing.md
                    PanelHero {
                        title:root.view==="overview"?"Drives":({add:"Add drive",move:"Move folder",manage:"Manage drive",resume:"Move & recovery",progress:"Storage operation"}[root.view]||"Drives")
                        meta:root.view==="overview"?root.disks.length+" drives · "+(root.storage.rootEncrypted?"OS encrypted":"OS root is not encrypted"):(root.chosenDisk?Model.display(root.chosenDisk.model):"your paths stay the same")
                        iconComponent:Component { Text { text:"󰋊";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.display; } }
                        trailingControl:Component { Button { text:root.view==="overview"?"Rescan":"Back";onClicked:{if(root.view==="overview"){service.error="";service.refresh();}else root.go("overview");} } }
                    }
                    Text {
                        width:content.width;visible:service.error!==""||root.jobError!==""
                        text:service.error||root.jobError;color:Color.urgent;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText
                    }
                    Text {
                        width:content.width;visible:!root.storage.helperAvailable
                        text:"Read-only overview. Install the system helper explicitly to enable provisioning and folder moves; the plugin never elevates itself.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText
                    }
                    Text {
                        width:content.width;visible:root.storage.testFixtureMode===true
                        text:"VM test fixture: the OS disk is not encrypted, so this does not prove the keyfile is protected.";color:Color.urgent;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText
                    }
                    Column {
                        width:content.width;spacing:Style.spacing.sm;visible:root.view==="overview"||root.view==="add"
                        PanelSectionHeader { text:root.view==="add"?"CHOOSE THE EXACT DISK":"SYSTEM & DATA" }
                        Repeater {
                            id:diskRows
                            model:root.disks.length
                            Column {
                                id: diskColumn
                                required property int index
                                property var disk:root.disks[index]||({})
                                width:content.width;spacing:Style.spacing.xs
                                Button {
                                    width:content.width;leftAlign:true;hasCursor:root.cursor===index
                                    text:Model.display(parent.disk.model)+" · "+Model.bytes(parent.disk.size)+"\n"+(parent.disk.system?"System · ":"")+Model.display(parent.disk.state)+" · "+(parent.disk.encrypted?"encrypted":"not encrypted")+(parent.disk.serial?" · …"+Model.display(parent.disk.serial.slice(-4)):"")
                                    enabled:root.view!=="add"||parent.disk.selectable===true
                                    onClicked: {root.selectedDiskId=parent.disk.id;if(root.view!=="add")root.selectDisk(parent.disk);}
                                }
                                Repeater {
                                    model:(diskColumn.disk.usage||[]).length
                                    Column {
                                        id:usageColumn
                                        required property int index
                                        property var u:diskColumn.disk.usage[index]||({})
                                        property var folders:Model.movedFolders(u.target,root.storage.moves||[])
                                        width:content.width;spacing:0
                                        Text {
                                            width:content.width;text:Model.display(usageColumn.u.target)+" · "+usageColumn.u.percent+"% used · "+Model.bytes(usageColumn.u.free)+" free"
                                            font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;color:usageColumn.u.percent>=90?Color.urgent:root.ink;textFormat:Text.PlainText
                                        }
                                        Repeater {
                                            model:usageColumn.folders.length
                                            Text {
                                                required property int index
                                                property var f:usageColumn.folders[index]||({})
                                                width:content.width;leftPadding:Style.spacing.md;elide:Text.ElideMiddle
                                                text:"↳ "+Model.display(f.source)+" · "+Model.bytes(f.bytes)+(f.bound?"":" · not reachable")
                                                font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;color:f.bound?root.ink:Color.urgent;opacity:f.bound?0.75:1;textFormat:Text.PlainText
                                            }
                                        }
                                    }
                                }
                                Text {
                                    readonly property var h:Model.health(diskColumn.disk,root.storage.health)
                                    readonly property string io:Model.ioText(service.rates[diskColumn.disk.name])
                                    width:content.width;visible:root.view==="overview";opacity:h.state==="failing"||h.state==="warning"?1:0.6
                                    text:h.label+(io?" · "+io:"")
                                    font.family:Style.font.family;font.pixelSize:Style.font.caption;color:h.state==="failing"||h.state==="warning"?Color.urgent:root.ink;elide:Text.ElideRight;textFormat:Text.PlainText
                                }
                            }
                        }
                        Text {width:content.width;visible:service.loaded && root.disks.length===0;text:"No supported disks detected. Network, RAID, LVM and loop topologies are not guessed.";font.family:Style.font.family;font.pixelSize:Style.font.body;color:root.ink;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                    }
                    Column {
                        width:content.width;spacing:Style.spacing.sm;visible:root.view==="overview" && root.configuredDrives.length>0
                        PanelSectionHeader {text:"CONFIGURED DATA DRIVES"}
                        Repeater {
                            model:root.configuredDrives.length
                            Column {
                                id:driveRow
                                required property int index
                                property var drive:root.configuredDrives[index]||({})
                                readonly property string observedState:Model.configuredDriveState(drive,root.disks)
                                readonly property var fix:Model.driveFix(drive,root.disks,root.storage.moves||[])
                                width:content.width;spacing:Style.spacing.xs
                                Text {
                                    width:content.width;text:Model.display(driveRow.drive.name)+" · "+driveRow.observedState+"\n"+Model.display(driveRow.drive.mountpoint)
                                    color:driveRow.observedState==="Mounted"&&driveRow.fix.hint===""?root.ink:Color.urgent;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText
                                }
                                Text {
                                    width:content.width;visible:driveRow.fix.hint!=="";text:driveRow.fix.hint
                                    color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText
                                }
                                Button {
                                    visible:driveRow.fix.action!==""
                                    text:driveRow.fix.action==="recover"?"Unlock with recovery passphrase…":"Reconnect drive"
                                    enabled:root.storage.helperAvailable&&!service.mutating
                                    onClicked:{
                                        if(driveRow.fix.action==="recover")service.launch(["unlock","--drive",driveRow.drive.id,"--label",Model.display(driveRow.drive.name)+" · "+Model.display(driveRow.drive.mountpoint)]);
                                        else service.submit({op:"reconnect_drive",id:driveRow.drive.id});
                                    }
                                }
                            }
                        }
                    }
                    Column {
                        width:content.width;spacing:Style.spacing.sm;visible:root.view==="overview"
                        PanelSectionHeader {text:"MOVED FOLDERS & RECOVERY"}
                        Text {
                            readonly property real n:Model.reclaimable(root.storage.moves||[])
                            visible:n>0;width:content.width
                            text:Model.bytes(n)+" reclaimable on the OS disk (old copies kept for Undo)"
                            color:root.ink;opacity:0.75;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText
                        }
                        Repeater {
                            id:moveRows;model:root.moves.length
                            Button {
                                required property int index
                                property var move:root.moves[index]||({})
                                width:content.width;leftAlign:true;hasCursor:root.cursor===root.disks.length+index
                                text:Model.display(move.source)+" → "+Model.display(move.destMount)+"\n"+Model.moveText(move,root.pendingRestart)[0]
                                onClicked:root.selectMove(move)
                            }
                        }
                        Text {visible:root.moves.length===0;text:"No folder moves. Your existing mounts are unchanged.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;textFormat:Text.PlainText}
                        Button {visible:root.pendingRestart!==null && root.pendingRestart.valid===true;text:"Restart now to finish…";onClicked:root.restartNow()}
                        Row {spacing:Style.spacing.sm;Button{text:"+ Add drive";enabled:root.storage.helperAvailable;onClicked:root.go("add");}Button{text:"Move folder";enabled:root.storage.helperAvailable;onClicked:root.go("move");}}
                    }
                    Column {
                        width:content.width;spacing:Style.spacing.sm;visible:root.view==="add" && root.chosenDisk!==null
                        Text {width:content.width;text:"Everything on "+(root.chosenDisk?Model.display(root.chosenDisk.model):"")+" will be erased. Confirm this disk's serial, not another disk's.";color:Color.urgent;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        TextField {id:serialField;width:content.width;placeholderText:"Type last 4 serial characters";onActiveFocusChanged:if(activeFocus)root.ensureVisible(this);Keys.onEscapePressed:catcher.forceActiveFocus();}
                        TextField {id:nameField;width:content.width;text:"data2";placeholderText:"Mapper name";onActiveFocusChanged:if(activeFocus)root.ensureVisible(this);Keys.onEscapePressed:catcher.forceActiveFocus();}
                        TextField {id:mountField;width:content.width;text:"/data2";placeholderText:"Mountpoint";onActiveFocusChanged:if(activeFocus)root.ensureVisible(this);Keys.onEscapePressed:catcher.forceActiveFocus();}
                        Toggle {id:eraseToggle;width:content.width;label:"Erase existing partitions/signatures";checked:false;onClicked:checked=!checked;}
                        Toggle {id:autoToggle;width:content.width;label:"Unlock with the OS";description:"Encrypted OS root required";checked:true;onClicked:checked=!checked;}
                        Text {width:content.width;text:"Btrfs · zstd · TRIM through encryption off. Recovery passphrase and its safe-storage confirmation open in a separate native window. Export the header backup off-machine afterwards.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Button {text:"Continue to encryption & recovery…";enabled:root.storage.helperAvailable && Model.canProvision(root.chosenDisk,serialField.text) && (!autoToggle.checked||root.storage.rootEncrypted||root.storage.testFixtureMode);onClicked:root.provision();}
                    }
                    Column {
                        width:content.width;spacing:Style.spacing.sm;visible:root.view==="move"
                        Text {width:content.width;text:"The folder moves during a restart, when nothing else can write to it: copy → check every file → the familiar path opens the drive. The old copy is kept until you delete it. Profiles, credentials, databases and unreadable files are refused, never skipped.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        TextField {id:sourceField;width:content.width;placeholderText:"Absolute source folder (e.g. ~/Videos expanded)";onActiveFocusChanged:if(activeFocus)root.ensureVisible(this);Keys.onEscapePressed:catcher.forceActiveFocus();}
                        TextField {id:destinationField;width:content.width;placeholderText:"Encrypted destination mount (e.g. /data)";onActiveFocusChanged:if(activeFocus)root.ensureVisible(this);Keys.onEscapePressed:catcher.forceActiveFocus();}
                        Button {text:"Review move…";enabled:root.storage.helperAvailable && sourceField.text!=="" && destinationField.text!=="" && !service.mutating;onClicked:root.startMove();}
                    }
                    Column {
                        width:content.width;spacing:Style.spacing.sm;visible:root.view==="manage" && root.chosenDisk!==null
                        Text {width:content.width;text:root.chosenDisk?Model.display(root.chosenDisk.model)+" · serial "+Model.display(root.chosenDisk.serial)+"\n"+root.chosenDisk.state+" · "+Model.health(root.chosenDisk,root.storage.health).label:"";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Text {
                            readonly property var h:root.chosenDisk?Model.health(root.chosenDisk,root.storage.health):({reason:"",state:""})
                            width:content.width;visible:h.reason!=="";text:h.reason
                            color:h.state==="failing"||h.state==="warning"?Color.urgent:root.ink;opacity:h.state==="unavailable"?0.7:1
                            font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText
                        }
                        Text {width:content.width;visible:root.chosenDisk&&root.chosenDisk.state==="locked";text:"Disk is present but locked. Missing keyfile or a new OS? Use the recovery passphrase. No secret is entered in this panel.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Button {text:"Continue existing drive setup…";visible:root.chosenDisk && (root.storage.drives||[]).some(function(d){return d.serial===root.chosenDisk.serial && d.state!=="ready";});onClicked:{var ds=root.storage.drives||[];for(var i=0;i<ds.length;i++)if(ds[i].serial===root.chosenDisk.serial)service.submit({op:"resume_drive",id:ds[i].id});}}
                        Button {
                            readonly property var drive:root.chosenDisk?Model.configuredFor(root.chosenDisk,root.configuredDrives):null
                            text:"Unlock with recovery passphrase…";visible:root.chosenDisk&&root.chosenDisk.state==="locked"
                            enabled:drive!==null&&root.storage.helperAvailable
                            onClicked:service.launch(["unlock","--drive",drive.id,"--label",Model.display(drive.name)+" · "+Model.display(drive.mountpoint)])
                        }
                        Text {width:content.width;visible:root.chosenDisk&&root.chosenDisk.state==="locked"&&Model.configuredFor(root.chosenDisk,root.configuredDrives)===null;text:"This encrypted disk was not set up by Drives on this computer, so it is not unlocked here.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        TextField {id:exportField;width:content.width;placeholderText:"Absolute header-export path in your home";onActiveFocusChanged:if(activeFocus)root.ensureVisible(this);Keys.onEscapePressed:catcher.forceActiveFocus();}
                        Button {text:"Export header backup…";enabled:root.storage.helperAvailable && exportField.text!=="";onClicked:{var d=root.storage.drives||[];for(var i=0;i<d.length;i++)if(d[i].serial===root.chosenDisk.serial)service.submit({op:"export_header",name:d[i].name,destination:exportField.text});}}
                    }
                    Column {
                        width:content.width;spacing:Style.spacing.sm;visible:root.view==="resume" && root.chosenMove!==null
                        readonly property var words:root.chosenMove?Model.moveText(root.chosenMove,root.pendingRestart):["",""]
                        Text {width:content.width;text:root.chosenMove?Model.display(root.chosenMove.source)+" → "+Model.display(root.chosenMove.destMount):"";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Text {width:content.width;text:parent.words[0];color:root.chosenMove&&Model.warning({moves:[root.chosenMove],restartPending:root.pendingRestart})?Color.urgent:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;font.bold:true;textFormat:Text.PlainText}
                        Text {width:content.width;text:parent.words[1];color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Text {width:content.width;visible:!!(root.chosenMove&&root.chosenMove.error);text:root.chosenMove?Model.display(root.chosenMove.error):"";color:Color.urgent;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Repeater {
                            model:root.chosenMove?Model.moveActions(root.chosenMove,root.pendingRestart):[]
                            Button {
                                required property var modelData
                                text:root.actionLabels[modelData]||modelData
                                enabled:root.storage.helperAvailable&&!service.mutating
                                foreground:modelData==="delete_old_copy"?Color.urgent:root.ink
                                onClicked:root.action(modelData)
                            }
                        }
                    }
                    Column {
                        width:content.width;spacing:Style.spacing.sm;visible:root.view==="progress"
                        Text {width:content.width;text:root.lastJob?root.lastJob.method+" · "+root.lastJob.state:"Waiting for the helper…";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Text {width:content.width;text:"You can close this panel; the helper owns the operation. Folder moves themselves only run during a restart.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Repeater {model:root.moves.length;Button{required property int index;property var move:root.moves[index]||({});width:content.width;leftAlign:true;text:Model.display(move.source)+" · "+Model.moveText(move,root.pendingRestart)[0];onClicked:root.selectMove(move);}}
                        Repeater {model:(root.storage.drives||[]).length;Text{required property int index;width:content.width;text:Model.display(root.storage.drives[index].name)+" · "+root.storage.drives[index].state;color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;textFormat:Text.PlainText;}}
                    }
                    PanelSeparator {}
                    Text {width:content.width;text:Model.footerHints(root.view);color:root.ink;opacity:0.6;font.family:Style.font.family;font.pixelSize:Style.font.caption;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                }
            }
            ConfirmDialog {
                id:confirm
                anchors.fill:parent
                Keys.onPressed:function(event){if(handleKey(event))event.accepted=true;}
                onCanceled:{opened=false;root.confirmationAction=null;catcher.forceActiveFocus();}
                onConfirmed:{var fn=root.confirmationAction;opened=false;root.confirmationAction=null;catcher.forceActiveFocus();if(fn)fn();}
            }
        }
    }
    IpcHandler {
        target:"drives"
        function status():string{return JSON.stringify({opened:root.opened,view:root.view,cursor:root.cursor,contentY:flick.contentY,confirmOpen:confirm.opened,confirmSelection:confirm.selectedIndex,snapshot:root.storage,rates:service.rates,footer:Model.footerHints(root.view),busy:service.busy,request:service.request.op,pending:service.pending?service.pending.op:"",error:service.error});}
        function open():void{root.open();}
        function close():void{root.close();}
        function refresh():void{service.refresh();}
        function goto(view:string):string{if(["overview","add","move","manage","resume","progress"].indexOf(view)<0)return "invalid";root.go(view);return "ok";}
        function selectDisk(id:string):string{for(var i=0;i<root.disks.length;i++)if(root.disks[i].id===id){root.selectDisk(root.disks[i]);return "ok";}return "missing";}
        function selectMove(id:string):string{for(var i=0;i<root.moves.length;i++)if(root.moves[i].id===id){root.selectMove(root.moves[i]);return "ok";}return "missing";}
        function setMove(source:string,dest:string):void{sourceField.text=source;destinationField.text=dest;root.go("move");}
        function reviewMove():void{root.startMove();}
        function moveAction(op:string):string{if(!root.chosenMove||Model.moveActions(root.chosenMove,root.pendingRestart).indexOf(op)<0)return "unavailable";root.action(op);return "ok";}
        function reconnect(name:string):string{var ds=root.configuredDrives;for(var i=0;i<ds.length;i++)if(ds[i].name===name){if(Model.driveFix(ds[i],root.disks,root.storage.moves||[]).action!=="reconnect")return "unavailable";service.submit({op:"reconnect_drive",id:ds[i].id});return "ok";}return "missing";}
        function serial(fragment:string):string{serialField.text=fragment;return Model.canProvision(root.chosenDisk,fragment)?"matched":"rejected";}
    }
}
