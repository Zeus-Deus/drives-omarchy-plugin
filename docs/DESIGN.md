# Drives build contract

Plugin ID `io.github.zeus-deus.drives`. Native Omarchy Quattro bar + KeyboardPanel; no root, sudo, pkexec or secret fields in QML. Persist source here; execute tests/build/install in session-owned Omarchy VM only. No automatic privileged install hooks. State is root-owned `/var/lib/drives-helper/{moves,drives}`. Product must refuse auto-unlock key storage on unencrypted root; test-only override is root-owned service environment, never a D-Bus parameter.

## Correcting the proposal
Polkit grants authorization, not LUKS secret input. A separate unprivileged GTK4 `helper/agent.py` collects recovery secrets in a password field, seals a memfd (all write/resize seals) and passes its FD via D-Bus UnixFDList. QML receives no secret. Recovery uses the same agent and udisks2 Encrypted.Unlock. No secrets in argv, environment, disk fixtures, D-Bus string args or logs. A disposable test-generated passphrase may be sent through an anonymous pipe/memfd by E2E, never printed.

## Read-only bridge
`/usr/bin/python3 bridge.py` takes one bounded JSON request on stdin, emits one bounded JSON response, no stderr secrets. Requests: `{op:"status"}`, `{op:"probe",path:...}`, `{op:"start_move",src:...,destMount:...}`, `{op:"resume_move"|"rollback_move"|"delete_old_copy"|"cancel_move"|"restart_move",id:...}`. Writes use helper.client. `{ok:true,...}` or `{ok:false,error:...}`. Status fields: `disks` (id/byId,name,model,serial,size,system,encrypted,mounts,state,selectable), `mounts` (target,source,fsroot,fstype,options), `moves`, `drives`, `helperAvailable`, `rootEncrypted`, `error`. Display sanitization strips controls/bidi/newline, but raw identifiers stay byte-exact internally. Read mountinfo first; never stat autofs paths. Resolve inverse lsblk chains, fail closed for unsupported multi-device/LVM/md/loop/network topologies. Poll 1 second while open; zero background shell polling when closed. Cap command output at ingestion, deadlines cover worst-case commands.

## System D-Bus
Bus `io.github.zeus_deus.Drives`, object `/io/github/zeus_deus/Drives`, interface same as bus.
Read: `Status() -> s` JSON `{moves:[],drives:[],version:"0.1.0"}`; no auth.
Writes authorize caller unique bus name against polkit with `AllowUserInteraction`, per-op `io.github.zeus-deus.drives.<action>` and auth_admin (not keep). Re-derive every condition; never trust panel booleans. Fail closed on missing auth, malformed JSON, unknown fields and secrets passed by string. Methods:
- `ProvisionDrive(s request,h secretFd) -> s`: request keys `byId,serial,name,mountpoint,erase,confirmation,autoUnlock`; schedule background provisioning and return `{ok:true,id:...}`. Secret FD must be sealed memfd bounded 8..4096 bytes. Serial identity and fragment confirmation checked again in helper; system/mounted disks excluded. Provision via udisks for supported format/config operations, preserve own journalling around each irreversible step. `helper/provisioning.py`: `provision(request,secret,common)` and `inspect(common)`; common provides `run`, `journal`, `state_dir`, `boot_id`. Interface integration may adapt explicitly in this document before merging.
- `StartMove(s src,s destMount) -> s` schedules and returns `{ok:true,id:...}`.
- `ResumeMove(s id)`, `RollbackMove(s id)`, `DeleteOldCopy(s id)`, `CancelMove(s id)`, `RestartMove(s id)` -> s.
- `ExportHeaderBackup(s name,s destination) -> s` requires separate auth, safe absolute regular-file destination, refuses overwrites/symlinks.

`helper/client.py`: functions `status()` and `call(method,args,secret_fd=None)` return decoded JSON; lazy GI imports. `helper/agent.py` CLI `provision --by-id <...> --serial <...> --name <...> --mountpoint <...> --confirmation <...> [--erase] [--manual]` shows actual model/serial, secret + confirmation fields only in GTK; never root. `unlock --device <udisks-object-path>` uses udisks with entered secret. QML closes before launching external window. `helper/install.sh` explicitly user-invoked privileged deployment; dependencies and uninstall documented, no side effects when imported.

## Moves
`helper/moves.py`: `MoveManager(common)` with methods `start(src,destMount)`, `resume(id)`, `rollback(id)`, `delete_old(id)`, `cancel(id)`, `restart(id)`, `inspect()`; operations return dict. Caller authorization is service responsibility; manager independently validates paths, mount topology, UID/protected paths, open FDs, unreadable ownership subtrees, nested mounts, encrypted destination with >=1.2x source size, target unique. Deny profiles/credentials/databases/system paths and symlinked ancestors. ACL/hardlink/xattr/sparse/odd-byte fidelity uses rsync -aHAXS --numeric-ids. Verify checksum dry run + metadata and counts, no silent skip. Git worktree references must remain valid through the unchanged source bind path or be safely repaired. Never execute arbitrary consuming-app shell command from caller.

The legacy live-copy/cutover procedure is disabled: new normal-session moves journal `awaiting-maintenance` with the original still in use and `verified=false`. Legacy live verification never authorizes cleanup, even after a reboot. Interrupted legacy switches keep both copies and require explicit maintenance inspection; no journal is silently upgraded. Protocol 2 is reserved for the new offline/quarantine implementation and is currently refused, not treated as qualified functionality. Cancel/Start over additionally require a pre-switch phase, an unchanged original, no saved cutover/placeholder/verification and no active bind: a missing backup alone never makes the destination disposable. Legacy normal-session Undo is disabled as well; it retains both copies for maintenance inspection. The internal quarantine primitives now reject outside regular-file and symlink hardlinks, prepare a durable root-private store on the same filesystem/subvolume, and rename the original without replacing any existing entry while preserving inode/owner/mode/mtime. They do not authorize exclusion, run automatically, or provide a public cutover path. Final offline copy, bind activation, rollback and cleanup must obey the maintenance contract below. Journals remain fsync + atomic rename + directory fsync before actions; startup inspects only. Keep source, destination, parent and quarantine filesystem/subvolume/inode identities plus a cutover boot ID and saved verification. Post-switch rollback must refuse destination divergence rather than discard new files; cleanup never promises reclaimed space if snapshots retain old data.

## Maintenance-mode migration (approved scope revision)

Ordinary-folder cutover requires explicit downtime. A live copy is only a seed;
it never authorizes activation or cleanup. Normal-session StartMove prepares a
journal and returns `awaiting-maintenance`, without renaming the live source.
An independently authorized maintenance request selects the pending journal;
users save work and reboot deliberately. The plugin never silently logs out,
kills applications, or isolates a live desktop target.

A root-owned early systemd generator reads the root-filesystem latch
`/drives-maintenance-request.json` and selects the dedicated maintenance target
before the graphical target, user managers, timers, containers or ordinary
writer services can start. Inaccessible/missing `/var` journals must never hide
a root boot latch: hold the gate closed and require administrative inspection.
The gate survives reboot until explicitly released.
Boot performs inspection only: no copy, resume, rollback or cleanup is automatic.
The maintenance console requires a separate explicit action for Continue/Undo.
The helper must verify the boot receipt, selected journal, minimal service
allowlist, absence of user sessions/writers, and current storage identity.
Open-descriptor/mapping scans are additional diagnostics, not writer exclusion.

Current implementation adds an explicitly started, read-only
`drives-maintenance-audit.service` (no boot enablement). It checks the selected
root-private latch/receipt against the current boot, actual target dependencies
and masks, pending systemd jobs, services/sockets/timers/paths, session records,
and process UID/executable/cgroup identity. Its observation is not a maintained
writer lease and is not connected to migration. The installed service refuses
an ordinary guest boot without changing fstab/crypttab, the helper PID or the
running desktop. A guest cold-boot test of the installed service also accepts
an inspect-only maintenance snapshot. A dedicated, bounded
`drives-maintenance-splash.service` quits and waits for the initramfs boot screen
before the minimal target; it does not pull normal boot targets or admit a
residual/deleted Plymouth executable. The test uses a QA-only boot trigger and
UART reporting/recovery, preserves the source and journal, and does not grant a
writer lease. Current packaging also installs a standalone normal-boot guard
and persistent `sysinit.target` Requires/After drop-in. It checks only the
presence of root latch/runtime controls (including malformed/dangling entries),
without helper imports or journal parsing. Missing guard program or generator
must retain the normal-boot failure barrier. This is a once-per-normal-boot
check, never a way to isolate an already-running session or establish a writer
lease. Maintained activation exclusion, storage admission and the actual offline
worker remain unqualified. Earlier inspect-only target probes alone do not
qualify the audit.
The explicit uninstaller refuses any root latch or maintenance runtime entry
(even empty, malformed or dangling), and any maintenance target state other than inactive. It checks
again after stopping the ordinary helper, before disabling it or removing the
recovery units/generator. A refused late request can leave the helper stopped,
with its enabled unit and recovery files retained. Quiet uninstall removes only
owned infrastructure; journals, keys and storage configuration remain. This is
not a mechanism for releasing a maintenance gate or establishing exclusion.

Under exclusion, the source moves to a same-filesystem root-private quarantine
with stable root-controlled ancestry. Reject external hardlinks (including
hardlinked symlinks), nested mounts and unsupported fidelity. Copy/reconcile and
fully verify the quarantined original before exposing the destination. Save the
post-exclusion verification, fsync destination data/metadata, then activate and
verify the bind. Keep quarantine inaccessible through normal boot until a
separately confirmed cleanup after reboot. Ambiguous crash recovery keeps the
gate closed and both copies retained. Rollback requires exclusion and refuses
post-cutover destination divergence. No containing-home snapshot may silently
include unrelated profiles or credentials.

Maintenance support is not qualified until real guest boot exclusion, explicit
recovery, late-write preservation and the revised crash matrix pass. The prior
normal-session migration proof does not establish this stronger contract.

## Tests/evidence
Strict vertical TDD; RED then GREEN logs. All executable gates inside VM with boot identity recorded. Evidence directory is managed session artifact dir (provided by parent). All root storage operations only guest; QMP attachment/reset approved only for own VM. Tests cannot introduce production auth bypasses. Read-only tests no polkit; privileged VM fixture setup may use guest sudo, but real E2E must additionally verify D-Bus auth behavior and GTK handoff. Report unavailable encrypted-OS qualification separately (stock Realm OS root is plaintext Btrfs). No publication without explicit request. Commit only own files, no AI author attribution; parent owns integration commits.
