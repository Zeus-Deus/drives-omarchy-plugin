"""Frozen rule bodies shipped before ownership receipts (full reachable history).

Only the named placeholders vary. The entire rendered byte string is compared;
recognizing a substring, prefix, comment or syntactically similar rule is unsafe.
"""
FINAL_V1=('# Drives plugin: {name} unlocks with the OS or via the Drives panel, not desktop automount.\n',
          'SUBSYSTEM=="block", ENV{ID_FS_TYPE}=="crypto_LUKS", ENV{ID_FS_UUID}=="{uuid}", ENV{UDISKS_IGNORE}="1"\n')
FINAL_V2=FINAL_V1+('SUBSYSTEM=="block", ENV{DM_UUID}=="CRYPT-LUKS?-{compact}-*", ENV{UDISKS_IGNORE}="1"\n',)
SETUP_V1=('# Drives plugin: {name} is being set up; removed when setup finishes.\n',
          'SUBSYSTEM=="block", ENV{ID_SERIAL_SHORT}=="{serial}", ENV{UDISKS_IGNORE}="1"\n')

def render(parts,**values):
    # str.format would also interpret the literal udev ENV{...} braces.
    text=''.join(parts)
    for key,value in values.items():text=text.replace('{'+key+'}',value)
    return text.encode()
