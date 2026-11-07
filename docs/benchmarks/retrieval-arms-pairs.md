# Fachliche Paarprüfung

Nicht gescored, sondern gelesen. Unter jedem Paar stehen drei Fragen,
die die Kennzahl `context values actually used` nicht beantworten kann.

- vollständige Paare: 20 von 20
- hier gelesen: 6 (Stichprobe, seed 20261107)

> **Wer das ausfüllt, unterschreibt es.** Leere Antworten bleiben leer;
> dieses Skript rät nicht.

## BMS-REQ-AFE-0102

**Frage des Testers:** How does the system notice that a measuring lead to a cell has come loose?

### none

`run-20261107T081320-a15007342cba` · Entwürfe 2 · Rückfragen 16 · unbelegte Werte 2 · Kontext gesendet 0 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen:** keine

**Entwürfe**

- **Detect single open‑wire on a specific cell tap**
  - Ziel: Verify that the BMS flags the exact open‑circuit channel within the required time, does not flag neighboring channels, and raises the DTC after two consecutive detections.
      prec 1. Power‑up the BMS and ensure the HiL rig has all 193 sense lines electrically connected.
           expect: All cells report valid voltages via Cell_Voltage_Raw_PullUp and Cell_Voltage_Raw_PullDown arrays.
           expect: Open_Wire_Fault_Bitmap is all zeros.
      step 1. Open the sense resistor for cell tap 5 on the HiL rig to simulate a loose measuring lead.
           expect: The physical connection for tap 5 is open (no continuity).
      step 2. Wait for the next AFE open‑wire diagnostic cycle (maximum 500 ms interval).
           expect: The AFE injects 100 µA pull‑up for 1 ms and measures V_pullup[5].
           expect: The AFE injects 100 µA pull‑down for 1 ms and measures V_pulldown[5].
      step 3. Monitor Open_Wire_Fault_Bitmap[5] after each diagnostic cycle.
           expect: Open_Wire_Fault_Bitmap[5] becomes 1 within ≤1200 ms from the moment the resistor was opened.
           measure: Detection latency = 1200 ms
      step 4. Check Open_Wire_Fault_Bitmap bits for neighboring taps 4 and 6.
           expect: Open_Wire_Fault_Bitmap[4] remains 0.
           expect: Open_Wire_Fault_Bitmap[6] remains 0.
      step 5. Allow a second diagnostic cycle to complete (another 500 ms).
           expect: A second consecutive detection of the open‑wire condition occurs.
      step 6. Read DTC_Cell_Sense_Open and observe system state.
           expect: DTC_Cell_Sense_Open equals 0x9A0201.
           expect: The BMS enters safe‑shutdown or lockout state as defined by the system (e.g., discharge disabled, fault flag raised).
      post 1. Reconnect the sense resistor for cell tap 5 and reset the BMS.
           expect: Open_Wire_Fault_Bitmap returns to all zeros after the next diagnostic cycle.
           expect: DTC_Cell_Sense_Open is cleared.
- **Verify no false positives when all sense lines are intact**
  - Ziel: Confirm that the diagnostic routine does not set any Open_Wire_Fault_Bitmap bits or DTC when all connections are good over multiple cycles.
      prec 1. Power‑up the BMS with all 193 sense lines properly connected on the HiL rig.
           expect: All voltage measurements are within normal range.
           expect: Open_Wire_Fault_Bitmap is all zeros.
      step 1. Allow the AFE open‑wire diagnostic routine to run for three consecutive cycles (≈1500 ms).
           expect: Each cycle performs the pull‑up and pull‑down injections as specified.
      step 2. Read Open_Wire_Fault_Bitmap after each cycle.
           expect: All bits remain 0 for every cycle.
      step 3. Read DTC_Cell_Sense_Open after the three cycles.
           expect: DTC_Cell_Sense_Open is not set (remains 0).
      post 1. Power‑down the BMS.
           expect: System returns to idle state without fault flags.

### lexical

`run-20261107T081813-1ecbb9de059a` · Entwürfe 2 · Rückfragen 6 · unbelegte Werte 0 · Kontext gesendet 4 (davon durch die Aufgabe 2) · belegt 0

**Herangezogene Quellen**

- `08_diagnostics_fault_derating_and_iso26262-150df807:sec:3288` · signal · full_text_search · 08_diagnostics_fault_derating_and_iso26262.md · *2. Comprehensive DTC Master Table*
  > | DTC Hex Code | Diagnostic Name | Fault Condition | Debounce Time | Healing Criteria | Safety Reaction Level | | :--- | :--- | :--- | :--- | :--- | :--- | | `0x9A0113` | HVIL Circuit Interrupted | Return pulse absent | $15\text{ ms}$ | Continuous pulse for $500\text{ ms}$ | Level 4 / Level 5 | | `0x9A0201` | Cell Voltage Sense Line Open | $|V_{\text{pullup}} - V_{\text{pulldown}}| \ge 350\text{ m
- `BMS-REQ-VNV-1201` · requirement · full_text_search · 14_hil_verification_and_compliance_test_specifications.md · *Requirement: BMS-REQ-VNV-1201*
  > BMS-REQ-VNV-1201 * **Title:** Automated Fault Insertion Unit (FIU) Test Suite Execution * **ASIL Level:** ASIL D * **Verification Method:** Test (HiL Automated Test Runner Script) * **Traceability:** ISO 26262-4:2018 Clause 8.4; ISO 26262-5:2018 Clause 10.4 * **Inputs:** * `FIU_Channel_Matrix` (Data Type: `uint16_t`, Target Sense Wire / CAN Line) * `FIU_Fault_Type` (Data Type: `enum`, Values: `{OP
- `02_cell_monitoring_and_analog_front_end-a17c94ea:sec:419` · explanation · task_search · 02_cell_monitoring_and_analog_front_end.md · *1. Scope & Technical Overview*
  > ## 1. Scope & Technical Overview This module specifies the real-time acquisition, validation, plausibility checking, and physical diagnostics of all core sensor signals in the high-voltage battery pack: 1. **192 Individual Cell Voltages:** Acquired synchronously via 16-bit Sigma-Delta ADCs across 16 daisy-chained CMU satellite ICs. 2. **64 Module Temperatures:** NTC thermistors spaced across cells
- `15_uds_diagnostic_services_and_ecu_reprogramming-54236838:sec:4818` · reference_test · task_search · 15_uds_diagnostic_services_and_ecu_reprogramming.md · *2. Bootloader Flash Reprogramming Sequence*
  > TESTER->>DCM: Service 0x37 (RequestTransferExit) DCM-->>TESTER: Positive Response 0x77 Note over TESTER,HSM: Signature Verification & Reset TESTER->>DCM: Service 0x31 0x01 0xFF03 [RSA-3072 / ECDSA Signature] (VerifyCheck) DCM->>HSM: Verify SHA-256 Hash of Flashed Partition HSM-->>DCM: Signature Validated & Anti-Rollback Updated DCM-->>TESTER: Positive Response 0x71 0x01 0xFF03 0x00 (Valid) TESTER-

**Entwürfe**

- **Detect single cell sense line open-circuit fault**
  - Ziel: Verify that the BMS detects an open measuring lead on a specific cell tap and sets the corresponding bit in Open_Wire_Fault_Bitmap and raises DTC 0x9A0201 within the required time.
      prec 1. Power on the BMS and allow the state‑machine boot sequence to complete
           expect: System reports normal operational state
      step 1. Using the HiL Fault Injection Unit, disconnect the sense‑wire resistor for a chosen cell tap (e.g., tap 45)
           expect: FIU reports open‑circuit fault on the selected tap
      step 2. Wait for the next AFE open‑wire diagnostic cycle (or trigger it if a test command exists)
           expect: Diagnostic routine executes for all taps
      step 3. Read Open_Wire_Fault_Bitmap from the Safety Monitor
           expect: Bit 45 in the bitmap is set to 1
           measure: Open_Wire_Fault_Bitmap[45] = 1 bit
      step 4. Read DTC_Cell_Sense_Open from the diagnostic register
           expect: DTC value equals 0x9A0201
           measure: DTC_Cell_Sense_Open = 0x9A0201 hex
      step 5. Measure elapsed time from fault insertion to DTC set
           expect: Elapsed time ≤ 1200 ms
           measure: detection_latency = ? ms
      post 1. Restore the sense‑wire connection via FIU
           expect: FIU reports reconnection
           measure: FIU_status = connected enum
      post 2. Verify that DTC_Cell_Sense_Open is cleared after healing criteria are met
           expect: No DTC present
           measure: DTC_Cell_Sense_Open = cleared enum
    ? None
    ? None
- **No false positives on neighboring channels when a single sense line is open**
  - Ziel: Confirm that opening one cell tap does not cause Open_Wire_Fault_Bitmap bits for adjacent channels to be set.
      prec 1. Power on the BMS and verify normal operation
           expect: System reports normal operational state
      step 1. Disconnect the sense‑wire resistor for a chosen cell tap (e.g., tap 78) using the FIU
           expect: FIU reports open‑circuit fault on tap 78
      step 2. Allow the diagnostic cycle to run (or trigger it)
           expect: Diagnostic routine executes for all taps
      step 3. Read the full Open_Wire_Fault_Bitmap
           expect: Bit 78 is 1 and all other bits 1..193 except 78 are 0
           measure: Open_Wire_Fault_Bitmap[78] = 1 bit
           measure: Open_Wire_Fault_Bitmap[others] = 0 bitfield
      post 1. Reconnect the sense‑wire via FIU
           expect: FIU reports reconnection
      post 2. Verify DTC_Cell_Sense_Open is cleared after healing
           expect: No DTC present
    ? None
    ? None

### hybrid

`run-20261107T082404-715b61fe3636` · Entwürfe 2 · Rückfragen 11 · unbelegte Werte 0 · Kontext gesendet 3 (davon durch die Aufgabe 1) · belegt 0

**Herangezogene Quellen**

- `08_diagnostics_fault_derating_and_iso26262-150df807:sec:3288` · signal · full_text_search · 08_diagnostics_fault_derating_and_iso26262.md · *2. Comprehensive DTC Master Table*
  > | DTC Hex Code | Diagnostic Name | Fault Condition | Debounce Time | Healing Criteria | Safety Reaction Level | | :--- | :--- | :--- | :--- | :--- | :--- | | `0x9A0113` | HVIL Circuit Interrupted | Return pulse absent | $15\text{ ms}$ | Continuous pulse for $500\text{ ms}$ | Level 4 / Level 5 | | `0x9A0201` | Cell Voltage Sense Line Open | $|V_{\text{pullup}} - V_{\text{pulldown}}| \ge 350\text{ m
- `BMS-REQ-BAL-0504` · requirement · full_text_search · 06_cell_balancing_and_capacity_retention.md · *Requirement: BMS-REQ-BAL-0504*
  > BMS-REQ-BAL-0504 * **Title:** Balancing Switch Diagnostic Detection (Stuck-Closed / Stuck-Open Faults) * **ASIL Level:** ASIL C * **Verification Method:** Test (HiL Emulated Shunt FET Short Injection) * **Traceability:** ISO 26262-5:2018 Clause 7 * **Inputs:** * `Filt_Cell_Voltage[1..192]` (Data Type: `uint16_t[192]`, Unit: `mV`, Source: AFE) * `CMU_Balancing_Command[1..16]` (Data Type: `uint16_t[
- `02_cell_monitoring_and_analog_front_end-a17c94ea:sec:419` · explanation · task_search · 02_cell_monitoring_and_analog_front_end.md · *1. Scope & Technical Overview*
  > ## 1. Scope & Technical Overview This module specifies the real-time acquisition, validation, plausibility checking, and physical diagnostics of all core sensor signals in the high-voltage battery pack: 1. **192 Individual Cell Voltages:** Acquired synchronously via 16-bit Sigma-Delta ADCs across 16 daisy-chained CMU satellite ICs. 2. **64 Module Temperatures:** NTC thermistors spaced across cells

**Entwürfe**

- **Detect single sense line open-circuit fault**
  - Ziel: Verify that disconnecting a single cell voltage sense lead is detected and reported in Open_Wire_Fault_Bitmap within the required time, with no false positives on neighboring channels.
      prec 1. Power on the BMS and allow it to reach steady‑state operation with all cell sense lines connected
           expect: System reports no active DTCs
           expect: Open_Wire_Fault_Bitmap is all zeros
      step 1. Wait for the next scheduled AFE sense‑wire diagnostic cycle (interval 500 ms)
           expect: Diagnostic routine starts without error
      step 2. Command the HiL Automated Wire Disconnect Switch to open the sense lead for cell index 5 (or any chosen index 1‑193)
           expect: Physical disconnect of the selected sense lead is confirmed by the test harness
      step 3. Monitor Open_Wire_Fault_Bitmap for the selected index
           expect: Bit corresponding to the selected index is set to 1 within 1200 ms after the disconnect
           measure: detection_time = <=1200 ms
      step 4. Verify that all other bits in Open_Wire_Fault_Bitmap remain 0
           expect: No false‑positive bits are set for any other cell index
      post 1. Reconnect the previously disconnected sense lead
           expect: Open_Wire_Fault_Bitmap clears the fault bit after the next diagnostic cycle if the line is restored
- **Debounce and DTC generation for persistent open‑wire fault**
  - Ziel: Confirm that after two consecutive detections of the same open‑wire condition the system sets DTC 0x9A0201 and initiates the defined safe‑shutdown/lockout action.
      prec 1. Power on the BMS and ensure no active DTCs; all sense lines are connected
           expect: Open_Wire_Fault_Bitmap is all zeros
           expect: No DTC_Cell_Sense_Open present
      step 1. Command the HiL Automated Wire Disconnect Switch to open the sense lead for cell index 12
           expect: Physical disconnect of the selected sense lead is confirmed
      step 2. Wait for the first diagnostic cycle to detect the fault
           expect: Open_Wire_Fault_Bitmap bit 12 is set to 1 after ≤1200 ms
           measure: first_detection_time = <=1200 ms
      step 3. Keep the sense line disconnected for at least one additional diagnostic interval (≥500 ms)
           expect: System performs a second diagnostic check
      step 4. Observe Open_Wire_Fault_Bitmap and DTC registers after the second detection
           expect: Open_Wire_Fault_Bitmap bit 12 remains 1
           expect: DTC_Cell_Sense_Open is set to 0x9A0201
           measure: debounce_time = <=1000 ms
      step 5. Verify that the BMS initiates the safe‑shutdown or lockout response defined for this fault
           expect: Observable safe‑shutdown action occurs (e.g., contactor open, vehicle power disabled)
      post 1. Reconnect the sense lead and reset the system
           expect: DTC_Cell_Sense_Open is cleared after healing criteria are met, Open_Wire_Fault_Bitmap clears

| Frage | Antwort |
| - | - |
| Q1 war die zusätzliche Quelle für diese Anforderung relevant? | unbeantwortet |
| Q2 hätte sie den Entwurf verbessern können? | unbeantwortet |
| Q3 wurde sie, wo benutzt, korrekt benutzt? | unbeantwortet |

Gelesen von: _offen_

---

## BMS-REQ-BAL-0504

**Frage des Testers:** How is a discharge switch that no longer opens or no longer closes found?

### none

`run-20261107T081442-0b2104660004` · Entwürfe 2 · Rückfragen 7 · unbelegte Werte 0 · Kontext gesendet 0 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen:** keine

**Entwürfe**

- **Detect Stuck-Closed Balancing MOSFET (Short) for Cell 5**
  - Ziel: Verify that the BMS detects a balancing MOSFET stuck‑closed fault and raises DTC 0x9A0701 within 30 minutes of standstill, before the cell voltage drops below 3000 mV.
      prec 1. Set vehicle to standstill (rest) condition.
           expect: Vehicle state reported as rest.
      prec 2. Ensure balancing command for cell 5 is OFF (clear bit in CMU_Balancing_Command[5]).
           expect: CMU_Balancing_Command[5] = 0.
      prec 3. Record initial voltage of cell 5 using Filt_Cell_Voltage[5].
           expect: Cell 5 voltage > 3000 mV.
           measure: Cell voltage = initial mV
      step 1. Inject a simulated short across balancing FET #5 using HiL Emulated Shunt FET Short Injection.
           expect: Short injection active for FET #5.
      step 2. Monitor cell 5 voltage for up to 30 minutes while balancing remains OFF.
           expect: Cell 5 voltage decreases at a rate corresponding to a discharge current ≥ 120 mA.
           expect: Cell 5 voltage remains above 3000 mV until fault is reported.
           measure: Discharge current (derived from voltage drop) = ≥120 mA
      step 3. Check diagnostic status for DTC_Balancing_FET_Stuck_Closed[5].
           expect: DTC_Balancing_FET_Stuck_Closed[5] = 0x9A0701 is set.
      step 4. Verify that a service warning is broadcast to the vehicle network.
           expect: Service warning message observed on the diagnostic bus.
      post 1. Clear simulated short injection and restore normal balancing command.
           expect: Short injection deactivated.
           expect: Balancing command for cell 5 can be toggled normally.
    ? None
    ? None
    ? None
    ? None
    ? None
- **Detect Stuck-Open Balancing MOSFET (No Voltage Step) for a Target Cell**
  - Ziel: Verify that when balancing is commanded ON, the BMS detects the absence of the required voltage step (≥ 2.0 mV) and raises DTC 0x9A0702 after the required debounce.
      prec 1. Set vehicle to standstill (rest) condition.
           expect: Vehicle state reported as rest.
      prec 2. Select a target cell (e.g., cell 7) and ensure its balancing command bit is OFF.
           expect: CMU_Balancing_Command[7] = 0.
      step 1. Assert balancing ON for the target cell by setting CMU_Balancing_Command[7] = 1.
           expect: Balancing command bit for cell 7 is set to 1.
      step 2. Measure the AFE sense voltage for cell 7 immediately before and after the command assertion.
           expect: If the MOSFET operates correctly, an instantaneous voltage step ΔV ≥ 2.0 mV is observed.
           measure: Voltage step ΔV = ≥2.0 mV
      step 3. If no voltage step ≥ 2.0 mV is observed, repeat the command and measurement for three consecutive diagnostic cycles.
           expect: After three consecutive cycles without the required step, the fault debounce condition is satisfied.
      step 4. Check diagnostic status for DTC_Balancing_FET_Stuck_Open[7].
           expect: DTC_Balancing_FET_Stuck_Open[7] = 0x9A0702 is set.
      step 5. Verify that a service warning is broadcast to the vehicle network.
           expect: Service warning message observed on the diagnostic bus.
      post 1. Clear the balancing command for the target cell and restore normal operation.
           expect: CMU_Balancing_Command[7] = 0.
           expect: No further DTCs are active.
    ? None
    ? None
    ? None
    ? None
    ? None

### lexical

`run-20261107T081936-a3d7f9888cee` · Entwürfe 2 · Rückfragen 11 · unbelegte Werte 0 · Kontext gesendet 3 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen**

- `09_autosar_can_fd_communication_and_uds_services-bb1e3f14:sec:454` · signal · full_text_search · 09_autosar_can_fd_communication_and_uds_services.md · *1. Network Topography & Communication Stack*
  > ## 1. Network Topography & Communication Stack The BMS interfaces to the vehicle Powertrain Domain Controller (VCU) and Charging Gateway (CCU) via a high-speed CAN-FD bus. The communication architecture adheres to AUTOSAR Classic specifications utilizing the E2E (End-to-End) protection protocol to guarantee safety-critical transmission up to ASIL D. ```mermaid flowchart TD subgraph ASW["AUTOSAR Ap
- `06_cell_balancing_and_capacity_retention-0fbd1e25:sec:443` · explanation · full_text_search · 06_cell_balancing_and_capacity_retention.md · *1. Scope & Balancing Topography*
  > ## 1. Scope & Balancing Topography In a 192-series cell string, minor variations in self-discharge rate, internal resistance, and local thermal gradients cause State of Charge (SOC) divergence over time. Because the battery pack is capacity-limited by the weakest cell during discharge and the highest cell during charging, cell balancing is mandatory to maintain maximum usable range. ```text +--[ R
- `BMS-REQ-BAL-0502` · requirement · full_text_search · 06_cell_balancing_and_capacity_retention.md · *Requirement: BMS-REQ-BAL-0502*
  > BMS-REQ-BAL-0502 * **Title:** Target Voltage Selection & Shunt Resistor Scheduling * **ASIL Level:** ASIL B * **Verification Method:** Test (HiL Imbalance Emulation Across 192 Cells) * **Traceability:** SAE J1798 * **Inputs:** * `Filt_Cell_Voltage[1..192]` (Data Type: `uint16_t[192]`, Unit: `mV`, Source: AFE) * `Balancing_Permitted_Flag` (Data Type: `boolean`, Unit: `bool`, Source: Supervisor) * *

**Entwürfe**

- **Stuck‑Closed MOSFET detection for cell #5**
  - Ziel: Verify that a simulated short across balancing FET #5 is detected within the required 30 min window and that the corresponding DTC is set before the cell voltage falls below 3000 mV.
      prec 1. Ensure vehicle is in standstill‑parking state and all balancing commands are OFF (CMU_Balancing_Command bits cleared).
           expect: Diagnostic manager reports "rest transition" event.
           expect: All entries in DTC_Balancing_FET_Stuck_Closed[] are cleared.
      prec 2. Record initial filtered voltage of cell 5 from Filt_Cell_Voltage[5].
           expect: Initial voltage V_initial ≥ 3000 mV.
      prec 3. Inject a simulated short across the shunt FET of cell 5 using the HiL “Emulated Shunt FET Short Injection” capability.
           expect: HiL environment confirms short injection is active for cell 5.
      step 1. Run the BMS diagnostic loop for up to 30 minutes, sampling every 60 s as defined by the periodic diagnostic timing.
           expect: After each 60 s cycle the diagnostic routine evaluates the discharge rate of cell 5.
           expect: If the measured discharge current exceeds 120 mA for three consecutive cycles, the fault is qualified.
      step 2. Monitor DTC_Balancing_FET_Stuck_Closed[5] output.
           expect: DTC value 0x9A0701 is written to DTC_Balancing_FET_Stuck_Closed[5] after the third consecutive detection.
           expect: The DTC is broadcast to the Diagnostic Event Manager (Dem).
      step 3. Verify that cell 5 voltage never drops below 3000 mV before the DTC is set.
           expect: Recorded voltage V_cell5 ≥ 3000 mV at the moment the DTC is asserted.
      post 1. Clear the injected short and reset all balancing commands to OFF.
           expect: HiL confirms short injection cleared.
           expect: All DTC entries for stuck‑closed faults are cleared after a system reset.
    ? None
    ? None
    ? None
    ? None
- **Stuck‑Open MOSFET detection when balancing is commanded ON**
  - Ziel: Confirm that the BMS detects a missing voltage step (≥ 2.0 mV) when a balancing MOSFET fails to open, and that the appropriate DTC is set after debounce.
      prec 1. Set vehicle to a non‑rest state (e.g., charging or driving) so that balancing can be commanded ON.
           expect: BMS diagnostic manager allows balancing activation.
      prec 2. Select a target cell (e.g., cell 10) and ensure its balancing command bit in CMU_Balancing_Command[10] is initially 0.
           expect: Balancing command for cell 10 is OFF.
      prec 3. Record the filtered sense voltage for cell 10 from Filt_Cell_Voltage[10] immediately before command.
           expect: Baseline voltage V_before is captured.
      step 1. Issue a balancing ON command for cell 10 by setting the corresponding bit in CMU_Balancing_Command[10].
           expect: BMS attempts to close the shunt FET for cell 10.
      step 2. Measure the instantaneous change in the AFE sense voltage for cell 10.
           expect: A voltage step ΔV_step ≥ 2.0 mV is observed within the defined measurement window.
      step 3. If no step ≥ 2.0 mV is detected, allow the diagnostic loop to run for three consecutive 60 s cycles.
           expect: After three consecutive detections of missing step, the fault is qualified.
      step 4. Monitor DTC_Balancing_FET_Stuck_Open[10] output.
           expect: DTC value 0x9A0702 is written to DTC_Balancing_FET_Stuck_Open[10] after debounce.
      post 1. Reset the balancing command for cell 10 to OFF and verify that the DTC can be cleared after a system reset.
           expect: Balancing command bit cleared.
           expect: DTC_Balancing_FET_Stuck_Open[10] is cleared following reset.
    ? None
    ? None

### hybrid

`run-20261107T082530-f24edba20d8f` · Entwürfe 2 · Rückfragen 14 · unbelegte Werte 0 · Kontext gesendet 3 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen**

- `08_diagnostics_fault_derating_and_iso26262-150df807:sec:3288` · signal · full_text_search · 08_diagnostics_fault_derating_and_iso26262.md · *2. Comprehensive DTC Master Table*
  > | DTC Hex Code | Diagnostic Name | Fault Condition | Debounce Time | Healing Criteria | Safety Reaction Level | | :--- | :--- | :--- | :--- | :--- | :--- | | `0x9A0113` | HVIL Circuit Interrupted | Return pulse absent | $15\text{ ms}$ | Continuous pulse for $500\text{ ms}$ | Level 4 / Level 5 | | `0x9A0201` | Cell Voltage Sense Line Open | $|V_{\text{pullup}} - V_{\text{pulldown}}| \ge 350\text{ m
- `02_cell_monitoring_and_analog_front_end-a17c94ea:sec:3630` · signal · full_text_search · 02_cell_monitoring_and_analog_front_end.md · *2. Signal Processing & Data Flow Diagram*
  > ```mermaid sequenceDiagram autonumber participant BMU as BMU Master Core participant CMU as CMU AFE Satellite participant SENSE as Cell Sense Pin Tap participant DEM as AUTOSAR Dem Manager Note over BMU,CMU: Periodic Diagnostic Initiation (Every 500 ms) BMU->>CMU: Command Diagnostic Pull-Up Current (100 uA) CMU->>SENSE: Inject 100 uA Source Current (1 ms) CMU->>CMU: Sample V_pullup via 16-bit ADC 
- `BMS-REQ-BAL-0502` · requirement · full_text_search · 06_cell_balancing_and_capacity_retention.md · *Requirement: BMS-REQ-BAL-0502*
  > BMS-REQ-BAL-0502 * **Title:** Target Voltage Selection & Shunt Resistor Scheduling * **ASIL Level:** ASIL B * **Verification Method:** Test (HiL Imbalance Emulation Across 192 Cells) * **Traceability:** SAE J1798 * **Inputs:** * `Filt_Cell_Voltage[1..192]` (Data Type: `uint16_t[192]`, Unit: `mV`, Source: AFE) * `Balancing_Permitted_Flag` (Data Type: `boolean`, Unit: `bool`, Source: Supervisor) * *

**Entwürfe**

- **Stuck‑Closed MOSFET Detection (Simulated Short on FET #5)**
  - Ziel: Verify that a balancing MOSFET that remains closed (shorted) is detected and the corresponding DTC 0x9A0701 is set within the required time and before cell voltage falls below 3000 mV.
      prec 1. Place the vehicle in a standstill (rest) condition and ensure the BMS is in the normal operating state.
           expect: System reports rest state via diagnostic status flag.
      prec 2. Issue a balancing OFF command for all cells via `CMU_Balancing_Command`.
           expect: All bits of `CMU_Balancing_Command` are cleared (value 0).
      prec 3. Inject a simulated short across the balancing MOSFET of cell 5 using the HiL test environment.
           expect: Test harness confirms short injection is active for cell 5.
      step 1. Wait for the first diagnostic execution after the rest transition (diagnostic runs every 60 s).
           expect: Diagnostic cycle starts within 60 s of rest transition.
      step 2. Measure the voltage drop `V_drop` for cell 5 while balancing is OFF.
           expect: Measured `V_drop` corresponds to a discharge current ≥ 120 mA (as defined in the requirement).
      step 3. Repeat steps 1‑2 for three consecutive diagnostic cycles (debounce).
           expect: Each cycle reports a discharge current ≥ 120 mA for cell 5.
      step 4. Read the diagnostic output `DTC_Balancing_FET_Stuck_Closed[5]`.
           expect: `DTC_Balancing_FET_Stuck_Closed[5]` equals 0x9A0701.
      step 5. Verify that a service warning is broadcast to the DEM manager.
           expect: DEM logs contain a warning message associated with DTC 0x9A0701.
      step 6. Check the cell voltage `Filt_Cell_Voltage[5]` at the moment the DTC is set.
           expect: Cell 5 voltage is ≥ 3000 mV.
      post 1. Reset the simulated short condition and clear the DTC via the diagnostic clear command.
           expect: `DTC_Balancing_FET_Stuck_Closed[5]` returns to 0.
- **Stuck‑Open MOSFET Detection (No Voltage Step on Balancing ON)**
  - Ziel: Confirm that a balancing MOSFET that fails to close (stuck‑open) is detected and the corresponding DTC 0x9A0702 is set after the required debounce.
      prec 1. Place the vehicle in a standstill (rest) condition and ensure the BMS is in the normal operating state.
           expect: System reports rest state via diagnostic status flag.
      prec 2. Select cell 10 as a balancing candidate (e.g., by setting the appropriate bit in `CMU_Balancing_Command`).
           expect: Bit for cell 10 in `CMU_Balancing_Command` is set to 1.
      prec 3. Configure the HiL test environment to simulate a stuck‑open condition on the MOSFET of cell 10 (MOSFET will not conduct when commanded).
           expect: Test harness confirms MOSFET open condition for cell 10.
      step 1. Record the pre‑command voltage `V_pre` of cell 10 from `Filt_Cell_Voltage[10]`.
           expect: `V_pre` is captured successfully.
      step 2. Issue a balancing ON command for cell 10 via `CMU_Balancing_Command`.
           expect: Balancing command bit for cell 10 remains set.
      step 3. Measure the voltage of cell 10 immediately after the command (within the detection window).
           expect: Measured voltage step `ΔV = V_pre – V_post` is < 2.0 mV (no step observed).
      step 4. Repeat steps 1‑3 for three consecutive diagnostic cycles (debounce).
           expect: Each cycle reports `ΔV < 2.0 mV` for cell 10.
      step 5. Read the diagnostic output `DTC_Balancing_FET_Stuck_Open[10]`.
           expect: `DTC_Balancing_FET_Stuck_Open[10]` equals 0x9A0702.
      step 6. Verify that a service warning is broadcast to the DEM manager.
           expect: DEM logs contain a warning message associated with DTC 0x9A0702.
      post 1. Clear the simulated stuck‑open condition and reset the DTC via the diagnostic clear command.
           expect: `DTC_Balancing_FET_Stuck_Open[10]` returns to 0.

| Frage | Antwort |
| - | - |
| Q1 war die zusätzliche Quelle für diese Anforderung relevant? | unbeantwortet |
| Q2 hätte sie den Entwurf verbessern können? | unbeantwortet |
| Q3 wurde sie, wo benutzt, korrekt benutzt? | unbeantwortet |

Gelesen von: _offen_

---

## BMS-REQ-EST-0205

**Frage des Testers:** How much power may be drawn or fed back at this moment, and for how long?

### none

`run-20261107T081400-6421c2faedf1` · Entwürfe 2 · Rückfragen 14 · unbelegte Werte 10 · Kontext gesendet 0 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen:** keine

**Entwürfe**

- **Discharge Power 10‑second Limit at Cell Minimum Voltage 2750 mV**
  - Ziel: Verify that BMS_Max_Discharge_Power_10s is limited so that the cell voltage cannot fall below the 2700 mV cutoff and that the limit decreases smoothly when Cell_V_Min reaches 2750 mV.
      prec 1. Configure HiL test bench with a single cell pack model and set input signals:
           expect: Cell_V_Min = 2750 mV
           expect: Cell_V_Max = 4200 mV (or any value ≥ V_cutoff_chg)
           expect: Cell_R0_Max = <value provided by tester>
           expect: Pack_T_Min = 250 (represents 25.0 °C)
           expect: BMS_Pack_SOC = 5000 (represents 50.00 %)
      prec 2. Verify that the BMS reads the configured input values via its internal diagnostics.
           expect: BMS reports Cell_V_Min = 2750 mV
           expect: BMS reports Cell_V_Max = 4200 mV
           expect: BMS reports Cell_R0_Max = <value provided>
           expect: BMS reports Pack_T_Min = 250
           expect: BMS reports BMS_Pack_SOC = 5000
      step 1. Allow the BMS calculation task to execute for at least three consecutive 20 ms cycles.
           expect: BMS_Max_Discharge_Power_10s is transmitted on VCU CAN‑FD ID 0x112
           expect: The transmitted value is within the defined range 0..6000 (0..600 kW)
           measure: BMS_Max_Discharge_Power_10s = observed CAN payload 0.1 kW
      step 2. Compare the observed BMS_Max_Discharge_Power_10s value with the theoretical power that would keep the cell voltage ≥ 2700 mV under the current inputs.
           expect: Observed power ≤ theoretical safe power
      step 3. Monitor the BMS_Max_Discharge_Power_10s value over five successive cycles while Cell_V_Min remains at 2750 mV.
           expect: The sequence of values shows a non‑increasing trend (smooth decrease) and does not exhibit abrupt jumps.
      post 1. Restore Cell_V_Min to a nominal value (e.g., 3500 mV) and verify that BMS_Max_Discharge_Power_10s returns to a higher level.
           expect: BMS_Max_Discharge_Power_10s increases within the allowed slew‑rate limits.
    ? None
    ? None
    ? None
    ? None
    ? None
    ? None
- **Continuous Discharge Power Limit and Slew‑Rate Limiter on Power Drop**
  - Ziel: Confirm that BMS_Max_Discharge_Power_Cont follows the calculated limit and that any reduction in the limit respects the maximum drop rate of 100 kW/s.
      prec 1. Set input signals to allow a high discharge power:
           expect: Cell_V_Min = 3500 mV
           expect: Cell_V_Max = 4200 mV
           expect: Cell_R0_Max = <value provided by tester>
           expect: Pack_T_Min = 250 (25.0 °C)
           expect: BMS_Pack_SOC = 8000 (80.00 %)
      prec 2. Verify that the BMS reads these inputs correctly.
           expect: All input values are reported accurately by the BMS.
      step 1. Allow the BMS to run for three 20 ms cycles and record BMS_Max_Discharge_Power_Cont.
           expect: The output is present on CAN‑FD ID 0x112 and lies within the range 0..5000 (0..500 kW).
           measure: BMS_Max_Discharge_Power_Cont = observed CAN payload 0.1 kW
      step 2. Instantaneously change Cell_V_Min from 3500 mV to 2750 mV (simulate a voltage sag).
           expect: BMS detects the new input on the next calculation cycle.
      step 3. Record BMS_Max_Discharge_Power_Cont for the next five 20 ms cycles.
           expect: The decrease in the output value per cycle does not exceed the allowed drop of 100 kW/s.
           expect: Maximum allowed drop per 20 ms cycle = 100 kW/s × 0.02 s = 2 kW → 20 units of 0.1 kW.
           expect: ΔPower ≤ 20 units between any two consecutive samples.
      step 4. Measure the time interval between successive CAN messages containing BMS_Max_Discharge_Power_Cont.
           expect: Each interval is 20 ms ±1 ms, satisfying the cyclic execution timing requirement.
      post 1. Reset Cell_V_Min to its original nominal value and verify that BMS_Max_Discharge_Power_Cont rises again within the same slew‑rate limits.
           expect: Increase per cycle ≤ 20 units (consistent with the 100 kW/s limit).
    ? None
    ? None
    ? None

### lexical

`run-20261107T081853-0dc07d86a253` · Entwürfe 2 · Rückfragen 12 · unbelegte Werte 2 · Kontext gesendet 3 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen**

- `09_autosar_can_fd_communication_and_uds_services-bb1e3f14:sec:3995` · signal · full_text_search · 09_autosar_can_fd_communication_and_uds_services.md · *2. CAN-FD Broadcast Message Matrix*
  > ## 2. CAN-FD Broadcast Message Matrix | CAN ID | Frame Name | Period | DLC | E2E Protected | Signal Payload Content | | :--- | :--- | :--- | :--- | :--- | :--- | | `0x100` | `BMS_State_01` | $10\text{ ms}$ | 64 | Yes (Profile 1) | Operational State, Contactor Status, HVIL State, Active Fault Severity | | `0x101` | `BMS_Cell_Voltages` | $10\text{ ms}$ | 64 | Yes (Profile 1) | $V_{\text{min}}$, $V_{
- `19_eaton_breaktor_advanced_circuit_protection-f90d4ea9:sec:586` · explanation · full_text_search · 19_eaton_breaktor_advanced_circuit_protection.md · *1. Scope & Device Topography Overview*
  > ## 1. Scope & Device Topography Overview The **Eaton Breaktor®** integrates the functionalities of three traditional high-voltage components into a single compact, resettable advanced circuit protection unit: 1. **Mechanical High-Voltage Contactor:** Switching operational traction and fast-charging current ($500\text{ A}$ continuous at $800\text{V} - 1000\text{V}$). 2. **Pyrotechnic Disconnect (Py
- `BMS-REQ-CHG-0606` · requirement · full_text_search · 07_charging_control_and_evse_interfacing.md · *Requirement: BMS-REQ-CHG-0606*
  > BMS-REQ-CHG-0606 * **Title:** Vehicle-to-Load (V2L) Bidirectional Discharge Envelope * **ASIL Level:** ASIL C * **Verification Method:** Test (HiL V2L Inverter Load Profile) * **Traceability:** ISO 15118-20; ISO 26262-4 * **Inputs:** * `BMS_Pack_SOC` (Data Type: `uint16_t`, Unit: `0.01 %`, Source: AEKF) * `V2L_Enable_Request` (Data Type: `boolean`, Unit: `bool`, Source: OBC CAN-FD) * `Cell_V_Min` 

**Entwürfe**

- **Nominal calculation of continuous and 10‑second discharge power limits**
  - Ziel: Verify that the BMS computes `BMS_Max_Discharge_Power_Cont` and `BMS_Max_Discharge_Power_10s` from nominal input values and that the outputs are within the specified ranges
      prec 1. Power‑up the ECU and ensure the BMS is in normal operating mode (no emergency safety trip)
           expect: BMS state reported as normal on CAN message `BMS_State_01` (ID 0x100)
      step 1. Inject the following input values via the appropriate CAN signals or test harness: Cell_V_Min=3500 mV, Cell_V_Max=4200 mV, Cell_R0_Max=10 (0.01 mOhm units → 0.1 mOhm), Pack_T_Min=250 (0.1 °C → 25 °C), BMS_Pack_SOC=8000 (0.01 % → 80 %), Pack voltage V_pack=400 V (source to be clarified)
           expect: All input signals are acknowledged by the BMS (e.g., reflected in diagnostic status)
      step 2. Wait for at least one BMS calculation cycle (≥20 ms)
           expect: A CAN‑FD frame `BMS_SOP_Limits` (ID 0x112) is transmitted within the 20 ms ±1 ms window
      step 3. Read the fields `Continuous Discharge kW` and `10s Pulse Discharge kW` from the received `BMS_SOP_Limits` message
           expect: Both fields are present and decoded as unsigned 16‑bit values with unit 0.1 kW
           measure: BMS_Max_Discharge_Power_Cont = 0..5000 0.1 kW
           measure: BMS_Max_Discharge_Power_10s = 0..6000 0.1 kW
      step 4. Compare the reported power limits with the values obtained from the specification formulas using the injected inputs
           expect: The reported limits are equal to the calculated limits within measurement resolution (0.1 kW)
      step 5. Verify that the reported limits do not exceed the maximum allowed ranges (0..5000 for continuous, 0..6000 for 10 s)
           expect: Both limits are ≤ their respective maximums
      post 1. Log the measured power limit values and the input set‑points for traceability
           expect: Log entries are stored in the test result database
- **Dynamic reduction of 10‑second discharge power limit as cell voltage approaches cutoff**
  - Ziel: Confirm that when `Cell_V_Min` approaches the 2700 mV cutoff, `BMS_Max_Discharge_Power_10s` decreases smoothly and respects the 100 kW/s slew‑rate limit, and that the limit prevents the cell voltage from falling below 2700 mV
      prec 1. Initialize the BMS with nominal inputs as in Test 1, but set Cell_V_Min to 2760 mV (10 mV above the 2750 mV trigger point)
           expect: BMS reports the initial `Cell_V_Min` value correctly
      step 1. Wait for a calculation cycle and record the initial `BMS_Max_Discharge_Power_10s` value from CAN ID 0x112
           expect: Initial 10 s power limit is captured
      step 2. Decrease `Cell_V_Min` in 10 mV steps down to 2740 mV, waiting at least 20 ms between steps to allow a new calculation cycle
           expect: Each step results in a new `BMS_SOP_Limits` message
      step 3. For each step, record the `BMS_Max_Discharge_Power_10s` value and compute the change ΔP relative to the previous step
           expect: ΔP is non‑negative (monotonic decrease)
           measure: ΔP per step = ≤ (100 kW/s) × 0.02 s = 2 kW kW
      step 4. Verify that the rate of power reduction does not exceed the slew‑rate limiter of 100 kW/s (i.e., ΔP/Δt ≤ 100 kW/s)
           expect: Measured ΔP/Δt for all steps ≤ 100 kW/s
      step 5. When `Cell_V_Min` reaches 2750 mV, check that the reported `BMS_Max_Discharge_Power_10s` value is low enough that a discharge at that power would not drive the cell below 2700 mV
           expect: Power limit satisfies the safety condition
      step 6. Maintain the 10 s power limit at the reduced value for a full 10‑second interval and verify that the BMS continues to transmit the same limit throughout the interval
           expect: No further reduction occurs and the limit remains constant for 10 s
      post 1. Document the full time‑series of `Cell_V_Min` and `BMS_Max_Discharge_Power_10s` values
           expect: Traceability matrix links each recorded value to the corresponding test step

### hybrid

`run-20261107T082447-d67b1e3c2ed6` · Entwürfe 2 · Rückfragen 22 · unbelegte Werte 6 · Kontext gesendet 3 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen**

- `09_autosar_can_fd_communication_and_uds_services-bb1e3f14:sec:3995` · signal · full_text_search · 09_autosar_can_fd_communication_and_uds_services.md · *2. CAN-FD Broadcast Message Matrix*
  > ## 2. CAN-FD Broadcast Message Matrix | CAN ID | Frame Name | Period | DLC | E2E Protected | Signal Payload Content | | :--- | :--- | :--- | :--- | :--- | :--- | | `0x100` | `BMS_State_01` | $10\text{ ms}$ | 64 | Yes (Profile 1) | Operational State, Contactor Status, HVIL State, Active Fault Severity | | `0x101` | `BMS_Cell_Voltages` | $10\text{ ms}$ | 64 | Yes (Profile 1) | $V_{\text{min}}$, $V_{
- `18_extreme_fast_charging_and_lithium_plating_prevention-7abfda8b:sec:3208` · explanation · full_text_search · 18_extreme_fast_charging_and_lithium_plating_prevention.md · *2. Pulse Relaxation Pause & Rebound Voltage Extraction Sequence*
  > ## 2. Pulse Relaxation Pause & Rebound Voltage Extraction Sequence ```mermaid sequenceDiagram autonumber participant EVSE as 400 kW DC Fast Charging Station participant CCU as Vehicle CCU Gateway (ISO 15118-20) participant BMS as BMS XFC Overpotential Engine participant AFE as 16x CMU Satellite ICs participant PELT as Active Thermoelectric Peltier Coolers Note over EVSE,BMS: Active XFC Session: Cu
- `BMS-REQ-CHG-0606` · requirement · full_text_search · 07_charging_control_and_evse_interfacing.md · *Requirement: BMS-REQ-CHG-0606*
  > BMS-REQ-CHG-0606 * **Title:** Vehicle-to-Load (V2L) Bidirectional Discharge Envelope * **ASIL Level:** ASIL C * **Verification Method:** Test (HiL V2L Inverter Load Profile) * **Traceability:** ISO 15118-20; ISO 26262-4 * **Inputs:** * `BMS_Pack_SOC` (Data Type: `uint16_t`, Unit: `0.01 %`, Source: AEKF) * `V2L_Enable_Request` (Data Type: `boolean`, Unit: `bool`, Source: OBC CAN-FD) * `Cell_V_Min` 

**Entwürfe**

- **Continuous discharge power limit calculation under nominal conditions**
  - Ziel: Verify that BMS computes BMS_Max_Discharge_Power_Cont according to the formulas in BMS-REQ-EST-0205 and that the value is within the specified range and update period.
      prec 1. Power on the BMS and establish a CAN‑FD connection to the VCU (ID 0x112).
           expect: BMS reports status "ready" on the CAN bus.
           expect: VCU acknowledges receipt of the CAN‑FD connection.
      prec 2. Configure the AFE simulator to provide nominal cell voltage and resistance values.
           expect: AFE reports the configured values when queried.
      step 1. Inject a nominal Cell_V_Min value (within safe operating range) via the AFE simulator.
           expect: BMS reads Cell_V_Min equal to the injected value.
      step 2. Inject a nominal Cell_V_Max value via the AFE simulator.
           expect: BMS reads Cell_V_Max equal to the injected value.
      step 3. Inject a nominal Cell_R0_Max value via the RLS Estimator simulator.
           expect: BMS reads Cell_R0_Max equal to the injected value.
      step 4. Inject a nominal Pack_T_Min value via the Temperature Monitor simulator.
           expect: BMS reads Pack_T_Min equal to the injected value.
      step 5. Inject a nominal BMS_Pack_SOC value via the AEKF simulator.
           expect: BMS reads BMS_Pack_SOC equal to the injected value.
      step 6. Wait for at least one 20 ms execution cycle of the SOP calculation.
           expect: BMS publishes BMS_Max_Discharge_Power_Cont on CAN ID 0x112 within the 20 ms ±1 ms timing window.
           measure: update_period = ≤21 ms ms
      step 7. Read the value of BMS_Max_Discharge_Power_Cont from CAN ID 0x112.
           expect: The raw CAN payload is decoded to a uint16_t representing power in 0.1 kW steps.
           expect: The decoded power value lies within the specified range 0..5000 (i.e., 0 kW to 500 kW).
           measure: BMS_Max_Discharge_Power_Cont = <decoded value> 0.1 kW
      step 8. Compare the measured power value with the expected value calculated from the formulas in the requirement.
           expect: Measured power matches the expected calculation within any defined tolerance.
      post 1. Reset all injected signals to their default (idle) state.
           expect: AFE, RLS Estimator, Temperature Monitor and AEKF report idle values.
    ? None
    ? None
    ? None
    ? None
    ? None
    ? None
    ? None
- **10 s pulse discharge power limit response to approaching cell‑voltage cutoff**
  - Ziel: Confirm that BMS_Max_Discharge_Power_10s reduces appropriately as Cell_V_Min approaches 2750 mV and that the reduction respects the slew‑rate limit of 100 kW/s.
      prec 1. Power on the BMS and establish CAN‑FD communication with the VCU (ID 0x112).
           expect: BMS reports ready status on the CAN bus.
      prec 2. Configure the AFE simulator to allow programmable Cell_V_Min values.
           expect: AFE acknowledges the ability to set Cell_V_Min.
      step 1. Set Cell_V_Min to a value well above the cutoff (e.g., 3000 mV).
           expect: BMS reads the injected Cell_V_Min value.
      step 2. Wait for one SOP calculation cycle (20 ms).
           expect: BMS publishes BMS_Max_Discharge_Power_10s on CAN ID 0x112.
      step 3. Record the initial BMS_Max_Discharge_Power_10s value.
           expect: Value is within the allowed range 0..6000 (0 kW to 600 kW).
      step 4. Gradually decrease Cell_V_Min in 10 mV steps from the initial value down to 2750 mV, waiting at least one SOP cycle after each step.
           expect: For each step, BMS reads the new Cell_V_Min value.
           expect: BMS_Max_Discharge_Power_10s is updated on each cycle.
      step 5. For each recorded power value, compute the change ΔP between successive cycles and the corresponding time Δt (≈20 ms).
           expect: The absolute rate |ΔP/Δt| does not exceed 100 kW/s (i.e., ≤ 2 kW per 20 ms).
           measure: power_drop_rate = <computed rate> kW/s
      step 6. Verify that the sequence of BMS_Max_Discharge_Power_10s values is monotonically non‑increasing as Cell_V_Min approaches 2750 mV.
           expect: No increase in power limit is observed during the ramp.
      step 7. At the final Cell_V_Min = 2750 mV, command a discharge load equal to the reported BMS_Max_Discharge_Power_10s for a 10 s interval.
           expect: Cell voltage measured by the AFE never falls below 2700 mV during the interval.
      post 1. Restore Cell_V_Min to a safe nominal value and clear all injected signals.
           expect: All simulators return to idle state.
    ? None
    ? None
    ? None

| Frage | Antwort |
| - | - |
| Q1 war die zusätzliche Quelle für diese Anforderung relevant? | unbeantwortet |
| Q2 hätte sie den Entwurf verbessern können? | unbeantwortet |
| Q3 wurde sie, wo benutzt, korrekt benutzt? | unbeantwortet |

Gelesen von: _offen_

---

## BMS-REQ-HVC-0301

**Frage des Testers:** What has to happen in which order before the main contactors may close?

### none

`run-20261107T081336-b67f24656595` · Entwürfe 2 · Rückfragen 23 · unbelegte Werte 0 · Kontext gesendet 0 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen:** keine

**Entwürfe**

- **Verify correct ordering before main positive contactor closure when voltage condition is met**
  - Ziel: Confirm that Cmd_Contactor_Main_Pos (K1) is asserted only after the required pre‑charge steps and voltage condition are satisfied, following the specified timing.
      prec 1. Set BMS_Operational_State to ST_03_PRECHARGE
           expect: BMS reports state ST_03_PRECHARGE via FSM
      step 1. Assert Cmd_Contactor_Main_Neg = TRUE (close K2)
           expect: K2 contactor is closed
           expect: Mechanical bounce has ceased after 25 ms
      step 2. Wait 25 ms
           expect: No bounce activity observed on K2
      step 3. Assert Cmd_Contactor_Precharge = TRUE (close K3) and start sampling Link_Voltage_Bus at 1 kHz
           expect: K3 contactor is closed
           expect: Link voltage samples are being recorded
      step 4. Continuously calculate ΔV(t) = |Pack_Voltage_Bus(t) – Link_Voltage_Bus(t)|
           expect: ΔV(t) ≤ 10.0 V OR Link_Voltage_Bus(t) ≥ 0.98·Pack_Voltage_Bus(t) is detected
           expect: The detected condition is sustained for at least 10 ms
      step 5. Assert Cmd_Contactor_Main_Pos = TRUE (close K1)
           expect: K1 contactor is closed
           expect: Inrush current through K1 does not exceed the allowed limit (≤30.0 A) – measurement method not defined
      step 6. Wait 20 ms mechanical overlap time
           expect: K1 remains closed during overlap
      step 7. De‑assert Cmd_Contactor_Precharge = FALSE (open K3)
           expect: K3 contactor is opened
      step 8. Assert Precharge_Complete_Flag = TRUE
           expect: FSM receives Precharge_Complete_Flag = TRUE
- **Verify that main positive contactor never closes when pre‑charge voltage condition is not met within timeout**
  - Ziel: Ensure that Cmd_Contactor_Main_Pos (K1) remains de‑asserted and timeout handling is performed if ΔV stays >10 V for more than 300 ms after Step 2.
      prec 1. Set BMS_Operational_State to ST_03_PRECHARGE
           expect: BMS reports state ST_03_PRECHARGE via FSM
      prec 2. Configure Pack_Voltage_Bus and Link_Voltage_Bus such that ΔV > 10.0 V and Link_Voltage_Bus < 0.98·Pack_Voltage_Bus at start of pre‑charge
           expect: Initial ΔV measurement confirms condition >10 V
      step 1. Assert Cmd_Contactor_Main_Neg = TRUE (close K2)
           expect: K2 contactor is closed
      step 2. Wait 25 ms
           expect: Mechanical bounce on K2 has ceased
      step 3. Assert Cmd_Contactor_Precharge = TRUE (close K3) and start sampling Link_Voltage_Bus at 1 kHz
           expect: K3 contactor is closed
           expect: Link voltage samples are being recorded
      step 4. Monitor ΔV(t) for up to 300 ms
           expect: ΔV(t) remains >10.0 V and Link_Voltage_Bus(t) < 0.98·Pack_Voltage_Bus(t) for the full 300 ms period
      step 5. Verify timeout handling after 300 ms
           expect: Cmd_Contactor_Main_Pos remains FALSE (K1 never closes)
           expect: Cmd_Contactor_Precharge is de‑asserted (K3 opened)
           expect: Cmd_Contactor_Main_Neg is de‑asserted (K2 opened)
           expect: Precharge_Complete_Flag = FALSE
           expect: Transition to ST_08_DEGRADED or ST_10_LOCKOUT occurs
           expect: DTC 0x9A0601 is logged

### lexical

`run-20261107T081828-2d10515c94ac` · Entwürfe 2 · Rückfragen 9 · unbelegte Werte 1 · Kontext gesendet 3 (davon durch die Aufgabe 1) · belegt 0

**Herangezogene Quellen**

- `08_diagnostics_fault_derating_and_iso26262-150df807:sec:3288` · signal · full_text_search · 08_diagnostics_fault_derating_and_iso26262.md · *2. Comprehensive DTC Master Table*
  > | DTC Hex Code | Diagnostic Name | Fault Condition | Debounce Time | Healing Criteria | Safety Reaction Level | | :--- | :--- | :--- | :--- | :--- | :--- | | `0x9A0113` | HVIL Circuit Interrupted | Return pulse absent | $15\text{ ms}$ | Continuous pulse for $500\text{ ms}$ | Level 4 / Level 5 | | `0x9A0201` | Cell Voltage Sense Line Open | $|V_{\text{pullup}} - V_{\text{pulldown}}| \ge 350\text{ m
- `BMS-REQ-SYS-0020` · requirement · full_text_search · 01_system_architecture_and_operating_modes.md · *Requirement: BMS-REQ-SYS-0020*
  > BMS-REQ-SYS-0020 * **Title:** Operational State Machine Transition Flow Control * **ASIL Level:** ASIL C * **Verification Method:** Test (HiL State Machine Model Checking) * **Traceability:** AUTOSAR BSW State Management Spec; ISO 26262-4 * **Inputs:** * `VCU_HV_Request` (Data Type: `uint8_t`, Unit: `enum`, Values: `{NO_REQ, DRIVE_ENABLE, CHARGE_AC, CHARGE_DC, SHUTDOWN}`, Source: CAN-FD `0x0C0`) *
- `04_high_voltage_contactor_and_safety_sequencing-b43a6cda:sec:490` · explanation · task_search · 04_high_voltage_contactor_and_safety_sequencing.md · *1. Scope & Circuit Topography*
  > ## 1. Scope & Circuit Topography The High-Voltage Contactor Management module commands the electromechanical relays coupling the 800V nominal battery core to the vehicle DC-link capacitor ($C_{\text{link}} \approx 1200\,\mu\text{F}$ in the traction inverter) and to off-board DC fast charging stations. ```text Pack(+) ----[ K1: Main Pos ]-----------------------+---------> Inverter DC+ \ | +--[ K3: 

**Entwürfe**

- **Nominal Precharge Sequence to Main Positive Contactor Closure**
  - Ziel: Verify that the software executes the required temporal sequence and closes K1 only after the voltage condition is satisfied, and that all flags and commands are set as specified.
      prec 1. Set BMS_Operational_State to ST_03_PRECHARGE via the state machine.
           expect: State machine reports current state = ST_03_PRECHARGE.
      prec 2. Provide Pack_Voltage_Bus = 400000 mV and Link_Voltage_Bus = 0 mV (initially) to the precharge manager.
           expect: Pack_Voltage_Bus and Link_Voltage_Bus values are accepted by the software.
      prec 3. Ensure Active_Fault_Severity = NONE and no other pre‑charge inhibiting conditions are present.
           expect: Diagnostic manager reports no active fault severity.
      step 1. Assert Cmd_Contactor_Main_Neg = TRUE.
           expect: Low‑Side Driver K2 reports contactor closed.
           measure: Cmd_Contactor_Main_Neg = True boolean
      step 2. Wait 25 ms for mechanical contact bounce cessation.
           expect: K2 remains closed for the full 25 ms period without bounce.
           expect: Elapsed time measured >= 25 ms.
           measure: elapsed_time = 25 ms
      step 3. Assert Cmd_Contactor_Precharge = TRUE and start sampling Link_Voltage_Bus at 1 kHz.
           expect: Low‑Side Driver K3 reports contactor closed.
           expect: Link voltage samples are captured at 1 kHz.
           measure: Cmd_Contactor_Precharge = True boolean
           measure: sampling_rate_Link_Voltage = 1000 Hz
      step 4. Continuously calculate ΔV(t) = |Pack_Voltage_Bus(t) - Link_Voltage_Bus(t)| and monitor until ΔV ≤ 10.0 V OR Link_Voltage_Bus ≥ 0.98·Pack_Voltage_Bus, sustained for ≥10 ms.
           expect: When either voltage condition becomes true and stays true for at least 10 ms, the condition is considered satisfied.
      step 5. Assert Cmd_Contactor_Main_Pos = TRUE.
           expect: Low‑Side Driver K1 reports contactor closed.
           expect: At the moment of assertion, ΔV ≤ 10.0 V (or the 0.98·V_pack condition holds).
           expect: Inrush current through K1 does not exceed 30.0 A.
           measure: Cmd_Contactor_Main_Pos = True boolean
           measure: ΔV_at_K1_closure = 10 V
           measure: Inrush_current_K1 = 30 A
      step 6. Wait 20 ms mechanical overlap time.
           expect: K1 remains closed for the full 20 ms period.
           measure: overlap_time = 20 ms
      step 7. De‑assert Cmd_Contactor_Precharge = FALSE.
           expect: Low‑Side Driver K3 reports contactor opened.
           measure: Cmd_Contactor_Precharge = False boolean
      step 8. Assert Precharge_Complete_Flag = TRUE.
           expect: FSM receives Precharge_Complete_Flag = TRUE.
           measure: Precharge_Complete_Flag = True boolean
      post 1. Verify that BMS_Operational_State may transition to ST_04_DRIVE_READY within the allowed 300 ms window.
           expect: State machine permits transition to ST_04_DRIVE_READY.
- **Precharge Timeout Handling – Main Positive Contactor Must Remain Open**
  - Ziel: Verify that when the voltage difference remains >10 V after 300 ms, the main positive contactor never closes and the timeout actions are performed.
      prec 1. Set BMS_Operational_State to ST_03_PRECHARGE.
           expect: State machine reports current state = ST_03_PRECHARGE.
      prec 2. Provide Pack_Voltage_Bus = 400000 mV and Link_Voltage_Bus = 0 mV (no precharge path resistance simulated).
           expect: Pack and link voltage values are accepted.
      step 1. Assert Cmd_Contactor_Main_Neg = TRUE.
           expect: K2 closed.
      step 2. Wait 25 ms.
           expect: K2 remains closed for the full period.
      step 3. Assert Cmd_Contactor_Precharge = TRUE and start sampling Link_Voltage_Bus at 1 kHz.
           expect: K3 closed and link voltage sampling active.
      step 4. Monitor ΔV(t) for 300 ms without satisfying ΔV ≤ 10 V or Link_Voltage_Bus ≥ 0.98·Pack_Voltage_Bus.
           expect: ΔV(t) stays > 10 V for the entire 300 ms interval.
      step 5. After 300 ms, verify that the software opens K3 and K2, sets Precharge_Complete_Flag = FALSE, logs DTC 0x9A0601, and does NOT assert Cmd_Contactor_Main_Pos.
           expect: K3 and K2 are opened.
           expect: Precharge_Complete_Flag = FALSE.
           expect: Diagnostic log contains DTC 0x9A0601.
           expect: Cmd_Contactor_Main_Pos remains FALSE.
           measure: DTC_logged = 0x9A0601 hex
           measure: Cmd_Contactor_Main_Pos = False boolean
      post 1. Confirm that the system remains in ST_03_PRECHARGE or transitions to a safe state as defined by higher‑level state machine rules.
           expect: No transition to ST_04_DRIVE_READY occurs.

### hybrid

`run-20261107T082421-5a6121658147` · Entwürfe 2 · Rückfragen 20 · unbelegte Werte 0 · Kontext gesendet 3 (davon durch die Aufgabe 1) · belegt 0

**Herangezogene Quellen**

- `22_hardware_and_software_interface_specifications-5cdbf23b:sec:571` · interface · full_text_search · 22_hardware_and_software_interface_specifications.md · *1. Scope & System Interface Topography*
  > ## 1. Scope & System Interface Topography The Battery Management System (BMS) bridges high-voltage physical sensors, battery cell satellites, off-board charging stations, and vehicle powertrain domain controllers. This specification establishes the electrical, protocol, timing, and fault-tolerance requirements for all external and internal interfaces of the BMS. ```mermaid flowchart TD subgraph Ex
- `BMS-REQ-HVC-0302` · requirement · full_text_search · 04_high_voltage_contactor_and_safety_sequencing.md · *Requirement: BMS-REQ-HVC-0302*
  > BMS-REQ-HVC-0302 * **Title:** Precharge Resistor Thermal Energy Accumulation & Cooldown Protection * **ASIL Level:** ASIL C * **Verification Method:** Test (HiL Back-to-Back Precharge Abort Injections) * **Traceability:** ISO 26262-4 * **Inputs:** * `Pack_Voltage_Bus` (Data Type: `uint32_t`, Unit: `mV`, Source: Terminal Sensor) * `Link_Voltage_Bus` (Data Type: `uint32_t`, Unit: `mV`, Source: Bus S
- `04_high_voltage_contactor_and_safety_sequencing-b43a6cda:sec:490` · explanation · task_search · 04_high_voltage_contactor_and_safety_sequencing.md · *1. Scope & Circuit Topography*
  > ## 1. Scope & Circuit Topography The High-Voltage Contactor Management module commands the electromechanical relays coupling the 800V nominal battery core to the vehicle DC-link capacitor ($C_{\text{link}} \approx 1200\,\mu\text{F}$ in the traction inverter) and to off-board DC fast charging stations. ```text Pack(+) ----[ K1: Main Pos ]-----------------------+---------> Inverter DC+ \ | +--[ K3: 

**Entwürfe**

- **Normal Precharge Sequence leading to Main Positive Contactor Closure**
  - Ziel: Verify the ordered actions and timing before K1 may close and that Precharge_Complete_Flag is set
      prec 1. Set BMS_Operational_State to ST_03_PRECHARGE
           expect: FSM reports state ST_03_PRECHARGE
      step 1. Assert Cmd_Contactor_Main_Neg = TRUE
           expect: Cmd_Contactor_Main_Neg signal is TRUE
           expect: K2 contactor is closed
      step 2. Wait 25 ms
           expect: 25 ms elapsed verified by system timer
      step 3. Assert Cmd_Contactor_Precharge = TRUE
           expect: Cmd_Contactor_Precharge signal is TRUE
           expect: K3 contactor is closed
      step 4. Begin sampling Pack_Voltage_Bus and Link_Voltage_Bus at 1 kHz
           expect: Sampling task active and samples recorded
      step 5. Calculate ΔV(t)=|Pack_Voltage_Bus‑Link_Voltage_Bus| and monitor
           expect: When ΔV ≤10 V OR Link_Voltage_Bus ≥0.98*Pack_Voltage_Bus, condition flag becomes true
      step 6. Continue monitoring until condition is sustained for ≥10 ms
           expect: Condition flag remains true for at least 10 consecutive ms of samples
      step 7. Assert Cmd_Contactor_Main_Pos = TRUE
           expect: Cmd_Contactor_Main_Pos signal is TRUE
           expect: K1 contactor is closed
      step 8. Wait 20 ms
           expect: 20 ms elapsed
      step 9. De‑assert Cmd_Contactor_Precharge = FALSE
           expect: Cmd_Contactor_Precharge signal is FALSE
           expect: K3 opens
      step 10. Assert Precharge_Complete_Flag = TRUE
           expect: Precharge_Complete_Flag signal is TRUE
      step 11. Measure inrush current through K1 at closure
           expect: Inrush current ≤30 A
           measure: Inrush_Current_K1 = 30 A
      post 1. Verify FSM transitions to next operational state after flag set
           expect: FSM reports expected post‑precharge state
    ? None
    ? None
    ? None
    ? None
- **Precharge Timeout Scenario – Ensure Main Positive Contactor does NOT Close**
  - Ziel: Validate that when ΔV remains >10 V beyond 300 ms, K1 stays open, K2/K3 are opened, and appropriate fault handling occurs
      prec 1. Set BMS_Operational_State to ST_03_PRECHARGE
           expect: FSM reports state ST_03_PRECHARGE
      prec 2. Configure Pack_Voltage_Bus and Link_Voltage_Bus such that ΔV >10 V for >300 ms
           expect: Voltage sources maintain the required difference
      step 1. Assert Cmd_Contactor_Main_Neg = TRUE
           expect: Cmd_Contactor_Main_Neg signal is TRUE
           expect: K2 closed
      step 2. Wait 25 ms
           expect: 25 ms elapsed
      step 3. Assert Cmd_Contactor_Precharge = TRUE
           expect: Cmd_Contactor_Precharge signal is TRUE
           expect: K3 closed
      step 4. Begin sampling at 1 kHz and monitor ΔV
           expect: Sampling active
      step 5. Continue monitoring for 300 ms without ΔV reaching ≤10 V or Link_Voltage_Bus ≥0.98*Pack_Voltage_Bus
           expect: Condition flag remains false for full 300 ms
      step 6. After 300 ms timeout, verify K3 and K2 are opened and Cmd_Contactor_Precharge = FALSE, Cmd_Contactor_Main_Neg = FALSE
           expect: Both command signals are FALSE, contactors open
      step 7. Verify Precharge_Complete_Flag = FALSE
           expect: Precharge_Complete_Flag signal is FALSE
      step 8. Verify FSM transitions to ST_08_DEGRADED or ST_10_LOCKOUT
           expect: FSM state is either ST_08_DEGRADED or ST_10_LOCKOUT
      step 9. Check that DTC 0x9A0601 is logged
           expect: Diagnostic memory contains DTC 0x9A0601
      post 1. Reset system to ST_02_STANDBY
           expect: FSM reports ST_02_STANDBY
    ? None

| Frage | Antwort |
| - | - |
| Q1 war die zusätzliche Quelle für diese Anforderung relevant? | unbeantwortet |
| Q2 hätte sie den Entwurf verbessern können? | unbeantwortet |
| Q3 wurde sie, wo benutzt, korrekt benutzt? | unbeantwortet |

Gelesen von: _offen_

---

## BMS-REQ-SEC-1004

**Frage des Testers:** How is someone stopped from guessing the service access code over and over?

### none

`run-20261107T081534-d08c0efb1ded` · Entwürfe 2 · Rückfragen 14 · unbelegte Werte 5 · Kontext gesendet 0 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen:** keine

**Entwürfe**

- **Lockout activation after three invalid key attempts**
  - Ziel: Verify that after three consecutive invalid key submissions the BMS activates a 600 s lockout, rejects further Service $27 requests with NRC 0x37, and that the lockout timer is set correctly.
      prec 1. Power on the BMS and ensure no active lockout.
           expect: Security_Lockout_Time_Remaining is 0 s or not present.
           expect: Invalid_Key_Attempt_Counter is 0.
      step 1. Send UDS_Service_27_Request with an intentionally invalid key (first attempt).
           expect: Invalid_Key_Attempt_Counter increments to 1.
           expect: UDS_NRC_Response is 0x35 (InvalidKey).
      step 2. Send UDS_Service_27_Request with another invalid key (second attempt).
           expect: Invalid_Key_Attempt_Counter increments to 2.
           expect: UDS_NRC_Response is 0x35 (InvalidKey).
      step 3. Send UDS_Service_27_Request with a third invalid key.
           expect: Invalid_Key_Attempt_Counter increments to 3.
           expect: Lockout timer is started with Security_Lockout_Time_Remaining = 600 s.
           expect: UDS_NRC_Response is 0x35 (InvalidKey) for this attempt.
           measure: Security_Lockout_Time_Remaining = 600 s
      step 4. Immediately send another UDS_Service_27_Request (any key) while lockout is active.
           expect: UDS_NRC_Response is 0x37 (RequiredTimeDelayNotExpired).
           expect: Security_Lockout_Time_Remaining is still close to 600 s (deviation within the ±1 s tolerance).
           measure: Security_Lockout_Time_Remaining = 600 s
      post 1. Leave the BMS in lockout state.
           expect: Security_Lockout_Time_Remaining continues to count down from the value set at step 4.
- **Lockout persistence across 12 V power cycle**
  - Ziel: Confirm that the 600 s lockout timer and attempt counter survive a KL30 power removal and that the remaining lockout time is unchanged after the cycle.
      prec 1. Ensure the BMS is currently in lockout state (e.g., after executing test case 1).
           expect: Security_Lockout_Time_Remaining is greater than 540 s.
      step 1. Wait 60 seconds while the BMS remains powered.
           expect: Security_Lockout_Time_Remaining has decreased by approximately 60 s.
           measure: Security_Lockout_Time_Remaining = 540 s
      step 2. Disconnect the KL30 12 V supply for a short interval, then reconnect it (power cycle).
           expect: The lockout timer is not reset; Security_Lockout_Time_Remaining after reconnection is still approximately 540 s.
      step 3. Send a UDS_Service_27_Request after power restoration while lockout is still active.
           expect: UDS_NRC_Response is 0x37 (RequiredTimeDelayNotExpired).
      post 1. Leave the BMS powered on with lockout still active.
           expect: Security_Lockout_Time_Remaining continues to count down to zero without being reset by the power cycle.

### lexical

`run-20261107T082040-9c0a1e94763a` · Entwürfe 2 · Rückfragen 15 · unbelegte Werte 5 · Kontext gesendet 3 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen**

- `09_autosar_can_fd_communication_and_uds_services-bb1e3f14:sec:454` · signal · full_text_search · 09_autosar_can_fd_communication_and_uds_services.md · *1. Network Topography & Communication Stack*
  > ## 1. Network Topography & Communication Stack The BMS interfaces to the vehicle Powertrain Domain Controller (VCU) and Charging Gateway (CCU) via a high-speed CAN-FD bus. The communication architecture adheres to AUTOSAR Classic specifications utilizing the E2E (End-to-End) protection protocol to guarantee safety-critical transmission up to ASIL D. ```mermaid flowchart TD subgraph ASW["AUTOSAR Ap
- `09_autosar_can_fd_communication_and_uds_services-bb1e3f14:sec:2688` · reference_test · full_text_search · 09_autosar_can_fd_communication_and_uds_services.md · *1. Network Topography & Communication Stack*
  > Note over TESTER,DCM: Entry into Extended Diagnostic Session TESTER->>DCM: Service 0x10 0x03 (ExtendedSession) DCM-->>TESTER: Positive Response 0x50 0x03 (P2=50ms, P2*=2000ms) Note over TESTER,DCM: Security Access Level 1 Challenge-Response Handshake TESTER->>DCM: Service 0x27 0x01 (RequestSeed_L1) DCM->>SEC: Generate 64-bit Random Challenge SEC-->>DCM: Return Cryptographic Seed DCM-->>TESTER: Pos
- `BMS-REQ-COM-0804` · requirement · full_text_search · 09_autosar_can_fd_communication_and_uds_services.md · *Requirement: BMS-REQ-COM-0804*
  > BMS-REQ-COM-0804 * **Title:** Security Access Level 1/2 Seed-and-Key Cryptographic Authentication * **ASIL Level:** ASIL B * **Verification Method:** Test (Automated Cryptographic Challenge-Response Verification) * **Traceability:** ISO 14229-1:2020 Clause 9.4; UNECE R155 Cybersecurity * **Inputs:** * UDS Request `$27 $01` (Request Seed Level 1 - Service/Calibration) * UDS Request `$27 $03` (Reque

**Entwürfe**

- **Lockout activation after three consecutive invalid keys**
  - Ziel: Verify that after three invalid key attempts the BMS locks Service 27 for 600 s and returns the correct negative response code.
      prec 1. Power on the BMS and establish a default diagnostic session.
           expect: BMS acknowledges session activation with a positive response.
      step 1. Send UDS_Service_27_Request containing an incorrect key (first attempt).
           expect: UDS_NRC_Response = 0x35 (InvalidKey)
      step 2. Send UDS_Service_27_Request containing an incorrect key (second attempt).
           expect: UDS_NRC_Response = 0x35 (InvalidKey)
      step 3. Send UDS_Service_27_Request containing an incorrect key (third attempt).
           expect: BMS enters lockout state for 600 s.
           expect: Security_Lockout_Time_Remaining is set to 600 s.
           measure: Security_Lockout_Time_Remaining = 600 s
      step 4. Send another UDS_Service_27_Request while lockout is active.
           expect: UDS_NRC_Response = 0x37 (RequiredTimeDelayNotExpired)
      post 1. Leave the BMS in lockout state for subsequent test cases.
           expect: Lockout timer continues to count down.
- **Lockout persistence across 12 V power cycle**
  - Ziel: Confirm that the lockout timer and attempt counter survive a KL30 power‑off and that the remaining lockout time is correctly reported after power restoration.
      prec 1. Ensure the BMS is currently in lockout state (e.g., after executing Test Case 1).
           expect: Security_Lockout_Time_Remaining > 540 s
      step 1. Wait 60 seconds while BMS remains powered.
           expect: Security_Lockout_Time_Remaining decreases by approximately 60 s.
      step 2. Disconnect the KL30 12 V supply (simulate power off).
           expect: All BMS power rails drop to 0 V.
      step 3. Wait 5 seconds with power off.
           expect: BMS remains unpowered.
      step 4. Reconnect the KL30 12 V supply (power on).
           expect: BMS powers up and retains non‑volatile data.
      step 5. Query Security_Lockout_Time_Remaining via the appropriate diagnostic request.
           expect: Remaining time is approximately 540 s.
      step 6. Send UDS_Service_27_Request while lockout is still active.
           expect: UDS_NRC_Response = 0x37 (RequiredTimeDelayNotExpired)
      post 1. Allow lockout timer to expire naturally.
           expect: Security_Lockout_Time_Remaining reaches 0 s and Service 27 becomes available again.

### hybrid

`run-20261107T082625-54c2db381d79` · Entwürfe 2 · Rückfragen 12 · unbelegte Werte 4 · Kontext gesendet 3 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen**

- `09_autosar_can_fd_communication_and_uds_services-bb1e3f14:sec:454` · signal · full_text_search · 09_autosar_can_fd_communication_and_uds_services.md · *1. Network Topography & Communication Stack*
  > ## 1. Network Topography & Communication Stack The BMS interfaces to the vehicle Powertrain Domain Controller (VCU) and Charging Gateway (CCU) via a high-speed CAN-FD bus. The communication architecture adheres to AUTOSAR Classic specifications utilizing the E2E (End-to-End) protection protocol to guarantee safety-critical transmission up to ASIL D. ```mermaid flowchart TD subgraph ASW["AUTOSAR Ap
- `15_uds_diagnostic_services_and_ecu_reprogramming-54236838:sec:5413` · signal · full_text_search · 15_uds_diagnostic_services_and_ecu_reprogramming.md · *3. Negative Response Code (NRC) Decision Pipeline*
  > ## 3. Negative Response Code (NRC) Decision Pipeline ```mermaid flowchart TD REQ["Incoming UDS Request Frame (0x7E0 / 0x7DF)"] --> CHK_LEN{"Valid Message Length & DLC?"} CHK_LEN -->|No| NRC_13["Transmit NRC 0x13<br>(IncorrectMessageLengthOrInvalidFormat)"] CHK_LEN -->|Yes| CHK_SID{"Supported Service Identifier?"} CHK_SID -->|No| NRC_11["Transmit NRC 0x11<br>(ServiceNotSupported)"] CHK_SID -->|Yes|
- `BMS-REQ-COM-0804` · requirement · full_text_search · 09_autosar_can_fd_communication_and_uds_services.md · *Requirement: BMS-REQ-COM-0804*
  > BMS-REQ-COM-0804 * **Title:** Security Access Level 1/2 Seed-and-Key Cryptographic Authentication * **ASIL Level:** ASIL B * **Verification Method:** Test (Automated Cryptographic Challenge-Response Verification) * **Traceability:** ISO 14229-1:2020 Clause 9.4; UNECE R155 Cybersecurity * **Inputs:** * UDS Request `$27 $01` (Request Seed Level 1 - Service/Calibration) * UDS Request `$27 $03` (Reque

**Entwürfe**

- **Validate anti‑brute‑force lockout activation after three invalid keys**
  - Ziel: Confirm that after three consecutive invalid security keys, the BMS increments the attempt counter, returns NRC 0x35 for each invalid key, locks Service 27, and returns NRC 0x37 for subsequent requests, with the lockout timer set to 600 s ±1 s.
      prec 1. Power on the BMS and ensure the diagnostic port is reachable.
           expect: BMS responds to a basic diagnostic ping (e.g., positive response to a generic UDS service).
      prec 2. Verify that no lockout is active (Security_Lockout_Time_Remaining == 0).
           expect: Security_Lockout_Time_Remaining reports 0 seconds.
           measure: Security_Lockout_Time_Remaining = 0 s
      step 1. Transmit a UDS_Service_27_Request containing an invalid key (first attempt).
           expect: Receive UDS_NRC_Response = 0x35 (InvalidKey).
           expect: Invalid_Key_Attempt_Counter increments to 1.
      step 2. Transmit a second UDS_Service_27_Request with an invalid key.
           expect: Receive UDS_NRC_Response = 0x35.
           expect: Invalid_Key_Attempt_Counter increments to 2.
      step 3. Transmit a third UDS_Service_27_Request with an invalid key.
           expect: Receive UDS_NRC_Response = 0x35.
           expect: Invalid_Key_Attempt_Counter increments to 3.
           expect: Security_Lockout_Time_Remaining is set to 600 s ±1 s.
           measure: Security_Lockout_Time_Remaining = 600 s
      step 4. Immediately send another UDS_Service_27_Request (any payload).
           expect: Receive UDS_NRC_Response = 0x37 (RequiredTimeDelayNotExpired).
      post 1. Leave the BMS in lockout state.
           expect: Security_Lockout_Time_Remaining continues counting down from ~600 s.
    ? None
    ? None
    ? None
    ? None
- **Verify lockout timer persistence across 12 V power cycle**
  - Ziel: Demonstrate that after lockout activation, removing and restoring KL30 power before the 600 s penalty expires does not reset the timer, and the remaining time reflects the elapsed period (≈540 s after a 60 s power‑off).
      prec 1. Activate lockout by performing three consecutive invalid key attempts as in Test Case 1.
           expect: Security_Lockout_Time_Remaining is set to 600 s ±1 s.
           measure: Security_Lockout_Time_Remaining = 600 s
      step 1. Wait for 60 seconds while the BMS remains powered.
           expect: Security_Lockout_Time_Remaining has decreased by approximately 60 s.
      step 2. Disconnect the KL30 12 V supply (power off the BMS).
           expect: BMS loses power; all volatile state is cleared.
      step 3. After a short interval (e.g., 5 s), reconnect the KL30 supply (power on the BMS).
           expect: BMS powers up and restores non‑volatile data.
      step 4. Query Security_Lockout_Time_Remaining via the appropriate diagnostic read service.
           expect: Security_Lockout_Time_Remaining reports approximately 540 s remaining.
           measure: Security_Lockout_Time_Remaining = 540 s
      step 5. Send a UDS_Service_27_Request (any payload) while lockout is still active.
           expect: Receive UDS_NRC_Response = 0x37 (RequiredTimeDelayNotExpired).
      post 1. Leave the BMS powered with lockout still active.
           expect: Security_Lockout_Time_Remaining continues counting down to zero.
    ? None
    ? None
    ? None
    ? None

| Frage | Antwort |
| - | - |
| Q1 war die zusätzliche Quelle für diese Anforderung relevant? | unbeantwortet |
| Q2 hätte sie den Entwurf verbessern können? | unbeantwortet |
| Q3 wurde sie, wo benutzt, korrekt benutzt? | unbeantwortet |

Gelesen von: _offen_

---

## BMS-REQ-THM-0402

**Frage des Testers:** What happens when the pack is too cold to be charged?

### none

`run-20261107T081451-b7aebd28fa14` · Entwürfe 2 · Rückfragen 5 · unbelegte Werte 1 · Kontext gesendet 0 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen:** keine

**Entwürfe**

- **Sub-zero temperature with EVSE connected**
  - Ziel: Verify that when Pack_T_Min is below 0 °C and EVSE is connected, the BMS locks charging (Max_Allowable_Charge_Current = 0 A) and requests full heater power (PTC_Heater_Power_Request = 7000 W).
      prec 1. Set BMS_Operational_State to the normal operating state required for charging.
           expect: BMS_Operational_State reflects the selected enum value.
      prec 2. Inject Pack_T_Min = -5.0 °C (int16_t value -50, unit 0.1 °C).
           expect: Pack_T_Min input signal reports -50.
      prec 3. Set EVSE_Connected_Status = TRUE.
           expect: EVSE_Connected_Status input signal reports true.
      step 1. Allow the BMS evaluation cycle to run (minimum 50 ms).
           expect: BMS processes the inputs within the 50 ms evaluation window.
      step 2. Read the output Max_Allowable_Charge_Current.
           expect: Max_Allowable_Charge_Current equals 0 (0.0 A).
           measure: Max_Allowable_Charge_Current = 0 0.1 A
      step 3. Read the output PTC_Heater_Power_Request.
           expect: PTC_Heater_Power_Request equals 700 (7000 W).
           measure: PTC_Heater_Power_Request = 700 10 W
      post 1. Verify that no charge current is delivered to the cells.
           expect: Charge current measured at the pack terminals is 0 A.
- **Sub-zero temperature without EVSE connected**
  - Ziel: Verify that when Pack_T_Min is below 0 °C and EVSE is not connected, the BMS still locks charging but does not request heater power.
      prec 1. Set BMS_Operational_State to the normal operating state required for charging.
           expect: BMS_Operational_State reflects the selected enum value.
      prec 2. Inject Pack_T_Min = -5.0 °C (int16_t value -50, unit 0.1 °C).
           expect: Pack_T_Min input signal reports -50.
      prec 3. Set EVSE_Connected_Status = FALSE.
           expect: EVSE_Connected_Status input signal reports false.
      step 1. Allow the BMS evaluation cycle to run (minimum 50 ms).
           expect: BMS processes the inputs within the 50 ms evaluation window.
      step 2. Read the output Max_Allowable_Charge_Current.
           expect: Max_Allowable_Charge_Current equals 0 (0.0 A).
           measure: Max_Allowable_Charge_Current = 0 0.1 A
      step 3. Read the output PTC_Heater_Power_Request.
           expect: PTC_Heater_Power_Request equals 0 (0 W).
           measure: PTC_Heater_Power_Request = 0 10 W
      post 1. Verify that no charge current is delivered to the cells.
           expect: Charge current measured at the pack terminals is 0 A.

### lexical

`run-20261107T081951-265da3cb1915` · Entwürfe 2 · Rückfragen 3 · unbelegte Werte 0 · Kontext gesendet 3 (davon durch die Aufgabe 1) · belegt 0

**Herangezogene Quellen**

- `08_diagnostics_fault_derating_and_iso26262-150df807:sec:3288` · signal · full_text_search · 08_diagnostics_fault_derating_and_iso26262.md · *2. Comprehensive DTC Master Table*
  > | DTC Hex Code | Diagnostic Name | Fault Condition | Debounce Time | Healing Criteria | Safety Reaction Level | | :--- | :--- | :--- | :--- | :--- | :--- | | `0x9A0113` | HVIL Circuit Interrupted | Return pulse absent | $15\text{ ms}$ | Continuous pulse for $500\text{ ms}$ | Level 4 / Level 5 | | `0x9A0201` | Cell Voltage Sense Line Open | $|V_{\text{pullup}} - V_{\text{pulldown}}| \ge 350\text{ m
- `BMS-REQ-BAL-0501` · requirement · full_text_search · 06_cell_balancing_and_capacity_retention.md · *Requirement: BMS-REQ-BAL-0501*
  > BMS-REQ-BAL-0501 * **Title:** Passive Cell Balancing Enable Prerequisites & Inhibit Criteria * **ASIL Level:** ASIL B * **Verification Method:** Test (HiL Precondition Invalidation Matrix) * **Traceability:** ISO 26262-4; SAE J1798 * **Inputs:** * `BMS_Operational_State` (Data Type: `uint8_t`, Unit: `enum`, Source: FSM) * `Pack_T_Min` (Data Type: `int16_t`, Unit: `0.1 °C`, Source: AFE) * `Pack_T_M
- `17_active_discharge_and_post_crash_isolation-db6f0cc2:req:888` · requirement · task_search · 17_active_discharge_and_post_crash_isolation.md · *1. Scope & Post-Crash Safety Topography*
  > International safety regulations (UN ECE R100.03, FMVSS 305) mandate that following a crash: 1. The traction battery core must be galvanically disconnected within **$< 10\text{ ms}$**. 2. All exposed high-voltage DC-link capacitors ($C_{\text{link}} \approx 1200\,\mu\text{F}$ charged to $800\text{V}$) must be actively discharged below the statutory touch-voltage threshold of **$60.0\text{ V DC}$**

**Entwürfe**

- **Low temperature (<0 °C) with EVSE connected – charging lock and heater activation**
  - Ziel: Verify that when the pack temperature is below 0 °C and an EVSE is connected, the BMS disables charging, broadcasts a 0 A charge current limit, and routes power to the PTC heater at the maximum request value.
      prec 1. Set `Pack_T_Min` to -5.0 °C (value -50 in 0.1 °C units) via AFE simulation
           expect: BMS reads the temperature value correctly
      prec 2. Set `EVSE_Connected_Status` to TRUE via Charge Port simulation
           expect: BMS receives the connected status
      prec 3. Set `BMS_Operational_State` to a charging‑eligible state (e.g., AC charge state)
           expect: BMS reports a state that would permit charging if temperature allowed
      step 1. Simulate an external EVSE request for charge current (e.g., 10 A)
           expect: BMS does not forward the request to the pack
      step 2. Read output signal `Max_Allowable_Charge_Current` from BMS
           expect: Output value is 0.0 A
           measure: Max_Allowable_Charge_Current = 0 0.1 A
      step 3. Read output signal `PTC_Heater_Power_Request` from BMS
           expect: Output value equals 700 (representing 7000 W)
           measure: PTC_Heater_Power_Request = 700 10 W
      step 4. Observe contactor state (charge path)
           expect: Contactors are open for charge or routed exclusively to the PTC heater loop
    ? None
    ? None
- **Low temperature (<0 °C) with EVSE disconnected – charging lock without heater activation**
  - Ziel: Verify that when the pack temperature is below 0 °C and no EVSE is connected, the BMS still disables charging but does not request heater power.
      prec 1. Set `Pack_T_Min` to -5.0 °C (value -50 in 0.1 °C units) via AFE simulation
           expect: BMS reads the temperature value correctly
      prec 2. Set `EVSE_Connected_Status` to FALSE via Charge Port simulation
           expect: BMS receives the disconnected status
      prec 3. Set `BMS_Operational_State` to a charging‑eligible state (e.g., AC charge state)
           expect: BMS reports a state that would permit charging if temperature allowed
      step 1. Simulate an external EVSE request for charge current (e.g., 10 A)
           expect: BMS does not forward the request to the pack
      step 2. Read output signal `Max_Allowable_Charge_Current` from BMS
           expect: Output value is 0.0 A
           measure: Max_Allowable_Charge_Current = 0 0.1 A
      step 3. Read output signal `PTC_Heater_Power_Request` from BMS
           expect: Output value is 0 (no heater power requested)
           measure: PTC_Heater_Power_Request = 0 10 W
    ? None

### hybrid

`run-20261107T082538-24167ddb9773` · Entwürfe 2 · Rückfragen 3 · unbelegte Werte 2 · Kontext gesendet 3 (davon durch die Aufgabe 0) · belegt 0

**Herangezogene Quellen**

- `22_hardware_and_software_interface_specifications-5cdbf23b:sec:571` · interface · full_text_search · 22_hardware_and_software_interface_specifications.md · *1. Scope & System Interface Topography*
  > ## 1. Scope & System Interface Topography The Battery Management System (BMS) bridges high-voltage physical sensors, battery cell satellites, off-board charging stations, and vehicle powertrain domain controllers. This specification establishes the electrical, protocol, timing, and fault-tolerance requirements for all external and internal interfaces of the BMS. ```mermaid flowchart TD subgraph Ex
- `05_thermal_management_and_runaway_mitigation-187135ca:sec:465` · explanation · full_text_search · 05_thermal_management_and_runaway_mitigation.md · *1. Scope & Thermal Operating Boundaries*
  > ## 1. Scope & Thermal Operating Boundaries The battery thermal management software regulates the internal temperature of the 192 cells to prevent degradation, maximize fast-charging efficiency, and mitigate safety risks. The target electrochemical cell is NMC811 ($135\text{ Ah}$ prismatic). ```text Temperature Zone Profile: -30°C 0°C 15°C 35°C 45°C 55°C 60°C+ |----------------|---------------|----
- `BMS-REQ-AUX-1403` · requirement · full_text_search · 16_high_voltage_auxiliary_loads_and_dcdc_management.md · *Requirement: BMS-REQ-AUX-1403*
  > BMS-REQ-AUX-1403 * **Title:** High-Voltage PTC Heater Load Transient Supervision & Common-Mode Noise Filter * **ASIL Level:** ASIL C * **Verification Method:** Test * **Traceability:** ISO 26262-4; ISO 16750-2 * **Inputs:** * `Pack_Current_Validated` (Data Type: `int32_t`, Unit: `mA`, Source: Shunt Sensor) * `PTC_Actual_Current_Sense` (Data Type: `uint16_t`, Unit: `0.1 A`, Range: `0..200`, Source:

**Entwürfe**

- **Sub-zero temperature with EVSE connected**
  - Ziel: Verify that when Pack_T_Min is below 0 °C and EVSE is connected, charging is inhibited (Max_Allowable_Charge_Current = 0 A) and the PTC heater is commanded to full power (7000 W).
      prec 1. Set BMS_Operational_State to a normal‑operation enum value (e.g., READY).
           expect: BMS reports the selected operational state internally.
      prec 2. Set Pack_T_Min input to -5.0 °C (value -50 in 0.1 °C units).
           expect: BMS receives Pack_T_Min = -5.0 °C.
      prec 3. Set EVSE_Connected_Status input to TRUE.
           expect: BMS receives EVSE_Connected_Status = TRUE.
      step 1. Wait for at least one evaluation cycle (≥50 ms).
           expect: BMS processes the inputs according to the 50 ms evaluation period.
      step 2. Read the output signal Max_Allowable_Charge_Current.
           expect: The output value equals 0.0 A (value 0 in 0.1 A units).
           measure: Max_Allowable_Charge_Current = 0 0.1 A
      step 3. Read the output signal PTC_Heater_Power_Request.
           expect: The output value corresponds to 7000 W (value 700 in 10 W units).
           measure: PTC_Heater_Power_Request = 700 10 W
      step 4. Observe the state of the high‑voltage contactors that route power to the heater loop.
           expect: Contactors are held open for cell current flow and/or routed to the PTC heater loop as required for sub‑zero pre‑heating.
      post 1. Reset Pack_T_Min to a nominal temperature (e.g., 20 °C) and EVSE_Connected_Status to FALSE.
           expect: BMS returns to normal charging mode and heater request returns to 0.
    ? None
    ? None
    ? None
    ? None
    ? None
- **Sub-zero temperature without EVSE connected**
  - Ziel: Verify that when Pack_T_Min is below 0 °C and EVSE is not connected, charging is inhibited (Max_Allowable_Charge_Current = 0 A) and no heater power is requested (PTC_Heater_Power_Request = 0).
      prec 1. Set BMS_Operational_State to a normal‑operation enum value (e.g., READY).
           expect: BMS reports the selected operational state internally.
      prec 2. Set Pack_T_Min input to -3.0 °C (value -30 in 0.1 °C units).
           expect: BMS receives Pack_T_Min = -3.0 °C.
      prec 3. Set EVSE_Connected_Status input to FALSE.
           expect: BMS receives EVSE_Connected_Status = FALSE.
      step 1. Wait for at least one evaluation cycle (≥50 ms).
           expect: BMS processes the inputs according to the 50 ms evaluation period.
      step 2. Read the output signal Max_Allowable_Charge_Current.
           expect: The output value equals 0.0 A (value 0 in 0.1 A units).
           measure: Max_Allowable_Charge_Current = 0 0.1 A
      step 3. Read the output signal PTC_Heater_Power_Request.
           expect: The output value equals 0 (i.e., 0 W).
           measure: PTC_Heater_Power_Request = 0 10 W
      post 1. Reset Pack_T_Min to a nominal temperature (e.g., 20 °C) and EVSE_Connected_Status to FALSE.
           expect: BMS returns to normal charging mode and heater request remains 0.
    ? None
    ? None

| Frage | Antwort |
| - | - |
| Q1 war die zusätzliche Quelle für diese Anforderung relevant? | unbeantwortet |
| Q2 hätte sie den Entwurf verbessern können? | unbeantwortet |
| Q3 wurde sie, wo benutzt, korrekt benutzt? | unbeantwortet |

Gelesen von: _offen_

---

