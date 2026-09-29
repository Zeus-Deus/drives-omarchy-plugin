const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const source=()=>fs.readFileSync(process.env.DRIVES_QML_SOURCE||path.join(__dirname,'../Service.qml'),'utf8');
test('Process stdin uses the shipped Quickshell API, not QProcess private methods',()=>assert.ok(!source().includes('closeWriteChannel'),'unsupported Process method prevented all bridge replies'));
test('Python children cannot write bytecode into the watched plugin tree',()=>assert.match(source(),/"\/usr\/bin\/python3","-B"/));
