# Documentation

Everything here is evidence-backed. Each factual claim is tagged:

- **verified** — reproduced against real hardware or a shipped artifact
- **inferred** — consistent with all available evidence, not directly proven
- **unknown** — no evidence; do not build on it

| Document | Contents |
|---|---|
| [protocol_0xB6.md](protocol_0xB6.md) | Verified wire protocol: framing, state frame, all `0xE1`/`0xE0` commands, timers, discovery |
| [device_db.md](device_db.md) | The shipped device database entry for `AK001-ZJ21413` |
| [app_evidence.md](app_evidence.md) | What is and is not recoverable from the shipped Flutter app |
| [architecture.md](architecture.md) | Component structure and design decisions |
| [deployment.md](deployment.md) | How to deploy and verify on a live Home Assistant |

## Evidence index

Raw captures and extracted artifacts live in [`evidence/`](evidence/).

## Hardware under test

| Device | IP | Model | Firmware | LED count |
|---|---|---|---|---|
| Soffit East | 192.168.1.51 | `AK001-ZJ21413` (0xB6) | 9.26 | 35 |
| Soffit West | 192.168.1.50 | `AK001-ZJ21413` (0xB6) | 9.26 | 35 |
| Soffit South | 192.168.1.52 | `AK001-ZJ21413` (0xB6) | 9.26 | 35 |

All three report `MODEL_INFO = ZG-BL-UFO` and are driven by the same protocol.

> **Note on LED count.** Byte 18 of the state frame reports **35** on all three
> controllers. Earlier reference work saw 80 and 100 on a different unit. The
> value is read from the device at runtime and all bitmap sizing is derived
> from it, so no code assumes a fixed count.
