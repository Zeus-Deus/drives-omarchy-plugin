"""Fixed root-only writer for the root-filesystem maintenance request.

The long-running helper runs with ProtectSystem=strict and cannot create files
in /, so it starts this module in a fresh, narrowly writable transient unit.
"""
import sys
from helper import maintenance
from helper.common import Failure

if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='arm':maintenance.write_latch(sys.argv[2],sys.argv[3])
    elif len(sys.argv)==4 and sys.argv[1]=='clear':maintenance.clear_latch(sys.argv[2],sys.argv[3])
    else:raise Failure('invalid maintenance request invocation')
