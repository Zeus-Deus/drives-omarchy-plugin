# Verification — 0.2.0

This release was checked in an isolated Omarchy virtual machine using synthetic
files and virtual disks. The user's desktop installation and real storage were
not used for write tests.

## Completed checks

- Full Python suite: 767 passed, 16 skipped.
- All Node tests, including the opt-in native process-tree tests: 154 passed,
  no failures or skips.
- QML validation and lint: no errors; five existing dynamic-style warnings.
- GTK component tests: nine passed. These are not interactive authorization tests.
- Upgrade and repeat installation preserved existing drive configuration, keys,
  journals and moved-folder data.
- Real maintenance moves preserved file contents, ownership, modes, ACLs,
  extended attributes, symlinks, internal hardlinks and inert special files.
- Explicit shared-hardlink consent was required. The moved copy became
  independent while outside aliases remained intact.
- Unchanged Undo restored the original. Undo after edits refused the outdated
  restore and kept the latest files active.
- Old-copy deletion was interrupted by terminating the exact helper process.
  The restarted helper automatically completed the already-authorized deletion;
  the latest data and outside alias were unchanged.
- Explicit uninstall/reinstall retained configuration, keys, recovery material,
  journals, automount rules and the active moved folder. The ordinary-user
  read-only bridge remained usable while the helper was unavailable.
- Move back after old-copy deletion returned all 10,016 latest fixture entries
  to the original filesystem. Contents and metadata matched, including SQLite
  integrity, the newest database row, new files and deletions. The bind and owned
  fstab entry were removed; the SSD copy was retained.
- Installed helper files and protected configuration were checked before and
  after the final suites. Both complete latest-data copies remained unchanged.
- The public source was scanned with pinned Gitleaks 8.30.0 with no findings.
  Published screenshots contain synthetic data and were checked for visible
  identifiers and metadata.

## Limits

- The final interactive GTK/polkit approval/cancellation flows were not
  independently re-qualified end to end. Component tests, actual D-Bus callers,
  authorization-boundary checks and backend operations were exercised instead.
- Actual abrupt-power-loss testing across every maintenance stage, and the
  complete cross-filesystem-type recovery matrix, were not completed. Recovery
  behavior beyond the exercised cases is a design and component-test claim,
  not a guarantee established by a full crash matrix.
- The guest's extra disks were virtual USB devices. This does not certify
  real-hardware SMART support, NVMe hotplug, drive failures or every controller.
- The publication screenshots cover one theme; other themes were not freshly
  screenshot-qualified. The existing Omarchy theme tokens are used.
- No test can guarantee recovery from failing hardware, uninterruptible kernel
  I/O or every possible interruption. Keep independent backups. On an ambiguous
  recovery state, retain every copy and follow the administrator-recovery
  guidance in [the design](DESIGN.md#interruptions-and-recovery).

This records the tested scope. Marketplace listing and automated validation do
not constitute a security audit or a guarantee against data loss.
