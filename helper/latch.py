"""Fixed root-only writer for the root-filesystem maintenance request.

The long-running helper runs with ProtectSystem=strict and cannot create files
in /, so it starts this module in a fresh, narrowly writable transient unit.
"""
import re,sys
from helper import maintenance
from helper.common import Failure

def arm(move_id,action):
    if not isinstance(move_id,str) or not re.fullmatch('[a-f0-9]{32}',move_id):raise Failure('invalid maintenance move id')
    current=maintenance.boot_id()
    if action=='return':
        move=maintenance.control_json(maintenance.STATE/'moves'/(move_id+'.json'),1024*1024)
        maintenance.validate_move({'moveId':move_id,'action':action,'armedBootId':current},move)
    return maintenance.write_latch(move_id,action,current)

if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='arm':arm(sys.argv[2],sys.argv[3])
    elif len(sys.argv)==4 and sys.argv[1]=='clear':maintenance.clear_latch(sys.argv[2],sys.argv[3])
    else:raise Failure('invalid maintenance request invocation')
