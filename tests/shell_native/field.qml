import QtQuick
import "../.." as Drives
Item {
    width: 420; height: 180
    property int returned: 0
    property int escaped: 0
    property int pastes: 0
    Drives.EditField {
        id: field; objectName: "field"; width: 350; y: 10
        Keys.onReturnPressed: parent.returned++
        Keys.onEscapePressed: parent.escaped++
        onPasteRequested: parent.pastes++
    }
    Drives.EditField { objectName: "other"; width: 350; y: 80 }
}
