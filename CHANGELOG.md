# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning]
(https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Added support for multiple networks in service definitions.
- Containers now connect to additional defined networks after starting.
- New `Network` model and configuration parsing for `networks` section.
- Added properties to `ServiceDefinition` model.

## [0.0.2] - 2026-02-01

### Added
- Added and updated footprinting functionality.
- Added URL reporter for footprinting.

### Changed
- Updated CLI and API for better service management.
- Improved `list_configured_services` CLI action to output profile and
  variety information.
- Updated Ozwald configuration location logic.
- Various updates to footprinting logic.
- Documentation updates in README.

### Fixed
- Fixed footprint issues and container start behavior.
- Hardened code around `OZWALD_SYSTEM_KEY` handling.
- Miscellaneous footprinting fixes.

## [0.0.1] - 2025-12-23

### Added
- Initial release of Ozwald: a multi-tool for container provisioning.
