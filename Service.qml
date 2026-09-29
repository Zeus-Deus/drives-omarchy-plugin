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
                    else if (request.op === "status") { snapshot=result; loaded=true; }
                    else { error=""; finished(result); }
                } catch(e) { error="Invalid storage bridge response."; }
            }
        }
        suppress = false;
        var next=pending;pending=null;
        if(next) Qt.callLater(function(){root.submit(next);});
    }
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
