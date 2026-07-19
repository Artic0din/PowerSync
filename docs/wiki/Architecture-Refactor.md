# PowerSync Architecture Refactor

Strangler-fig rewrite of the Home Assistant integration into a typed,
Protocol-driven architecture. Compatibility uses controlled config-entry /
entity migration with temporary service and HTTP adapters.

## Decisions

- **End state:** typed `EntryRuntime`, `BatteryPort`, `PriceProvider`,
  `EnergyTelemetry`, `BrandCapabilities`, single `LoadpointArbiter`
- **Compatibility:** one-time config migration OK; deprecate old surfaces
  behind shims
- **Delivery:** extract behind new boundaries in shippable phases

## Package map

| Package | Responsibility |
|---------|----------------|
| `runtime/` | `EntryRuntime` (typed bag; dual-writes with `hass.data`) |
| `control/` | `BatteryPort`, `ForceModeStore`, restore contracts |
| `capabilities/` | Static brand capability matrix |
| `pricing/` | `PriceProvider` + Amber-interval normalizer |
| `energy/` | `EnergyTelemetry` + snapshots |
| `batteries/` | Per-brand `BatteryPort` adapters |
| `optimization/` | Slim orchestrator + LP / overlays / executor |
| `ev/` | Loadpoint model + arbiter |
| `api_http/` | `HomeAssistantView` handlers |
| `config_flow_pkg/` | Modular config/options steps |
| `bootstrap/` | Setup / unload / migrate orchestration |
| `powerwall_local/` | Reference modular package (unchanged transport) |
| `inverters/` | Curtailment `InverterController` ABC |

## Phases

0. Scaffolding + `EntryRuntime` dual-write + contract tests  
1. Control plane (`BatteryPort` breaks optimizer → service → closure loop)  
2. HTTP API extraction to `api_http/`  
3. Split price vs energy coordinators  
4. Slim optimization coordinator; capability-driven dispatch  
5. EV loadpoint arbiter; retire dual EV ownership  
6. Modular config flow + schema migration  
7. Thin bootstrap `__init__.py`, split `const`, remove shims  

## Contracts

### BatteryPort

Normalized units: power in W, duration in minutes, reserve in percent.
Optimizer and HA services both call `BatteryPort` (services remain as shims).

### Restore contract

Clear force/idle flags only after a successful hardware write. Honor
monitoring-mode symmetry and `_restore_superseded` races. Persist reserve
targets for startup heal.

### EV arbiter

Modes propose; `LoadpointArbiter` issues at most one command per cycle and
never stops an unowned external session unless the user opts in.

## Related docs

- [EV-Charging-Refactor.md](./EV-Charging-Refactor.md)
- [Smart-Optimization.md](./Smart-Optimization.md)
- `.agents/skills/optimizer-bug-hunt/references/architecture.md`
