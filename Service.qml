import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

Item {
    id: root
    visible: false
    property bool opened: false
    property bool busy: false
    property bool loaded: false
    property bool suppress: false
    property var request: ({op:"status"})
    property var pending: null
    property var snapshot: ({disks:[],moves:[],drives:[],jobs:[],helperAvailable:false})
    property string error: ""
    property var rates: ({})
    readonly property bool mutating: busy && request.op !== "status"
    readonly property string bridgePath: decodeURIComponent(Qt.resolvedUrl("bridge.py").toString().replace(/^file:\/\//,""))
    readonly property string agentPath: decodeURIComponent(Qt.resolvedUrl("helper/agent.py").toString().replace(/^file:\/\//,""))
    signal launching()
    signal finished(var result)

    function refresh() { if (opened && !busy) submit({op:"status"}); }
    function submit(req) {
        if (busy) {
            if (request.op === "status" && req.op !== "status") {
                pending = req; suppress = true;
                if (worker.running) worker.signal(15);
            }
            return;
        }
        request = req; busy = true; suppress = false;
        worker.command = Model.boundedArgv(["/usr/bin/python3","-B",bridgePath]);
        deadline.interval = req.op === "status" ? 10000 : 150000;
        deadline.restart(); worker.stdinEnabled=true; worker.running = true;
    }
    function complete(code) {
        if (!busy) return;
        deadline.stop(); busy = false;
        if (!suppress) {
            if (code !== 0) error = code === 90 ? "Storage response exceeded the safe size limit." : "Storage bridge could not run (exit " + code + ").";
            else {
                try {
                    var result = JSON.parse(output.text);
                    if (!result.ok) error = Model.display(result.error);
                    else if (request.op === "status") { rates=Model.ioRates(snapshot,result); snapshot=result; loaded=true; }
                    else { error=""; finished(result); }
                } catch(e) { error="Invalid storage bridge response."; }
            }
        }
        suppress = false;
        var next=pending;pending=null;
        if(next) Qt.callLater(function(){root.submit(next);});
    }
    // ---- folder sizes (read-only du, as this user, streamed line by line) ----
    // Measuring a full home folder can take a minute or two, so it runs in its
    // own process, never blocks status polling, and is kept for 10 minutes.
    property var sizes: Model.emptySizes()
    property bool sizing: sizer.running
    property real sizedAt: 0
    property var sizeRequest: ({op: "sizes", paths: []})
    property var queuedPaths: null
    // A new set of folders to measure (e.g. the drive list just loaded)
    // rescans; the same set within 10 minutes reuses the last result.
    function scanSizes(paths, force) {
        paths = (paths || []).slice(0, 16);
        var same = JSON.stringify(paths) === JSON.stringify(sizeRequest.paths);
        if (sizer.running) { if (!same || force) queuedPaths = paths; return; }
        if (!force && same && sizedAt > 0 && Date.now() - sizedAt < 600000 && sizes.done) return;
        sizeRequest = {op: "sizes", paths: paths};
        sizes = Model.emptySizes(true);
        sizer.command = Model.boundedArgv(["/usr/bin/python3", "-B", bridgePath, "--sizes"]);
        sizer.stdinEnabled = true; sizer.running = true;
    }
    Process {
        id: sizer
        stdinEnabled: true
        stdout: SplitParser { onRead: function(line) { root.sizes = Model.addSizeLine(root.sizes, line); } }
        onStarted: { sizer.write(JSON.stringify(root.sizeRequest) + "\n"); sizer.stdinEnabled = false; }
        onExited: function(code, status) {
            if (!root.sizes.done) root.sizes = Model.addSizeLine(root.sizes, JSON.stringify({done: true, complete: false}));
            root.sizedAt = Date.now();
            var next = root.queuedPaths; root.queuedPaths = null;
            if (next !== null && root.opened) Qt.callLater(function() { root.scanSizes(next, true); });
        }
    }
    Timer { interval: 170000; running: sizer.running; onTriggered: if (sizer.running) sizer.signal(15); }

    function launch(args) {
        launching();
        Quickshell.execDetached(["/usr/bin/python3","-B",agentPath].concat(args));
    }
    onOpenedChanged: {
        if (opened) refresh();
        else if (busy && request.op === "status") { suppress=true; if(worker.running) worker.signal(15); }
    }
    Timer { interval:1000; repeat:true; running:root.opened; onTriggered:root.refresh(); }
    Timer {
        id:deadline
        onTriggered: { if(worker.running)worker.signal(9);root.complete(-1); }
    }
    Process {
        id:worker
        stdinEnabled:true
        stdout: StdioCollector { id:output; waitForEnd:true }
        onStarted: { worker.write(JSON.stringify(root.request)+"\n");worker.stdinEnabled=false; }
        onExited: function(code,status) { root.complete(code); }
        onRunningChanged: if(!running && root.busy) Qt.callLater(function(){if(!worker.running && root.busy)root.complete(-1);});
    }
}
