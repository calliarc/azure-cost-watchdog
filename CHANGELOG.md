# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-23

First working release.

### Added

- Daily summary of spend by subscription, resource group and service
- Day-over-day and week-over-week change detection
- Anomaly alerts when spend crosses a configurable threshold (percent and absolute, compared with a rolling average)
- Slack (Block Kit) and Microsoft Teams (Adaptive Card) notifications
- One-command deployment with managed identity (no stored secrets)

[Unreleased]: https://github.com/calliarc/azure-cost-watchdog/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/calliarc/azure-cost-watchdog/releases/tag/v0.1.0
