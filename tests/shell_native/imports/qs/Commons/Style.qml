pragma Singleton
import QtQuick
QtObject {
 property QtObject font: QtObject { property string family: "monospace"; property int body: 14 }
 property QtObject spacing: QtObject { property int inputPaddingY: 5; property int controlPaddingX: 8 }
 property int cornerRadius: 6
 function space(n) { return n }
 function controlFill(f,h,i,a) { return "#222222" }
 function selectionFillFor(i,a) { return "#555577" }
}
