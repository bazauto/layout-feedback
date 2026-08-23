# PN7150 (T2T) State Machine Plan

## Overview
The PN7150 is **NCI + IRQ driven**. IRQ asserts when **any NCI message** is ready. It is *not* a tag‑presence pin. We must read all queued messages whenever IRQ is asserted and drive our own state machine for activation/deactivation and presence tracking.

## States (per reader)
1. **INIT**
   - Send `CORE_RESET_CMD` → wait `CORE_RESET_RSP`
   - Send `CORE_INIT_CMD` → wait `CORE_INIT_RSP`
   - Transition → **DISCOVERY**

2. **DISCOVERY**
   - Send `RF_DISCOVER_CMD` (poll NFC‑A only)
   - Wait `RF_DISCOVER_NTF`
   - If protocol is **T2T (0x02)** → send `RF_DISCOVER_SELECT_CMD`
   - Transition → **ACTIVATING**

3. **ACTIVATING**
   - Wait `RF_INTF_ACTIVATED_NTF`
   - Extract UID, protocol, interface
   - Emit **Activated(UID, protocol)** (only on UID change)
   - Transition → **ACTIVE**

4. **ACTIVE**
   - Periodic presence check:
     - Send `DATA_PACKET` with `READ 0x30 0x00`
     - Expect **DATA packet response** (NCI MT=DATA, header `0x00 0x00 len`)
     - If response received → tag still present
     - If timeout or `RF_DEACTIVATE_NTF` / `CORE_INTERFACE_ERROR_NTF` → transition **DEACTIVATING**
   - Continue draining NCI messages whenever IRQ is asserted.

5. **DEACTIVATING**
   - Emit **Deactivated(UID)**
   - Clear current UID/protocol
   - Send `RF_DEACTIVATE_CMD` (Idle)
   - Transition → **DISCOVERY**

## IRQ Handling
- IRQ asserts when messages are ready.
- ISR should **only set a flag**.
- Main loop:
  - If flag set, read messages until IRQ de‑asserts.
  - Dispatch each message into the state machine.

## Presence Check (T2T)
- Command: `READ` block 0 → `DATA_PACKET` payload `0x30 0x00`.
- Success: receive DATA packet response (length 16 bytes typical).
- Failure: no response before timeout OR `RF_DEACTIVATE_NTF` / `CORE_INTERFACE_ERROR_NTF`.
- Use **N consecutive failures** before declaring Deactivated (e.g., N=2 or 3).

## Events
- **Activated**: only on new UID.
- **Deactivated**: only when tag is confirmed lost.

## Multi‑Reader (Mux) Extension
- Time‑slice each reader/channel:
  - Select mux channel
  - If IRQ asserted, drain messages
  - If ACTIVE, run presence check
- Each reader keeps its **own state machine**.

## Notes
- Don’t treat IRQ as tag presence.
- Avoid constant `RF_DISCOVER_CMD` spam; only restart when needed.
- T2T protocol value is `0x02` in `RF_INTF_ACTIVATED_NTF` payload.
