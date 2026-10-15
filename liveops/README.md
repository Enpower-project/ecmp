# LiveOps

`liveops` is the runtime operations component for live energy community operation.

It is intended to complement `ecmp`, which currently focuses on historical analysis,
planning, simulation, and optimization support. The `liveops` service is the place
to build features such as:

- real-time community state monitoring
- live dispatch and control workflows
- operational alerts and operator actions
- runtime orchestration of flexibility activation
- integrations with live telemetry and field systems

## Current scope

This folder contains a minimal FastAPI scaffold so the component can evolve
independently from `ecmp` while following a similar service layout.

## Run locally

```bash
docker compose up liveops
```

The service listens on port `8001` by default.
