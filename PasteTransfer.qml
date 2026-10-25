import QtQuick
import Quickshell.Io
import "Model.js" as Model

Item {
    id: root
    visible: false
    required property var targetField
    property bool allowed: false
    property string contextKey: ""
    property var ticket: null
    property int epoch: 0
    signal failed(string message)
    readonly property string supervisorPath: decodeURIComponent(Qt.resolvedUrl("supervisor.py").toString().replace(/^file:\/\//,""))
    readonly property string pastePath: decodeURIComponent(Qt.resolvedUrl("paste.py").toString().replace(/^file:\/\//,""))
    function cancel() {
        epoch++; ticket = null;
        if (worker.running) worker.signal(15);
    }
    function start() {
        if (worker.running || !allowed) return;
        ticket = {epoch: ++epoch, key: contextKey, text: targetField.text,
                  cursor: targetField.cursorPosition, start: targetField.selectionStart, end: targetField.selectionEnd};
        worker.command = Model.boundedArgv(["/usr/bin/python3", "-B", pastePath], supervisorPath, 1.2, 524288);
        worker.running = true;
    }
    function complete(code) {
        const old = ticket; ticket = null;
        if (!old || old.epoch !== epoch || !allowed || old.key !== contextKey
                || old.text !== targetField.text || old.cursor !== targetField.cursorPosition
                || old.start !== targetField.selectionStart || old.end !== targetField.selectionEnd) return;
        if (code !== 0) { failed("Clipboard transfer timed out or was too large. Nothing was pasted."); return; }
        try {
            const value = JSON.parse(output.text);
            if (!value.ok || typeof value.text !== "string") throw new Error(value.error || "Clipboard text is unavailable.");
            targetField.insertText(value.text);
        } catch (e) { failed(String(e)); }
    }
    onAllowedChanged: if (!allowed) cancel()
    onContextKeyChanged: cancel()
    Connections {
        target: root.targetField
        function onTextChanged() { if (root.ticket) root.cancel(); }
        function onCursorPositionChanged() { if (root.ticket) root.cancel(); }
        function onSelectionStartChanged() { if (root.ticket) root.cancel(); }
        function onSelectionEndChanged() { if (root.ticket) root.cancel(); }
    }
    Timer { interval: 1800; running: worker.running; onTriggered: root.cancel() }
    Process {
        id: worker
        stdout: StdioCollector { id: output; waitForEnd: true }
        onExited: function(code, status) { root.complete(code); }
    }
}
