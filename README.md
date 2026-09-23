# Azure Cost Watchdog

Azure Function that posts daily cost changes and anomalies to Slack or Microsoft Teams.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Status: in development](https://img.shields.io/badge/status-in%20development-orange)

> **Status:** in active development. Star or watch the repo to follow progress.

## Features

- Daily summary of spend by subscription, resource group and service
- Day-over-day and week-over-week change detection
- Anomaly alerts when spend crosses a configurable threshold
- Slack and Microsoft Teams notifications
- One-command deployment with managed identity (no stored secrets)

## Tech stack

- Azure Functions (Python)
- Azure Cost Management API
- Slack / Teams webhooks
- Bicep for deployment

## Getting started

Setup instructions will be added with the first release.

## Roadmap

- [ ] Initial release
- [ ] Documentation and examples
- [ ] CI and automated tests

Have an idea? [Open an issue](https://github.com/calliarc/azure-cost-watchdog/issues).

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © 2026 CalliArc

---

Built and maintained by [CalliArc](https://www.calliarc.com/). Need help with Azure cloud services? [Talk to our team](https://www.calliarc.com/services/azure-cloud-migration/).
