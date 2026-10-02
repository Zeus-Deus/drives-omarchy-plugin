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
    readonly property var moves: storage.moves || []
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
    function startMove() {
        var src=sourceField.text,dest=destinationField.text;
        ask("Move "+Model.display(src)+"?\nApps keep the same path through a bind mount. The original stays as .pre-move until you explicitly delete it after a reboot. Open files, protected data or unreadable subtrees block the move.",function(){service.submit({op:"start_move",src:src,destMount:dest});go("progress");});
    }
    function action(op) {
        if(!chosenMove)return;
        var id=chosenMove.id;
        var words={rollback_move:"Undo this move? Refused if new files differ from the old copy.",delete_old_copy:"Delete the old copy? Undo will no longer be possible. Btrfs snapshots may retain the data; reclaimed space is not guaranteed.",cancel_move:"Cancel and remove the partial destination copy? The original stays in use.",restart_move:"Remove the partial copy and start over?"};
        if(words[op])ask(words[op],function(){service.submit({op:op,id:id});});
        else service.submit({op:op,id:id});
    }
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
                        text:"VM test fixture: OS root is plaintext. This does not prove encrypted-root key protection.";color:Color.urgent;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText
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
                                    Text {
                                        required property int index
                                        property var u:diskColumn.disk.usage[index]||({})
                                        width:content.width;text:Model.display(u.target)+" · "+u.percent+"% used · "+Model.bytes(u.free)+" free"
                                        font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;color:root.ink;textFormat:Text.PlainText
                                    }
                                }
                            }
                        }
                        Text {width:content.width;visible:service.loaded && root.disks.length===0;text:"No supported disks detected. Network, RAID, LVM and loop topologies are not guessed.";font.family:Style.font.family;font.pixelSize:Style.font.body;color:root.ink;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                    }
                    Column {
                        width:content.width;spacing:Style.spacing.sm;visible:root.view==="overview"
                        PanelSectionHeader {text:"MOVED FOLDERS & RECOVERY"}
                        Repeater {
                            id:moveRows;model:root.moves.length
                            Button {
                                required property int index
                                property var move:root.moves[index]||({})
                                width:content.width;leftAlign:true;hasCursor:root.cursor===root.disks.length+index
                                text:Model.display(move.source)+"\n"+Model.display(move.state)+" · "+(move.bound?"bind active":move.oldCopyAvailable?"missing bind · locked placeholder":"original stays in use")
                                onClicked:root.selectMove(move)
                            }
                        }
                        Text {visible:root.moves.length===0;text:"No folder moves. Your existing mounts are unchanged.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;textFormat:Text.PlainText}
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
                        Text {width:content.width;text:"Copy → full checksum & metadata verify → bind → read/write test. Open files, nested mounts, profiles, credentials and unreadable subtrees block the move. Nothing is killed. Keep the old copy until a successful reboot.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        TextField {id:sourceField;width:content.width;placeholderText:"Absolute source folder (e.g. ~/Videos expanded)";onActiveFocusChanged:if(activeFocus)root.ensureVisible(this);Keys.onEscapePressed:catcher.forceActiveFocus();}
                        TextField {id:destinationField;width:content.width;placeholderText:"Encrypted destination mount (e.g. /data)";onActiveFocusChanged:if(activeFocus)root.ensureVisible(this);Keys.onEscapePressed:catcher.forceActiveFocus();}
                        Button {text:"Review move…";enabled:root.storage.helperAvailable && sourceField.text!=="" && destinationField.text!=="" && !service.mutating;onClicked:root.startMove();}
                    }
                    Column {
                        width:content.width;spacing:Style.spacing.sm;visible:root.view==="manage" && root.chosenDisk!==null
                        Text {width:content.width;text:root.chosenDisk?Model.display(root.chosenDisk.model)+" · serial "+Model.display(root.chosenDisk.serial)+"\n"+root.chosenDisk.state+" · SMART: not checked":"";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Text {width:content.width;visible:root.chosenDisk&&root.chosenDisk.state==="locked";text:"Disk is present but locked. Missing keyfile or a new OS? Use the recovery passphrase. No secret is entered in this panel.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Button {text:"Continue existing drive setup…";visible:root.chosenDisk && (root.storage.drives||[]).some(function(d){return d.serial===root.chosenDisk.serial && d.state!=="ready";});onClicked:{var ds=root.storage.drives||[];for(var i=0;i<ds.length;i++)if(ds[i].serial===root.chosenDisk.serial)service.submit({op:"resume_drive",id:ds[i].id});}}
                        Button {text:"Unlock with recovery passphrase…";visible:root.chosenDisk&&root.chosenDisk.state==="locked";enabled:root.chosenDisk && root.chosenDisk.encryptedObject;onClicked:service.launch(["unlock","--device",root.chosenDisk.encryptedObject]);}
                        TextField {id:exportField;width:content.width;placeholderText:"Absolute header-export path in your home";onActiveFocusChanged:if(activeFocus)root.ensureVisible(this);Keys.onEscapePressed:catcher.forceActiveFocus();}
                        Button {text:"Export header backup…";enabled:root.storage.helperAvailable && exportField.text!=="";onClicked:{var d=root.storage.drives||[];for(var i=0;i<d.length;i++)if(d[i].serial===root.chosenDisk.serial)service.submit({op:"export_header",name:d[i].name,destination:exportField.text});}}
                    }
                    Column {
                        width:content.width;spacing:Style.spacing.sm;visible:root.view==="resume" && root.chosenMove!==null
                        Text {width:content.width;text:root.chosenMove?Model.display(root.chosenMove.source)+" → "+Model.display(root.chosenMove.destMount)+"\n"+Model.display(root.chosenMove.state)+" · "+(root.chosenMove.bound?"bind active":"bind absent")+"\n"+Model.display(root.chosenMove.error||""):"";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Text {width:content.width;text:root.chosenMove&&root.chosenMove.originalAvailable?"Your original is untouched and stays in use. Continue syncs changes and restarts full verification.":"Inspect both copies before acting. Nothing resumes automatically at boot.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Button {text:"Continue (full verify)";enabled:root.storage.helperAvailable&&!service.mutating;onClicked:root.action("resume_move");}
                        Row {spacing:Style.spacing.sm;Button{text:"Start over…";enabled:root.chosenMove&&root.chosenMove.originalAvailable;onClicked:root.action("restart_move");}Button{text:"Cancel move…";enabled:root.chosenMove&&root.chosenMove.originalAvailable;onClicked:root.action("cancel_move");}}
                        Button {text:"Undo move…";enabled:root.chosenMove&&root.chosenMove.oldCopyAvailable;onClicked:root.action("rollback_move");}
                        Button {text:"Delete old copy…";enabled:root.chosenMove&&root.chosenMove.canDelete===true;foreground:Color.urgent;onClicked:root.action("delete_old_copy");}
                        Text {width:content.width;text:"Deletion is separate and only available after saved cutover verification, an OS reboot and the correct live bind. Snapshots may retain old data.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                    }
                    Column {
                        width:content.width;spacing:Style.spacing.sm;visible:root.view==="progress"
                        Text {width:content.width;text:root.lastJob?root.lastJob.method+" · "+root.lastJob.state:"Waiting for the helper…";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Text {width:content.width;text:"You can close this panel; the helper owns the operation. Copy → Verify → Switch → Test → Keep old copy. Interrupted work stays paused until you explicitly Continue or Undo.";color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.bodySmall;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
                        Repeater {model:root.moves.length;Button{required property int index;property var move:root.moves[index]||({});width:content.width;leftAlign:true;text:Model.display(move.source)+" · "+move.state;onClicked:root.selectMove(move);}}
                        Repeater {model:(root.storage.drives||[]).length;Text{required property int index;width:content.width;text:Model.display(root.storage.drives[index].name)+" · "+root.storage.drives[index].state;color:root.ink;font.family:Style.font.family;font.pixelSize:Style.font.body;textFormat:Text.PlainText;}}
                    }
                    PanelSeparator {}
                    Text {width:content.width;text:"j/k move · enter manage · a add · m move · r rescan · esc back/close";color:root.ink;opacity:0.6;font.family:Style.font.family;font.pixelSize:Style.font.caption;wrapMode:Text.WordWrap;textFormat:Text.PlainText}
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
        function status():string{return JSON.stringify({opened:root.opened,view:root.view,cursor:root.cursor,contentY:flick.contentY,confirmOpen:confirm.opened,confirmSelection:confirm.selectedIndex,snapshot:root.storage,error:service.error});}
        function open():void{root.open();}
        function close():void{root.close();}
        function refresh():void{service.refresh();}
        function goto(view:string):string{if(["overview","add","move","manage","resume","progress"].indexOf(view)<0)return "invalid";root.go(view);return "ok";}
        function selectDisk(id:string):string{for(var i=0;i<root.disks.length;i++)if(root.disks[i].id===id){root.selectDisk(root.disks[i]);return "ok";}return "missing";}
        function selectMove(id:string):string{for(var i=0;i<root.moves.length;i++)if(root.moves[i].id===id){root.selectMove(root.moves[i]);return "ok";}return "missing";}
        function setMove(source:string,dest:string):void{sourceField.text=source;destinationField.text=dest;root.go("move");}
        function reviewMove():void{root.startMove();}
        function serial(fragment:string):string{serialField.text=fragment;return Model.canProvision(root.chosenDisk,fragment)?"matched":"rejected";}
    }
}
