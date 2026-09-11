[日本語](./README.md)

# OptiBuddy

A general-purpose business optimization tool: define the reality of your operation as a DSL (Domain Specific Language), and AI plus a solver work together to produce an optimal schedule or configuration.

## Overview

OptiBuddy is not a dedicated tool locked to one industry. It is built on a common structure — "tasks, resources, constraints, objective function" (optimization/constraint problems such as RCPSP, resource-constrained project scheduling) — and combines domain-specific rules as **extensions** on top of that structure. This lets it cover a wide range of operations, from container terminal yard planning to event staffing.

```
Business DSL (describes the reality of the operation)
  problem_class + extensions
        ↓
   OptiBuddy core (transformation / control)
        ↓                          ↓
Solver Input DSL                UI DSL
(variables / constraints /      (Gantt/Grid/KPI
 objective function)             structure)
        ↓
   Solver execution (CP Optimizer / CP-SAT)
        ↓
Solver Output DSL (schedule, detected issues, multiple solutions)
        ↓
   OptiBuddy core → fed into UI DSL
        ↓
Optimal schedule + automatic issue detection + AI-suggested improvements
```

## Key features

- **Register a new operation just by talking to the AI** — Fill in a hearing sheet describing your operation in plain language, and the AI classifies it against existing domains, then generates code and registers it in the database automatically. A variant of an existing domain takes tens of seconds; a fully new domain takes a few minutes. No math or coding knowledge required.
- **A wide range of business domains built in** — Covers container terminal yard planning, delivery route optimization, shift scheduling, facility location, and more. Each is built on a mathematically established base problem (RCPSP, CVRP, CFLP, knapsack, bin packing, etc.), with operation-specific rules layered on as extensions.
- **Automatic issue detection with AI-suggested fixes** — After a solution is computed, `issue_rules` automatically checks for constraint violations and inefficiencies, and an AI chat proposes fixes. The user only has to answer "apply" or "skip."
- **Choice of solver engine** — Defaults to Google OR-Tools CP-SAT (Apache 2.0, no extra install needed). Can be switched to IBM CPLEX CP Optimizer / CPLEX MIP (via `docplex`, separately licensed, optional install). MIP domains that exceed the CPLEX Community Edition model-size limit automatically fall back to HiGHS (open source).
- **Results shown as Gantt/Grid/KPI** — Solved results are rendered through the UI DSL as a Gantt chart, grid, and KPI panels.

## Tech stack

- Frontend: React + TypeScript + Vite
- Backend: Python + Flask
- Optimization engines: Google OR-Tools CP-SAT (default) / IBM CPLEX CP Optimizer and CPLEX MIP (`docplex`, optional) / HiGHS (automatic MIP fallback)
- Automated domain generation: Claude API (LLM pipeline, Stage1a → Stage1b / Stage2)

## Setup

For installation, startup, and loading sample data, see [Install.md](./Install.md) (Japanese).

## Documentation

- [OptiBuddy_User_Manual.md](./docs/OptiBuddy_User_Manual.md) — How to use it (registering an operation, reviewing scenarios, working with the AI chat's improvement suggestions) (Japanese)
- [OptiBuddy_Development_Guide.md](./docs/OptiBuddy_Development_Guide.md) — For developers (architecture, DSL design, how extensions work) (Japanese)
- [CSPLIB_REFERENCE.md](./docs/CSPLIB_REFERENCE.md) — Reference for the base mathematical problems each domain builds on (Japanese)

## License

OptiBuddy is released under the [Business Source License 1.1](./LICENSE) (BSL 1.1). Production use for your own business is free for anyone, for any of the built-in domains as well as domains you define yourself. The only restriction applies to third parties in the business of IT/consulting services who provide repeated, paid services (building domains on behalf of clients, hosting, system integration, etc.) to multiple customers — that use case requires a separate commercial license agreed with the Licensor in advance. See [LICENSE-FAQ.md](./LICENSE-FAQ.md) for examples and a quick-reference table (Japanese). Under BSL 1.1, the entire Licensed Work automatically converts to the Apache License 2.0 on the Change Date stated in `LICENSE`.

IBM CPLEX / CP Optimizer (`docplex`), an optional optimization engine, is separately licensed (IBM) and is not bundled or redistributed with OptiBuddy itself. The default engine is OR-Tools CP-SAT (Apache 2.0, no extra install needed). See [Install.md](./Install.md) for details on switching engines.

For a list of the licenses of the OSS libraries OptiBuddy depends on, see [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).
