pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Layout follows the design mockups: A overview, B add drive, C/E move folder,
// F moved folder, D missing drive. Wording, state and offered actions are
// decided in Model.js (Node-tested); this file only lays rows out.
BarWidget {
    id: root
    moduleName: "io.github.zeus-deus.drives"

    // ---- navigation state ----------------------------------------------------
    property string view: "overview"          // overview | drive | add | move | resume | progress
    property string selectedKey: ""           // dataDrives() key shown on the drive view
    property string selectedDiskId: ""        // disk being set up on the add view
    property string selectedMoveId: ""
    property int addStep: 1
    property bool autoUnlock: true
    property bool eraseDisk: false
    property string moveTarget: ""
    property string watchJob: ""
    property int cursor: 0
    property var confirmationAction: null

    readonly property bool opened: controller.open
    readonly property bool popoutSwitchClosing: controller.popoutSwitchClosing
    readonly property var storage: service.snapshot
    readonly property var disks: storage.disks || []
    readonly property var moves: Model.visibleMoves(storage.moves || [])
    readonly property var pendingRestart: storage.restartPending || null
    readonly property bool restartArmed: pendingRestart !== null && pendingRestart.valid === true
    readonly property bool canWrite: storage.helperAvailable === true && !service.mutating
    readonly property string home: Quickshell.env("HOME")

    // ---- rows (Model.js) -----------------------------------------------------
    readonly property var systemRows: Model.systemDisks(storage)
    readonly property var driveRows: Model.dataDrives(storage)
    readonly property var newRows: Model.newDisks(storage)
    readonly property var folderRows: Model.folderRows(storage)
    readonly property var alertRows: Model.alerts(storage)
    readonly property var candidates: Model.addCandidates(storage)
    readonly property var targets: Model.moveTargets(storage)
    readonly property var chosenDrive: {
        for (var i = 0; i < driveRows.length; i++) if (driveRows[i].key === selectedKey) return driveRows[i];
        return null;
    }
    readonly property var cd: chosenDrive || ({})
    readonly property bool cdConfigured: chosenDrive !== null && chosenDrive.drive !== null
    readonly property var chosenDisk: {
        for (var i = 0; i < disks.length; i++) if (disks[i].id === selectedDiskId) return disks[i];
        return null;
    }
    readonly property var chosenMove: {
        for (var i = 0; i < moves.length; i++) if (moves[i].id === selectedMoveId) return moves[i];
        return null;
    }
    readonly property var watchedJob: {
        var js = storage.jobs || [];
        for (var i = js.length - 1; i >= 0; i--) if (js[i].id === watchJob) return js[i];
        return null;
    }
    readonly property bool serialOk: Model.canProvision(chosenDisk, serialField.text)
    readonly property bool autoAllowed: storage.rootEncrypted === true || storage.testFixtureMode === true
    readonly property string moveSource: Model.expandHome(folderField.text, home)

    // ---- keyboard order for the current view ---------------------------------
    readonly property var nav: {
        var out = [];
        if (view === "overview") {
            driveRows.forEach(function(r) { out.push({kind: "drive", id: r.key}); });
            newRows.forEach(function(c) { out.push({kind: "new", id: c.id}); });
            folderRows.forEach(function(f) { out.push({kind: "move", id: f.id}); });
            if (restartArmed) out.push({kind: "act", id: "restart"});
        } else if (view === "drive" && chosenDrive) {
            if (chosenDrive.fix.action !== "") out.push({kind: "act", id: chosenDrive.fix.action});
            folderRows.forEach(function(f) { if (cdConfigured && f.move.destMount === chosenDrive.drive.mountpoint) out.push({kind: "move", id: f.id}); });
            if (cdConfigured) out.push({kind: "act", id: "export"});
        } else if (view === "add") {
            if (addStep === 1) candidates.forEach(function(c) { if (c.selectable) out.push({kind: "pick", id: c.id}); });
            else out.push({kind: "opt", id: "auto"}, {kind: "opt", id: "manual"}, {kind: "opt", id: "erase"}, {kind: "act", id: "provision"});
        } else if (view === "move") {
            targets.forEach(function(t) { out.push({kind: "target", id: t.mountpoint}); });
            out.push({kind: "act", id: "review"});
        } else if (view === "resume" && chosenMove) {
            Model.moveActions(chosenMove, pendingRestart).forEach(function(op) { out.push({kind: "act", id: op}); });
        } else if (view === "progress") {
            if (jobDone && restartArmed) out.push({kind: "act", id: "restart"});
            out.push({kind: "act", id: "home"});
        }
        return out;
    }
    readonly property bool jobDone: watchedJob !== null && watchedJob.state === "done"
    function navIndex(kind, id) {
        for (var i = 0; i < nav.length; i++) if (nav[i].kind === kind && nav[i].id === id) return i;
        return -1;
    }

    // ---- hero text -----------------------------------------------------------
    readonly property var heading: {
        if (view === "drive" && chosenDrive)
            return ["󰋊", chosenDrive.title, (chosenDrive.disk ? "serial …" + Model.display(String(chosenDrive.disk.serial).slice(-4)) + " · " : "") + (cdConfigured ? Model.display(chosenDrive.drive.mountpoint) : "")];
        if (view === "add")
            return addStep === 1 ? ["󰋊", "Add drive", "step 1 of 2 · choose the disk"] : ["󰌾", "Encryption & unlock", "step 2 of 2 · how this disk opens"];
        if (view === "move") return ["󰉒", "Move folder", "keeps its path · moves during a restart"];
        if (view === "resume" && chosenMove) {
            var stage = Model.moveStage(chosenMove, pendingRestart);
            return [stage.indexOf("moved") === 0 || stage === "cleaned" ? "󰄬" : (Model.warning({moves: [chosenMove], restartPending: pendingRestart}) ? "󰀦" : "󰉒"),
                    Model.shortPath(chosenMove.source), Model.moveMeta(chosenMove, pendingRestart)];
        }
        if (view === "progress") {
            var j = Model.jobText(watchedJob);
            return [j.state === "failed" ? "󰀦" : (j.state === "done" ? "󰄬" : "󰔟"), j.title, j.state === "running" ? "working · you can close this panel" : (j.state === "failed" ? "stopped · nothing was changed by this step" : "done")];
        }
        return ["󰋊", "Drives", Model.overviewMeta(storage)];
    }

    // ---- palette (theme tokens only) -----------------------------------------
    readonly property color ink: bar ? bar.foreground : Color.foreground
    readonly property color dim: Qt.darker(ink, 1.4)
    readonly property string face: Style.font.family
    function toneColor(tone) { return tone === "ok" ? Color.accent : (tone === "bad" ? Color.urgent : dim); }
    function segColor(i) { return Util.alpha(Color.accent, [1.0, 0.68, 0.46, 0.3][Math.max(0, i) % 4]); }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    // ---- actions --------------------------------------------------------------
    function open() { controller.open = true; service.opened = true; }
    function close() { confirm.opened = false; controller.open = false; service.opened = false; }
    function closeForPopoutSwitch() { controller.popoutSwitchClosing = true; close(); Qt.callLater(function() { controller.popoutSwitchClosing = false; }); }
    function go(where) { view = where; cursor = 0; flick.contentY = 0; catcher.forceActiveFocus(); }
    function back() {
        if (view === "add" && addStep === 2) { addStep = 1; cursor = Math.max(0, navIndex("pick", selectedDiskId)); return; }
        if (view === "overview") close(); else go("overview");
    }
    function ask(message, action, confirmText) {
        confirmationAction = action; confirm.message = message; confirm.confirmText = confirmText || "Confirm";
        confirm.selectedIndex = 0; confirm.opened = true; confirm.forceActiveFocus();
    }
    function openDrive(key) { selectedKey = key; go("drive"); }
    function openMove(id) { selectedMoveId = id; go("resume"); }
    function openAdd(id) {
        var s = Model.suggestMount(storage);
        nameField.text = s.name; mountField.text = s.mountpoint; serialField.text = "";
        autoUnlock = autoAllowed; eraseDisk = false; addStep = 1;
        var first = "";
        for (var i = 0; i < candidates.length; i++) if (candidates[i].selectable) { first = candidates[i].id; break; }
        selectedDiskId = id || first;
        go("add");
        cursor = Math.max(0, navIndex("pick", selectedDiskId));
    }
    function addNext() { if (!serialOk) return false; addStep = 2; cursor = 0; catcher.forceActiveFocus(); return true; }
    function openMoveFolder(source, dest) {
        folderField.text = source ? Model.shortPath(source) : "";
        moveTarget = dest || (targets.length ? targets[0].mountpoint : "");
        go("move");
        cursor = Math.max(0, navIndex("target", moveTarget));
    }
    function moveCursor(dy) { if (nav.length) cursor = Math.max(0, Math.min(nav.length - 1, cursor + dy)); }
    function ensureVisible(item) {
        var p = item.mapToItem(content, 0, 0);
        if (p.y < flick.contentY) flick.contentY = p.y;
        else if (p.y + item.height > flick.contentY + flick.height) flick.contentY = Math.min(flick.contentHeight - flick.height, p.y + item.height - flick.height);
    }
    function activate() { var n = nav[cursor]; if (n) run(n); }
    function run(n) {
        if (n.kind === "drive") openDrive(n.id);
        else if (n.kind === "new") openAdd(n.id);
        else if (n.kind === "move") openMove(n.id);
        else if (n.kind === "pick") { selectedDiskId = n.id; serialField.forceActiveFocus(); }
        else if (n.kind === "opt") { if (n.id === "erase") eraseDisk = !eraseDisk; else if (n.id === "manual" || autoAllowed) autoUnlock = n.id === "auto"; }
        else if (n.kind === "target") { moveTarget = n.id; }
        else if (n.kind === "act") act(n.id);
    }
    function act(op) {
        if (op === "home") return go("overview");
        if (op === "restart")
            return ask("Restart now?\nOpen windows close. This restart takes a little longer while the folder is handled, then your desktop comes back.",
                       function() { close(); Quickshell.execDetached(["omarchy-system-reboot"]); }, "Restart");
        if (!canWrite) return;
        if (op === "reconnect" && cdConfigured) return service.submit({op: "reconnect_drive", id: chosenDrive.drive.id});
        if (op === "recover" && cdConfigured)
            return service.launch(["unlock", "--drive", chosenDrive.drive.id, "--label", Model.display(chosenDrive.drive.name) + " · " + Model.display(chosenDrive.drive.mountpoint)]);
        if (op === "export") return exportHeader();
        if (op === "provision") return provision();
        if (op === "review") return reviewMove();
        if (!chosenMove) return;
        var id = chosenMove.id, name = Model.shortPath(chosenMove.source);
        var words = {
            resume_move: ["Move " + name + " during the next restart?\nWhile nothing else runs, the folder is copied, every file is checked, and " + name + " then opens the drive.", "Schedule"],
            rollback_move: ["Undo the move of " + name + " during the next restart?\nThe original comes back and the copy on the drive is removed. Refused if files changed since the move, so no work is lost.", "Schedule undo"],
            delete_old_copy: ["Delete the old copy of " + name + "?\nYour files stay on the drive; only the duplicate on the OS disk goes. Undo is no longer possible afterwards. Btrfs snapshots may keep the space for a while.", "Delete"],
            cancel_move: ["Cancel moving " + name + "?\nNothing has moved yet. The empty folder on the drive is removed and " + name + " stays where it is.", "Cancel move"],
            cancel_restart: ["Don't move " + name + " on the next restart?", "Don't move"]
        };
        if (words[op]) ask(words[op][0], function() { service.submit({op: op, id: id}); }, words[op][1]);
    }
    function provision() {
        var disk = chosenDisk;
        if (!serialOk || (autoUnlock && !autoAllowed)) return;
        var args = ["provision", "--by-id", disk.byId, "--serial", disk.serial, "--name", nameField.text, "--mountpoint", mountField.text, "--confirmation", serialField.text];
        if (eraseDisk) args.push("--erase");
        if (!autoUnlock) args.push("--manual");
        service.launch(args);
    }
    function exportHeader() {
        if (exportField.text === "") { exportField.forceActiveFocus(); return; }
        service.submit({op: "export_header", name: chosenDrive.drive.name, destination: Model.expandHome(exportField.text, home)});
    }
    function reviewMove() {
        if (moveSource === "" || moveTarget === "") { folderField.forceActiveFocus(); return; }
        var name = Model.shortPath(moveSource), drive = moveTarget;
        for (var i = 0; i < targets.length; i++) if (targets[i].mountpoint === moveTarget) drive = targets[i].title + " (" + Model.display(moveTarget) + ")";
        var src = moveSource, dest = moveTarget;
        ask(name + " stays exactly where it is.\nApps keep using " + name + "; the files will live on " + drive + ".\nNothing changes now: the folder is checked, then moved during your next restart. The old copy is kept until you delete it.",
            function() { service.submit({op: "start_move", src: src, destMount: dest}); }, "Plan move");
    }
    readonly property var actionWords: ({
        resume_move: ["󰑓", "Move on next restart…", ""], restart: ["󰜉", "Restart now…", ""], cancel_restart: ["󰜺", "Don't move on next restart", ""],
        rollback_move: ["󰕌", "Undo move…", ""], delete_old_copy: ["󰆴", "Delete old copy…", "bad"], cancel_move: ["󰜺", "Cancel move…", ""],
        reconnect: ["󰌆", "Reconnect drive", ""], recover: ["󰌆", "Unlock with recovery passphrase…", ""], export: ["󰈔", "Export header backup", ""],
        provision: ["󰌾", "Encrypt & set up…", ""], review: ["󰉒", "Review move…", ""], home: ["󰋊", "Back to drives", ""]
    })

    Service {
        id: service
        onLaunching: root.close()
        onFinished: function(result) { if (result.jobId) { root.watchJob = result.jobId; root.go("progress"); } }
    }

    WidgetButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: "󰋊" + (Model.warning(root.storage) ? " !" : (!root.vertical && root.setting("showUsage", true) && root.driveRows.length ? " " + Model.fullest(root.disks) + "%" : ""))
        foreground: Model.warning(root.storage) ? Color.urgent : root.ink
        onPressed: function(b) { if (root.opened) root.close(); else root.open(); }
    }

    // ---- building blocks ------------------------------------------------------
    component Pill: Rectangle {
        id: pill
        property string label: ""
        property string tone: "dim"
        readonly property color toneInk: root.toneColor(tone)
        visible: label !== ""
        implicitWidth: pillText.implicitWidth + Style.space(12)
        implicitHeight: pillText.implicitHeight + Style.space(2)
        color: "transparent"
        border.width: 1
        border.color: Util.alpha(toneInk, 0.5)
        radius: Style.cornerRadius
        Text {
            id: pillText
            anchors.centerIn: parent
            text: pill.label
            color: pill.toneInk
            font.family: root.face
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
        }
    }

    // Usage bar: one segment per moved folder, then everything else.
    component Meter: Rectangle {
        id: meter
        property var segments: []
        property real other: 0
        implicitHeight: Style.space(4)
        color: Util.alpha(root.ink, 0.12)
        Row {
            anchors.fill: parent
            Repeater {
                model: meter.segments.length
                Rectangle {
                    required property int index
                    readonly property var seg: meter.segments[index] || ({fraction: 0, color: 0})
                    width: seg.fraction > 0 ? Math.max(2, meter.width * seg.fraction) : 0
                    height: meter.height
                    color: root.segColor(seg.color)
                }
            }
            Rectangle { width: meter.width * Math.max(0, Math.min(1, meter.other)); height: meter.height; color: Util.alpha(root.ink, 0.4) }
        }
    }

    component SectionHead: Item {
        id: head
        property string label: ""
        property string trail: ""
        width: content.width
        implicitHeight: headText.implicitHeight + Style.space(6)
        PanelSectionHeader { id: headText; anchors.bottom: parent.bottom; text: head.label; foreground: root.ink; fontFamily: root.face }
        Text {
            anchors.right: parent.right
            anchors.baseline: headText.baseline
            text: head.trail
            color: root.dim
            font.family: root.face
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
        }
    }

    // One row: icon · title + pill · optional meter · sub line · trailing text.
    component NavRow: CursorSurface {
        id: row
        property int navIndex: -1
        property string icon: ""
        property color iconColor: root.ink
        property string title: ""
        property color titleColor: root.ink
        property string pillLabel: ""
        property string pillTone: "dim"
        property string sub: ""
        property color subColor: root.dim
        property string trail: ""
        property color trailColor: root.dim
        property bool selectable: true
        property bool showMeter: false
        property var segments: []
        property real other: 0
        signal chosen()
        width: content.width
        hasCursor: selectable && navIndex >= 0 && root.cursor === navIndex
        foreground: root.ink
        opacity: selectable || !enabled ? 1 : 0.5
        implicitHeight: rowBody.implicitHeight + Style.space(12)
        onHasCursorChanged: if (hasCursor) root.ensureVisible(row)
        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            enabled: row.selectable && row.enabled
            cursorShape: Qt.PointingHandCursor
            onContainsMouseChanged: if (containsMouse && row.navIndex >= 0) root.cursor = row.navIndex
            onClicked: row.chosen()
        }
        Item {
            id: rowBody
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: Style.spacing.rowPaddingX
            anchors.rightMargin: Style.spacing.rowPaddingX
            implicitHeight: Math.max(rowIcon.implicitHeight, rowInfo.implicitHeight)
            Text {
                id: rowIcon
                visible: row.icon !== ""
                anchors.left: parent.left
                anchors.top: parent.top
                width: Style.space(18)
                text: row.icon
                color: row.iconColor
                font.family: root.face
                font.pixelSize: Style.font.body
                textFormat: Text.PlainText
            }
            Text {
                id: rowTrail
                visible: row.trail !== ""
                anchors.right: parent.right
                anchors.top: parent.top
                text: row.trail
                color: row.trailColor
                font.family: root.face
                font.pixelSize: Style.font.bodySmall
                textFormat: Text.PlainText
            }
            Column {
                id: rowInfo
                anchors.left: rowIcon.visible ? rowIcon.right : parent.left
                anchors.leftMargin: rowIcon.visible ? Style.space(6) : 0
                anchors.right: parent.right
                anchors.rightMargin: rowTrail.visible ? rowTrail.implicitWidth + Style.space(10) : 0
                spacing: Style.space(4)
                Row {
                    width: parent.width
                    spacing: Style.space(8)
                    Text {
                        id: rowTitle
                        width: Math.min(implicitWidth, parent.width - (row.pillLabel !== "" ? rowPill.implicitWidth + Style.space(8) : 0))
                        text: row.title
                        color: row.titleColor
                        elide: Text.ElideRight
                        font.family: root.face
                        font.pixelSize: Style.font.body
                        textFormat: Text.PlainText
                    }
                    Pill { id: rowPill; label: row.pillLabel; tone: row.pillTone; anchors.verticalCenter: rowTitle.verticalCenter }
                }
                Meter { width: parent.width; visible: row.showMeter; segments: row.segments; other: row.other }
                Text {
                    width: parent.width
                    visible: row.sub !== ""
                    text: row.sub
                    color: row.subColor
                    wrapMode: Text.WordWrap
                    font.family: root.face
                    font.pixelSize: Style.font.bodySmall
                    textFormat: Text.PlainText
                }
            }
        }
    }

    component ActionRow: NavRow {
        property string op: ""
        readonly property var words: root.actionWords[op] || ["", op, ""]
        navIndex: root.navIndex("act", op)
        icon: words[0]
        title: words[1]
        iconColor: words[2] === "bad" ? Color.urgent : root.ink
        titleColor: words[2] === "bad" ? Color.urgent : root.ink
        enabled: op === "home" || op === "restart" || root.canWrite
        opacity: enabled ? 1 : 0.45
        onChosen: root.act(op)
    }

    // ✓ / ! / ○ line whose wrapped text keeps a hanging indent.
    component Check: Item {
        id: check
        property string glyph: "✓"
        property string text: ""
        property color tint: root.ink
        width: content.width
        implicitHeight: checkText.implicitHeight
        Text { id: checkGlyph; width: Style.space(18); text: check.glyph; color: check.tint; font.family: root.face; font.pixelSize: Style.font.bodySmall; textFormat: Text.PlainText }
        Text { id: checkText; anchors.left: checkGlyph.right; anchors.right: parent.right; text: check.text; color: check.tint; wrapMode: Text.WordWrap; font.family: root.face; font.pixelSize: Style.font.bodySmall; textFormat: Text.PlainText }
    }

    component Note: Text {
        width: content.width
        color: root.dim
        wrapMode: Text.WordWrap
        font.family: root.face
        font.pixelSize: Style.font.bodySmall
        textFormat: Text.PlainText
    }

    component Label: Text {
        color: root.dim
        font.family: root.face
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
    }

    component Field: TextField {
        id: field
        foreground: root.ink
        onActiveFocusChanged: if (activeFocus) root.ensureVisible(field)
        Keys.onEscapePressed: catcher.forceActiveFocus()
    }

    // Bordered card; urgent=true for erase warnings and missing drives.
    component Card: Rectangle {
        id: card
        property bool urgent: false
        default property alias body: cardBody.data
        width: content.width
        implicitHeight: cardBody.implicitHeight + Style.space(20)
        color: urgent ? Util.alpha(Color.urgent, 0.06) : "transparent"
        border.width: 1
        border.color: urgent ? Util.alpha(Color.urgent, 0.5) : Util.alpha(root.ink, 0.16)
        radius: Style.cornerRadius
        Column {
            id: cardBody
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Style.space(10)
            spacing: Style.space(6)
        }
    }

    KeyboardPanel {
        id: controller
        anchorItem: button
        bar: root.bar
        owner: root
        focusTarget: catcher
        contentWidth: fittedContentWidth(Style.space(460))
        contentHeight: fittedContentHeight(content.implicitHeight, Style.space(660))

        PanelKeyCatcher {
            id: catcher
            anchors.fill: parent
            blocked: confirm.opened || serialField.activeFocus || nameField.activeFocus || mountField.activeFocus || folderField.activeFocus || exportField.activeFocus
            onCloseRequested: root.back()
            onMoveRequested: function(dx, dy) { root.moveCursor(dy); }
            onActivateRequested: root.activate()
            onTextKey: function(t) {
                if (root.view !== "overview") return;
                if (t === "r") service.refresh();
                else if (t === "a" && root.storage.helperAvailable) root.openAdd("");
                else if (t === "m" && root.storage.helperAvailable) root.openMoveFolder("", "");
            }

            Flickable {
                id: flick
                anchors.fill: parent
                clip: true
                contentWidth: width
                contentHeight: content.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                WheelHandler {
                    onWheel: function(event) { flick.contentY = Model.scroll(flick.contentY, event.angleDelta.y, event.pixelDelta.y, flick.contentHeight, flick.height, Style.spacing.popupRowHeight); event.accepted = true; }
                }

                Column {
                    id: content
                    width: flick.width
                    spacing: Style.space(8)

                    PanelHero {
                        width: content.width
                        title: root.heading[1]
                        meta: root.heading[2]
                        foreground: root.ink
                        fontFamily: root.face
                        iconComponent: Component {
                            Text { text: root.heading[0]; color: root.ink; font.family: root.face; font.pixelSize: Style.font.display; textFormat: Text.PlainText }
                        }
                        trailingControl: Component {
                            Button {
                                bordered: true
                                foreground: root.ink
                                fontFamily: root.face
                                text: root.view === "overview" ? "+ Add drive" : "Back"
                                enabled: root.view !== "overview" || root.storage.helperAvailable === true
                                onClicked: { if (root.view === "overview") root.openAdd(""); else root.back(); }
                            }
                        }
                    }

                    // Error from the request just made (never stale jobs).
                    Card {
                        urgent: true
                        visible: service.error !== ""
                        Text { width: parent.width; text: service.error; color: Color.urgent; wrapMode: Text.WordWrap; font.family: root.face; font.pixelSize: Style.font.bodySmall; textFormat: Text.PlainText }
                    }

                    // ================= A · overview =================
                    Column {
                        width: content.width
                        spacing: Style.space(4)
                        visible: root.view === "overview"

                        Note {
                            visible: service.loaded && root.storage.helperAvailable !== true
                            text: "Read-only overview. Install the storage helper to set up drives and move folders; the plugin never elevates itself."
                        }

                        // D · a drive that isn't there or isn't open
                        Repeater {
                            model: root.alertRows.length
                            Card {
                                id: alertCard
                                required property int index
                                readonly property var a: root.alertRows[index] || ({fix: {action: "", hint: ""}, folders: []})
                                urgent: true
                                Check { width: parent.width; glyph: "󰀦"; tint: Color.urgent; text: alertCard.a.title }
                                Text { width: parent.width; text: alertCard.a.sub; color: root.dim; font.family: root.face; font.pixelSize: Style.font.bodySmall; wrapMode: Text.WordWrap; textFormat: Text.PlainText }
                                Repeater {
                                    model: (alertCard.a.folders || []).length
                                    Text {
                                        required property int index
                                        width: content.width - Style.space(20)
                                        text: "󰌾  " + alertCard.a.folders[index] + "  · locked, nothing is written to the OS disk"
                                        color: root.dim
                                        elide: Text.ElideRight
                                        font.family: root.face
                                        font.pixelSize: Style.font.bodySmall
                                        textFormat: Text.PlainText
                                    }
                                }
                                Text { width: parent.width; text: alertCard.a.fix.hint; visible: text !== ""; color: root.ink; font.family: root.face; font.pixelSize: Style.font.bodySmall; wrapMode: Text.WordWrap; textFormat: Text.PlainText }
                                Button {
                                    visible: alertCard.a.fix.action !== ""
                                    bordered: true
                                    foreground: root.ink
                                    fontFamily: root.face
                                    text: alertCard.a.fix.action === "recover" ? "󰌆  Unlock with recovery passphrase…" : "󰌆  Reconnect drive"
                                    enabled: root.canWrite
                                    onClicked: { root.selectedKey = alertCard.a.key; root.act(alertCard.a.fix.action); }
                                }
                            }
                        }

                        SectionHead { label: "SYSTEM"; trail: root.systemRows.length === 1 ? root.systemRows[0].size : "" }
                        Repeater {
                            model: root.systemRows.length
                            NavRow {
                                required property int index
                                readonly property var r: root.systemRows[index] || ({})
                                selectable: false
                                opacity: 1
                                icon: "󰋊"
                                title: r.title || ""
                                pillLabel: r.pill || ""
                                pillTone: r.tone || "dim"
                                sub: r.sub || ""
                                showMeter: !!r.usage
                                other: r.other || 0
                            }
                        }

                        SectionHead {
                            visible: root.driveRows.length > 0
                            label: "DATA"
                            trail: Model.dataTotal(root.storage)
                        }
                        Repeater {
                            model: root.driveRows.length
                            NavRow {
                                required property int index
                                readonly property var r: root.driveRows[index] || ({})
                                navIndex: index
                                icon: "󰋊"
                                iconColor: r.problem ? Color.urgent : root.ink
                                title: r.title || ""
                                pillLabel: r.pill || ""
                                pillTone: r.tone || "dim"
                                sub: r.sub || ""
                                showMeter: !!r.usage
                                segments: r.segments || []
                                other: r.other || 0
                                trail: "Manage"
                                onChosen: root.openDrive(r.key)
                            }
                        }

                        SectionHead { visible: root.newRows.length > 0; label: "NEW DISKS"; trail: "not set up" }
                        Repeater {
                            model: root.newRows.length
                            NavRow {
                                required property int index
                                readonly property var c: root.newRows[index] || ({})
                                navIndex: root.driveRows.length + index
                                icon: "󰋊"
                                title: c.title || ""
                                pillLabel: "new"
                                pillTone: "ok"
                                sub: c.sub || ""
                                trail: "Set up"
                                enabled: root.storage.helperAvailable === true
                                onChosen: root.openAdd(c.id)
                            }
                        }

                        SectionHead {
                            visible: root.folderRows.length > 0
                            label: "MOVED FOLDERS"
                            trail: {
                                var n = Model.reclaimable(root.storage.moves || []);
                                return n > 0 ? Model.compact(n) + " old copies on OS disk" : root.folderRows.length + (root.folderRows.length === 1 ? " folder" : " folders");
                            }
                        }
                        Repeater {
                            model: root.folderRows.length
                            NavRow {
                                required property int index
                                readonly property var f: root.folderRows[index] || ({})
                                navIndex: root.driveRows.length + root.newRows.length + index
                                icon: "■"
                                iconColor: f.color >= 0 ? root.segColor(f.color) : root.dim
                                title: f.label || ""
                                trail: f.status || ""
                                trailColor: root.toneColor(f.tone)
                                onChosen: root.openMove(f.id)
                            }
                        }
                        ActionRow { visible: root.restartArmed; op: "restart" }

                        Note {
                            visible: service.loaded && root.driveRows.length === 0 && root.newRows.length === 0
                            text: "No data drives yet. Plug in a disk to set it up as an encrypted data drive."
                        }
                        Note {
                            visible: root.storage.testFixtureMode === true
                            topPadding: Style.space(4)
                            text: "VM test mode · this OS disk is not encrypted"
                            font.pixelSize: Style.font.caption
                        }
                    }

                    // ================= drive detail =================
                    Column {
                        width: content.width
                        spacing: Style.space(6)
                        visible: root.view === "drive" && root.chosenDrive !== null

                        Row {
                            spacing: Style.space(6)
                            Pill { label: root.cd.pill || ""; tone: root.cd.tone || "dim" }
                            Pill {
                                readonly property var h: root.cd.health || ({state: "unknown", label: ""})
                                label: h.state === "unknown" ? "" : String(h.label).replace("Health: ", "health ")
                                tone: h.state === "failing" || h.state === "warning" ? "bad" : (h.state === "passed" ? "ok" : "dim")
                            }
                        }
                        Meter { width: content.width; visible: !!root.cd.usage; segments: root.cd.segments || []; other: root.cd.other || 0 }
                        Note { text: root.cd.sub || ""; color: root.ink }
                        Note {
                            readonly property string io: root.cd.disk ? Model.ioText(service.rates[root.cd.disk.name]) : ""
                            readonly property string why: root.cd.health ? root.cd.health.reason : ""
                            visible: text !== ""
                            text: [io, why].filter(function(s) { return s !== ""; }).join("\n")
                        }
                        Note { visible: text !== ""; text: root.cd.fix ? root.cd.fix.hint : ""; color: Color.urgent }
                        ActionRow { visible: root.cd.fix !== undefined && root.cd.fix.action === "reconnect"; op: "reconnect" }
                        ActionRow { visible: root.cd.fix !== undefined && root.cd.fix.action === "recover"; op: "recover" }

                        SectionHead { visible: (root.cd.folders || []).length > 0; label: "MOVED HERE"; trail: "" }
                        Repeater {
                            model: root.folderRows.length
                            NavRow {
                                required property int index
                                readonly property var f: root.folderRows[index] || ({move: {}})
                                visible: root.cdConfigured && f.move.destMount === root.chosenDrive.drive.mountpoint
                                height: visible ? implicitHeight : 0
                                navIndex: root.navIndex("move", f.id)
                                icon: "■"
                                iconColor: f.color >= 0 ? root.segColor(f.color) : root.dim
                                title: f.source || ""
                                sub: Model.compact((f.move.stats && f.move.stats.bytes) || 0)
                                trail: f.status || ""
                                trailColor: root.toneColor(f.tone)
                                onChosen: root.openMove(f.id)
                            }
                        }

                        SectionHead { visible: root.cdConfigured; label: "RECOVERY"; trail: "" }
                        Note {
                            visible: root.cdConfigured
                            text: "Keep a copy of the encryption header off this computer. With it and the recovery passphrase the drive opens even if its header gets damaged."
                        }
                        Field { id: exportField; width: content.width; visible: root.cdConfigured; placeholderText: "~/drive-header-backup.img"; Keys.onReturnPressed: root.act("export") }
                        ActionRow { visible: root.cdConfigured; op: "export" }
                    }

                    // ================= B · add drive =================
                    Column {
                        width: content.width
                        spacing: Style.space(6)
                        visible: root.view === "add"

                        Row {
                            spacing: Style.space(4)
                            Repeater {
                                model: 2
                                Rectangle { required property int index; width: Style.space(28); height: Style.space(3); color: index < root.addStep ? Color.accent : Util.alpha(root.ink, 0.18) }
                            }
                        }
                        // step 1 · choose the disk
                        Repeater {
                            model: root.addStep === 1 ? root.candidates.length : 0
                            NavRow {
                                required property int index
                                readonly property var c: root.candidates[index] || ({})
                                selectable: c.selectable === true
                                navIndex: root.navIndex("pick", c.id)
                                current: c.id === root.selectedDiskId
                                icon: "󰋊"
                                title: c.title || ""
                                pillLabel: c.selectable ? "new" : ""
                                pillTone: "ok"
                                sub: c.sub || ""
                                trail: c.selectable ? "" : "󰌾"
                                onChosen: { root.selectedDiskId = c.id; serialField.forceActiveFocus(); }
                            }
                        }
                        Note {
                            visible: root.addStep === 1 && root.newRows.length === 0
                            text: "No disk can be set up right now. Plug in a new disk; disks in use and the system disk are never offered."
                        }
                        Card {
                            urgent: true
                            visible: root.addStep === 1 && root.chosenDisk !== null && root.chosenDisk.selectable === true
                            Check {
                                width: parent.width
                                glyph: "󰀦"
                                tint: Color.urgent
                                text: "Everything on " + (root.chosenDisk ? Model.display(root.chosenDisk.model) + " (serial …" + Model.display(String(root.chosenDisk.serial).slice(-4)) + ")" : "") + " will be erased."
                            }
                            Text { width: parent.width; text: "To confirm, type the last 4 characters of its serial:"; color: root.dim; wrapMode: Text.WordWrap; font.family: root.face; font.pixelSize: Style.font.bodySmall; textFormat: Text.PlainText }
                            Row {
                                spacing: Style.space(8)
                                Field { id: serialField; width: Style.space(120); placeholderText: "····"; Keys.onReturnPressed: root.addNext() }
                                Button {
                                    anchors.verticalCenter: serialField.verticalCenter
                                    bordered: true
                                    foreground: root.ink
                                    fontFamily: root.face
                                    text: "Continue ⏎"
                                    enabled: root.serialOk
                                    opacity: enabled ? 1 : 0.4
                                    onClicked: root.addNext()
                                }
                            }
                            Text {
                                visible: serialField.text.length >= 4 && !root.serialOk
                                text: "That doesn't match this disk's serial."
                                color: Color.urgent
                                font.family: root.face
                                font.pixelSize: Style.font.bodySmall
                                textFormat: Text.PlainText
                            }
                        }

                        // step 2 · how it opens
                        NavRow {
                            visible: root.addStep === 2
                            navIndex: root.navIndex("opt", "auto")
                            selectable: root.autoAllowed
                            icon: root.autoUnlock ? "◉" : "○"
                            iconColor: root.autoUnlock ? Color.accent : root.ink
                            title: "Unlock with Omarchy"
                            pillLabel: "recommended"
                            pillTone: "ok"
                            sub: root.autoAllowed ? "Opens as soon as your OS disk is unlocked. No extra prompt. Folders can be moved onto it." : "Needs an encrypted OS disk; this one is not encrypted."
                            onChosen: root.autoUnlock = true
                        }
                        NavRow {
                            visible: root.addStep === 2
                            navIndex: root.navIndex("opt", "manual")
                            icon: !root.autoUnlock ? "◉" : "○"
                            iconColor: !root.autoUnlock ? Color.accent : root.ink
                            title: "Ask every time"
                            sub: "Opens only with the recovery passphrase. Folders can't be moved onto it."
                            onChosen: root.autoUnlock = false
                        }
                        NavRow {
                            visible: root.addStep === 2
                            navIndex: root.navIndex("opt", "erase")
                            icon: root.eraseDisk ? "󰄵" : "󰄱"
                            iconColor: root.eraseDisk ? Color.urgent : root.ink
                            title: "Erase existing partitions"
                            sub: "Needed if the disk was used before. Off: setup stops if anything is on it."
                            onChosen: root.eraseDisk = !root.eraseDisk
                        }
                        Card {
                            visible: root.addStep === 2
                            Label { text: "Mount at" }
                            Field { id: mountField; width: parent.width }
                            Label { text: "Name" }
                            Field { id: nameField; width: parent.width }
                            Label { width: parent.width; wrapMode: Text.WordWrap; text: "btrfs · zstd · TRIM through encryption off (more private)" }
                        }
                        Note {
                            visible: root.addStep === 2
                            text: "Next, a small window shows the recovery passphrase to store safely; you need it after a reinstall or on another computer. Then Omarchy asks for your password."
                        }
                        ActionRow {
                            visible: root.addStep === 2
                            op: "provision"
                            enabled: root.canWrite && root.serialOk && (!root.autoUnlock || root.autoAllowed)
                        }
                    }

                    // ================= C · move folder =================
                    Column {
                        width: content.width
                        spacing: Style.space(6)
                        visible: root.view === "move"

                        SectionHead { label: "FOLDER"; trail: "" }
                        Field { id: folderField; width: content.width; placeholderText: "~/Videos"; Keys.onReturnPressed: { catcher.forceActiveFocus(); root.cursor = 0; } }
                        SectionHead { label: "MOVE TO"; trail: "" }
                        Repeater {
                            model: root.targets.length
                            NavRow {
                                required property int index
                                readonly property var t: root.targets[index] || ({})
                                navIndex: root.navIndex("target", t.mountpoint)
                                current: t.mountpoint === root.moveTarget
                                icon: t.mountpoint === root.moveTarget ? "◉" : "○"
                                iconColor: t.mountpoint === root.moveTarget ? Color.accent : root.ink
                                title: t.title || ""
                                sub: Model.display(t.mountpoint) + " · " + Model.compact(t.free) + " free"
                                onChosen: root.moveTarget = t.mountpoint
                            }
                        }
                        Note { visible: root.targets.length === 0; text: "No drive can take a folder yet. Set up a drive that unlocks with Omarchy first." }
                        SectionHead { label: "WHAT HAPPENS"; trail: "" }
                        Repeater {
                            model: [
                                ["✓", "The path stays the same. Apps keep using it; the files live on the drive."],
                                ["✓", "It moves during a restart, when nothing else uses it. Every file is checked."],
                                ["✓", "The old copy stays until you delete it, so Undo works until then."],
                                ["!", "Open files, profiles, keyrings, databases and unreadable files are refused, never skipped."]
                            ]
                            Check {
                                required property var modelData
                                glyph: modelData[0]
                                text: modelData[1]
                                tint: modelData[0] === "!" ? Color.urgent : root.ink
                            }
                        }
                        ActionRow { op: "review"; enabled: root.canWrite && root.moveSource !== "" && root.moveTarget !== "" }
                    }

                    // ================= E/F · one move =================
                    Column {
                        width: content.width
                        spacing: Style.space(6)
                        visible: root.view === "resume" && root.chosenMove !== null
                        readonly property var words: root.chosenMove ? Model.moveText(root.chosenMove, root.pendingRestart) : ["", ""]
                        readonly property var checks: root.chosenMove ? Model.moveChecks(root.chosenMove) : []

                        Note { text: parent.words[1]; color: root.ink; font.pixelSize: Style.font.body }
                        Note { visible: text !== ""; text: root.chosenMove ? Model.moveFacts(root.chosenMove, root.storage) : "" }
                        Repeater {
                            model: parent.checks.length
                            Check {
                                required property int index
                                readonly property var c: root.chosenMove ? (Model.moveChecks(root.chosenMove)[index] || ({})) : ({})
                                glyph: c.ok ? "✓" : (c.pending ? "○" : "!")
                                text: c.text || ""
                                tint: c.ok ? root.ink : (c.pending ? root.dim : Color.urgent)
                            }
                        }
                        Card {
                            visible: root.chosenMove !== null && (root.chosenMove.state === "switched" || root.chosenMove.state === "rebooted")
                            Text {
                                width: parent.width
                                text: "Old copy · " + (root.chosenMove ? Model.compact((root.chosenMove.stats && root.chosenMove.stats.bytes) || 0) : "") + " on the OS disk"
                                color: root.ink
                                font.family: root.face
                                font.pixelSize: Style.font.body
                                textFormat: Text.PlainText
                            }
                            Text {
                                width: parent.width
                                text: root.chosenMove && root.chosenMove.canDelete ? "Keep it as a safety net, or delete it to get the space back." : "Delete becomes available after one normal restart."
                                color: root.dim
                                wrapMode: Text.WordWrap
                                font.family: root.face
                                font.pixelSize: Style.font.bodySmall
                                textFormat: Text.PlainText
                            }
                        }
                        Note {
                            visible: root.chosenMove !== null && !!root.chosenMove.error && Model.showMoveError(root.chosenMove, root.storage)
                            text: root.chosenMove ? "Last attempt: " + Model.display(root.chosenMove.error) : ""
                            color: root.chosenMove && root.chosenMove.needsAttention ? Color.urgent : root.dim
                        }
                        Item { width: 1; height: Style.space(2) }
                        Repeater {
                            model: root.chosenMove ? Model.moveActions(root.chosenMove, root.pendingRestart) : []
                            ActionRow { required property string modelData; op: modelData }
                        }
                    }

                    // ================= helper job =================
                    Column {
                        width: content.width
                        spacing: Style.space(6)
                        visible: root.view === "progress"
                        readonly property var j: Model.jobText(root.watchedJob)

                        Note { visible: parent.j.state === "running"; text: "The helper owns this step. Omarchy may ask for your password first."; color: root.ink }
                        Note { visible: parent.j.state === "failed"; text: parent.j.error || ""; color: Color.urgent }
                        Note { visible: root.jobDone && root.restartArmed; text: "Save your work, then restart to move the folder. This restart takes a little longer than usual."; color: root.ink }
                        ActionRow { visible: root.jobDone && root.restartArmed; op: "restart" }
                        ActionRow { op: "home" }
                    }

                    // ---- key hints ------------------------------------------------
                    PanelSeparator { width: content.width; foreground: root.ink }
                    Flow {
                        width: content.width
                        spacing: Style.space(14)
                        Repeater {
                            model: Model.footerHints(root.view)
                            Row {
                                required property var modelData
                                spacing: Style.space(5)
                                Text { text: modelData[0]; color: root.ink; font.family: root.face; font.pixelSize: Style.font.caption; font.bold: true; textFormat: Text.PlainText }
                                Text { text: modelData[1]; color: root.dim; font.family: root.face; font.pixelSize: Style.font.caption; textFormat: Text.PlainText }
                            }
                        }
                    }
                }
            }

            ConfirmDialog {
                id: confirm
                anchors.fill: parent
                Keys.onPressed: function(event) { if (handleKey(event)) event.accepted = true; }
                onCanceled: { opened = false; root.confirmationAction = null; catcher.forceActiveFocus(); }
                onConfirmed: { var fn = root.confirmationAction; opened = false; root.confirmationAction = null; catcher.forceActiveFocus(); if (fn) fn(); }
            }
        }
    }

    IpcHandler {
        target: "drives"
        function status(): string {
            return JSON.stringify({opened: root.opened, view: root.view, addStep: root.addStep, cursor: root.cursor, nav: root.nav, heading: root.heading,
                contentY: flick.contentY, contentHeight: flick.contentHeight, height: flick.height,
                confirmOpen: confirm.opened, confirmSelection: confirm.selectedIndex, confirmMessage: confirm.message,
                snapshot: root.storage, rates: service.rates, footer: Model.footerHints(root.view).map(function(h) { return h.join(" "); }).join(" · "),
                busy: service.busy, request: service.request.op, pending: service.pending ? service.pending.op : "", error: service.error});
        }
        function open(): void { root.open(); }
        function close(): void { root.close(); }
        function refresh(): void { service.refresh(); }
        function goto(view: string): string {
            if (view === "overview") root.go("overview");
            else if (view === "add") root.openAdd("");
            else if (view === "move") root.openMoveFolder("", "");
            else return "invalid";
            return "ok";
        }
        function selectDisk(id: string): string {
            for (var i = 0; i < root.driveRows.length; i++) if (root.driveRows[i].disk && root.driveRows[i].disk.id === id) { root.openDrive(root.driveRows[i].key); return "ok"; }
            for (var j = 0; j < root.candidates.length; j++) if (root.candidates[j].id === id) { if (!root.candidates[j].selectable) return "not-selectable"; root.openAdd(id); return "ok"; }
            return "missing";
        }
        function selectDrive(name: string): string {
            for (var i = 0; i < root.driveRows.length; i++) if (root.driveRows[i].drive && root.driveRows[i].drive.name === name) { root.openDrive(root.driveRows[i].key); return "ok"; }
            return "missing";
        }
        function selectMove(id: string): string { for (var i = 0; i < root.moves.length; i++) if (root.moves[i].id === id) { root.openMove(id); return "ok"; } return "missing"; }
        function setMove(source: string, dest: string): void { root.openMoveFolder(source, dest); }
        function reviewMove(): void { root.reviewMove(); }
        function moveAction(op: string): string { if (!root.chosenMove || Model.moveActions(root.chosenMove, root.pendingRestart).indexOf(op) < 0) return "unavailable"; root.act(op); return "ok"; }
        function reconnect(name: string): string {
            for (var i = 0; i < root.driveRows.length; i++) {
                var r = root.driveRows[i];
                if (r.drive && r.drive.name === name) { if (r.fix.action !== "reconnect") return "unavailable"; root.selectedKey = r.key; root.act("reconnect"); return "ok"; }
            }
            return "missing";
        }
        function serial(fragment: string): string { serialField.text = fragment; return root.serialOk ? "matched" : "rejected"; }
        function addNext(): string { return root.addNext() ? "ok" : "rejected"; }
        function key(k: string): string {
            if (k === "j") root.moveCursor(1); else if (k === "k") root.moveCursor(-1); else if (k === "enter") root.activate(); else if (k === "esc") root.back(); else return "invalid";
            return "ok";
        }
    }
}
