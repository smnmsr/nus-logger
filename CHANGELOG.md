# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2025-09-11

### Added

- Advertisement / scan response filtering enabled by default requiring the Nordic UART Service UUID; new `--adv-filter / --no-adv-filter` CLI flag and `LoggerSettings.require_adv_nus` to control it.
- Early scan termination when a preferred address substring is observed (used internally to speed up reconnect loops).
- Support scanning without a `--name` filter (wildcard mode) – name now defaults to empty string instead of requiring a parameter.
- Wizard flow enhancements: ability to disable advertisement filter mid-flow; clearer display of unnamed devices.
- New `NUSClient.connect_discovered` method allowing a two-step scan-then-connect pattern for custom selection logic.
- Added test ensuring `--filter-addr` can be used without specifying a name.

### Changed

- Connection logic refactored: initial scan and selection separated from connection to improve UX and provide warnings when multiple candidates match.
- `NUSClient.scan` now returns devices even if they have an empty name when wildcard scanning; includes new parameters `early_addr_substring` and `require_adv_nus`.
- Improved reconnection loop: re-scan leverages early address substring matching for faster recovery.
- README updated with new CLI flags, troubleshooting hint, and dedicated section explaining advertisement filtering.

### Fixed

- Better handling of devices omitting advertised names (no longer silently excluded when scanning without a name filter).

### Internal

- Additional logging around early scan termination and selection.

## [0.1.3] - 2025-09-09

- Previous release (see git history for details).

[0.2.0]: https://pypi.org/project/nus-logger/0.2.0/
[0.1.3]: https://pypi.org/project/nus-logger/0.1.3/
