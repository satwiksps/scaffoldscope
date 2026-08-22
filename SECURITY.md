# Security policy

## Supported versions

Security fixes are provided for the latest 1.x release. Version 0.x no longer
receives fixes.

| Version | Supported |
|---|---|
| 1.x | Yes |
| 0.x and unreleased snapshots | No |

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/satwiksps/scaffoldscope/security/advisories/new).
Do not open a public issue, discussion, or pull request containing exploit details,
credentials, private traces, or embargoed findings.

A useful report includes:

- the affected version and operating system;
- the smallest reproducible configuration or bundle;
- the expected and observed security boundary;
- impact and realistic attack prerequisites; and
- suggested mitigations, if known.

Remove API keys, proprietary repositories, and personal data. If evidence cannot
be safely minimized, describe it first and wait for handling instructions.

The maintainer aims to acknowledge a report within three business days and give
an initial assessment within seven. Complex sandbox or supply-chain findings may
take longer. Please allow a coordinated fix and release before public disclosure.
The project does not currently operate a bug-bounty program.

## Security boundaries

Reports are particularly useful for credential disclosure, path traversal,
sandbox escape, command injection, unsafe archive handling, dependency or release
compromise, and evidence-integrity failures that can silently misstate results.

The local sandbox is an execution convenience, not a hostile-code security
boundary. Model-generated code and evaluator commands should be treated as
untrusted. The Docker backend reduces exposure when configured as documented but
does not replace host hardening, least-privilege credentials, or independent
review of tasks and images.
