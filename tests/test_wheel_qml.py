"""Exercise the panel's actual WheelHandler in Qt, without a live desktop.

The step matches the working PassPage list (three two-line rows), not three
single-line popup rows. A direct wheel step must also cancel any kinetic flick
already in flight so Qt cannot move the position again after the handler.
"""
import os
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = '/usr/lib/qt6/bin/qmltestrunner'


def qml_block(source, marker):
    start = source.index(marker)
    pos = source.index('{', start)
    depth = 1
    end = pos + 1
    while depth:
        if source[end] == '{':
            depth += 1
        elif source[end] == '}':
            depth -= 1
        end += 1
    return source[start:end]


@pytest.mark.skipif(not os.path.exists(RUNNER), reason='Qt6 Quick Test runner required')
def test_actual_panel_wheel_handler(tmp_path):
    panel = (ROOT / 'Panel.qml').read_text()
    handler = qml_block(panel, 'WheelHandler {')
    step = re.search(r'^\s*readonly property (?:real|int) wheelStep: ([^\n]+)', panel, re.M)
    step_property = 'property real wheelStep: ' + (step.group(1) if step else '84')
    # Import the implementation itself, never a second copy of its arithmetic.
    shutil.copyfile(ROOT / 'Model.js', tmp_path / 'Model.js')
    qml = '''import QtQuick
import QtTest
import "Model.js" as Model

Item {
    id: root
    width: 400; height: 400
    %s
    QtObject {
        id: style
        property QtObject spacing: QtObject {
            property real popupRowHeight: 28
            property real rowPaddingX: 12
        }
        function space(px) { return px; }
    }
    Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width; contentHeight: 6000
        boundsBehavior: Flickable.StopAtBounds
        %s
        Rectangle { width: 400; height: 6000; color: "grey" }
    }
    TestCase {
        name: "DrivesWheel"
        when: windowShown
        function init() { flick.cancelFlick(); flick.contentY = 1000; wait(30); }
        function test_one_notch_matches_working_passpage() {
            mouseWheel(flick, 200, 200, 0, -120);
            wait(400);
            compare(flick.contentY, 1150);
        }
        function test_fast_notches_accumulate() {
            for (var i = 0; i < 5; i++) { mouseWheel(flick, 200, 200, 0, -120); wait(20); }
            wait(400);
            compare(flick.contentY, 1750);
        }
        function test_high_resolution_notch() {
            for (var i = 0; i < 8; i++) mouseWheel(flick, 200, 200, 0, -15);
            wait(400);
            compare(flick.contentY, 1150);
        }
        function test_wheel_stops_an_existing_flick() {
            flick.flick(0, -1500);
            wait(30);
            verify(flick.flicking, "fixture must have a kinetic flick in flight");
            mouseWheel(flick, 200, 200, 0, -120);
            verify(!flick.flicking, "wheel must cancel kinetic motion before stepping");
            var at = flick.contentY;
            wait(400);
            compare(flick.contentY, at, "no delayed animation may override the wheel position");
        }
        function test_clamps_to_both_ends() {
            flick.contentY = 0;
            mouseWheel(flick, 200, 200, 0, 120);
            wait(100); compare(flick.contentY, 0);
            flick.contentY = flick.contentHeight - flick.height;
            mouseWheel(flick, 200, 200, 0, -120);
            wait(100); compare(flick.contentY, flick.contentHeight - flick.height);
        }
    }
}
''' % (step_property, handler)
    # Only the theme object is substituted; the production handler is unedited.
    qml = qml.replace('Style.', 'style.')
    file = tmp_path / 'tst_drives_wheel.qml'
    file.write_text(qml)
    result = subprocess.run(
        [RUNNER, '-input', str(file)], capture_output=True, text=True, timeout=30,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_QUICK_BACKEND': 'software'},
    )
    assert result.returncode == 0, result.stdout + result.stderr
