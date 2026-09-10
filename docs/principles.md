<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Shared practice for city data and tools

**Extend open-source culture to city data and the tools that support it.**
Use shared work, return what you learn, and improve it together.

| Shared cycle | Improve city data | Improve shared tools and practice |
|---|---|---|
| Use | Work with CityGML | Use common processing and checks |
| Report findings | Describe errors or changes in buildings | Describe missed problems, excessive warnings or unsupported cases |
| Verify and improve | Review evidence, check and approve a data PR | Reproduce a case, improve processing, checks and explanations |
| Share again | Publish updated data | Deliver improvements in a common release |

Cities record problems and their resolution in their usual Issues and PRs.
The 4dcitygml side gathers reusable cases and handles the additional work of
organizing them for common improvements. A city can also consult 4dcitygml
directly when it cannot resolve a problem on its own. Reporting a problem does
not require knowing its cause or how to implement a fix.

Work that a city can complete independently proceeds; work that depends on a
common fix is resolved together. Reproduction establishes what a process did,
not whether the underlying source or interpretation is correct. Adoption still
requires the appropriate human review. Improving a tool and approving a city's
data are separate decisions.

The operational layer connects individual city cases with common standards:
it makes standards usable as tools and checks, turns local experience into
reusable improvements, and distinguishes data and implementation errors from
questions about a standard. Where appropriate, reproducible cases and
requirements can be provided to standards developers. Decisions on standards
belong to those developers. This describes the project's intended contribution,
not an existing formal relationship with a standards organization.

Implementation is incremental. A successful pilot does not unlock arbitrary
bulk updates. See [implementation status](implementation-status.md) for current
limits and [source distribution](shared-tooling-release.md) for delivery.
