import pathlib,sys,ast,types
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))

def test_clipboard_marks_recovery_secret_sensitive_and_clears_owned_value():
    source=pathlib.Path(__file__).resolve().parents[1]/'helper'/'agent.py'
    node=next(n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.FunctionDef) and n.name=='copy_value')
    class Clipboard:
        content=None
        def set_text(self,s):self.content=s
        def set_content(self,p):self.content=p
        def get_content(self):return self.content
    clipboard=Clipboard();timers=[];labels=[]
    class Provider:
        @staticmethod
        def new_for_value(v):return ('text',v)
        @staticmethod
        def new_for_bytes(m,b):return (m,b)
        @staticmethod
        def new_union(p):return p
    class Value:
        def init(self,t):pass
        def set_string(self,s):self.value=s
    ns={'Gdk':types.SimpleNamespace(Display=types.SimpleNamespace(get_default=lambda:types.SimpleNamespace(get_clipboard=lambda:clipboard)),ContentProvider=Provider),
        'GObject':types.SimpleNamespace(Value=Value),
        'GLib':types.SimpleNamespace(Bytes=types.SimpleNamespace(new=lambda b:b),timeout_add_seconds=lambda n,f:timers.append((n,f))),
        'secret':types.SimpleNamespace(get_text=lambda:'generated-unit-fixture'), 'copy':types.SimpleNamespace(set_label=labels.append)}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns);ns['copy_value'](None)
    assert isinstance(clipboard.content,list) and any(p[0]=='x-kde-passwordManagerHint' for p in clipboard.content)
    assert timers and timers[0][0]==20
    timers[0][1]();assert clipboard.content is None
    ns['copy_value'](None);clipboard.content='new user clipboard';timers[-1][1]()
    assert clipboard.content=='new user clipboard'
