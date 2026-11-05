# BMS Software Requirements Specification: Pyrofuse Actuation & Hardware Short-Circuit Protection

**Document ID:** BMS-SRS-11-PYRO-HW-SAFETY  
**Version:** 2.4.0  
**Classification:** Automotive High-Voltage Powertrain (EV)  
**Safety Compliance:** ISO 26262:2018 ASIL D (Hardware Safety Mechanism & Software Supervisor)  
**Target Hardware:** Pyrotechnic Disconnect Switch (Squib firing bridge), Fast Analog HW Comparator ($< 10\,\mu\text{s}$), Isolated Shunt Modulator, Secondary Magnetic Reed Sensor

---

## 1. Scope & Ultra-Fast Safety Topography

In an 800V nominal electric vehicle battery pack, a low-impedance external short circuit (e.g., traction inverter power transistor shoot-through, crash-induced busbar breach, or terminal puncture) can ramp up short-circuit current to $25,000\text{ A}$ within hundreds of microseconds. Standard electromechanical contactors cannot interrupt currents beyond their arc breaking capacity ($\sim 2000\text{ A}$), as doing so would cause plasma flashover and catastrophic enclosure destruction.

To eliminate this hazard, the BMS integrates a **Pyrotechnic Circuit Disconnect (Pyrofuse)** triggered by a hardware-autonomous fast trip loop combined with redundant ASIL D MCU supervision.

```mermaid
flowchart TD
    subgraph Current_Sensor_Analog["Isolated Shunt Transducer (100 uOhm Manganin)"]
        SHUNT["Physical Shunt Resistor"]
        ANALOG_AMP["Ultra-Fast Isolated Instrumentation Amplifier"]
        ANALOG_COMP["High-Speed Analog Comparator (Response < 1.5 us)"]
        VREF["Precision Calibrated Reference V_ref (Threshold = 2500 A)"]
    end

    subgraph Hardware_Trip_Logic["Autonomous Hardware Fast-Trip Circuit (ASIL D)"]
        LATCH["High-Speed SR Flip-Flop Latch"]
        HW_GATE["Hardware Gate Interlock (Independent of MCU Core)"]
        DRV_HS["High-Side Driver (Isolated P-MOSFET)"]
        DRV_LS["Low-Side Driver (Isolated N-MOSFET)"]
        CAP_RESERVE["Energy Storage Firing Capacitor (470 uF / 25V)"]
    end

    subgraph Pyrotechnic_Actuator["Pyrotechnic Disconnect Switch"]
        SQUIB["Pyrotechnic Gas Squib (R_loop = 2.0 Ohm)"]
        PISTON["Piston & Hardened Ceramic Blade"]
        BUSBAR["Pack Positive Copper Busbar Bridge"]
        CHAMBER["Arc Quenching De-Ionization Chamber"]
    end

    subgraph MCU_Supervisor["BMU Master Microcontroller (Dual Core Lockstep)"]
        INT_PIN["Fast Hardware External Interrupt Line (NMI)"]
        SW_SUPERVISOR["Pyrofuse Supervisor Software Component"]
        SQUIB_DIAG["Squib Diagnostic Circuit (10 mA Test Current)"]
        CAN_REPORT["CAN-FD Emergency Telemetry Dispatch"]
    end

    SHUNT --> ANALOG_AMP
    ANALOG_AMP --> ANALOG_COMP
    VREF --> ANALOG_COMP
    ANALOG_COMP -->|Trip Pulse < 1.5 us| LATCH

    LATCH --> HW_GATE
    LATCH --> INT_PIN

    CAP_RESERVE --> DRV_HS
    HW_GATE --> DRV_HS
    HW_GATE --> DRV_LS

    DRV_HS --> SQUIB
    SQUIB --> DRV_LS

    SQUIB -->|Expanding Gas (1000 bar)| PISTON
    PISTON -->|Shears Busbar in < 1.2 ms| BUSBAR
    BUSBAR --> CHAMBER

    INT_PIN --> SW_SUPERVISOR
    SQUIB_DIAG -.->|Diagnostic Pulse| SQUIB
    SW_SUPERVISOR --> CAN_REPORT
```

---

## 2. Ultra-Fast Short-Circuit Trip Timing Sequence

```mermaid
sequenceDiagram
    autonumber
    participant SHORT as External Short-Circuit Fault
    participant SHUNT as 100 uOhm Shunt & Analog Comparator
    participant HW as Hardware Firing Latch & Drivers
    participant SQUIB as Pyrotechnic Squib Switch
    participant MCU as BMU Dual Lockstep Core
    participant CONT as High Voltage Contactors

    SHORT->>SHUNT: Current Ramps: 0A -> 2500A (di/dt = 1.5 kA/us)
    SHUNT->>SHUNT: Shunt Voltage Exceeds 250 mV (2500 A Threshold)
    SHUNT->>HW: Comparator Output Triggers Hardware Latch (t <= 1.5 us)
    
    par Autonomous Hardware Ignition Loop
        HW->>SQUIB: Discharge Firing Capacitor (1.75 A Fire Pulse, t <= 10 us)
        SQUIB->>SQUIB: Chemical Propellant Ignites & Piston Drives Blade
        SQUIB->>SHORT: Copper Busbar Sheared; Arc Quenched in Chamber (t <= 1.5 ms)
    and Simultaneous Core Interrupt
        HW->>MCU: Assert Non-Maskable Interrupt (NMI Line)
        MCU->>MCU: Lockout Contactor Drivers (Prevent Opening Under Arc)
        MCU->>CONT: Inhibit Opening Until Current Extinction Confirmed
        MCU->>MCU: Latch Permanent Hardware Lockout State ST_10
    end
```

---

## 3. Detailed Functional Requirements

### Requirement: BMS-REQ-SC-0901
* **Title:** Autonomous Hardware Ultra-Fast Short-Circuit Detection Loop
* **ASIL Level:** ASIL D
* **Verification Method:** Test (HiL Ultra-Fast Transient Current Injection & High-Speed Oscilloscope Capture)
* **Traceability:** ISO 26262-5:2018 Clause 8; UN ECE R100.03
* **Inputs:**
  * `Shunt_Differential_Analog_Voltage` (Data Type: `analog`, Unit: `mV`, Range: `-500..500`, Source: Hardware Shunt Leads)
  * `HW_Comparator_Reference_Voltage` (Data Type: `analog`, Calibrated Value: `250.0 mV` $\pm 2.0\text{ mV}$)
* **Outputs:**
  * `HW_Trip_Latch_Output` (Data Type: `boolean`, Unit: `discrete`, Destination: Firing Gate & MCU NMI Pin)
  * `Short_Circuit_Interrupt_Active` (Data Type: `boolean`, Unit: `discrete`, Destination: BMU Core 0 NMI)
* **Timing & Debounce:** Hardware analog propagation delay: $\le 1.5\,\mu\text{s}$. Total response time to hardware latch assertion: $\le 2.0\,\mu\text{s}$. Zero digital filtering/debouncing permitted.
* **Description:**  
  The BMS shall implement an autonomous analog hardware circuit that operates independently of microcontroller clock generation and software execution:
  1. The voltage across the primary $100\,\mu\Omega$ current shunt shall be conditioned by an isolated wideband analog instrumentation amplifier (bandwidth $\ge 500\text{ kHz}$).
  2. The amplified signal shall feed directly into a high-speed analog comparator referenced against `HW_Comparator_Reference_Voltage` ($250.0\text{ mV}$ corresponding to $|I_{\text{fault}}| = 2500\text{ A}$).
  3. When $|I_{\text{fault}}| \ge 2500.0\text{ A}$, the comparator shall assert a digital rising edge driving a hardware set-reset (SR) flip-flop latch.
  4. The latch output `HW_Trip_Latch_Output` shall immediately drive the pyrofuse firing bridge and route an asynchronous Non-Maskable Interrupt (`NMI`) to the microcontroller core.
* **Rationale:** A severe short circuit can collapse the 12V auxiliary rail or corrupt microcontroller execution. Hardware-autonomous tripping guarantees disconnection even during total ECU core brownout.
* **Acceptance Criteria:**  
  When an analog pulse simulating $2550\text{ A}$ current step is applied to the shunt terminals, `HW_Trip_Latch_Output` must transition to HIGH within $\le 2.0\,\mu\text{s}$.

---

### Requirement: BMS-REQ-SC-0902
* **Title:** Dual-Channel Interlocked Pyrotechnic Driver Bridge Actuation
* **ASIL Level:** ASIL D
* **Verification Method:** Test (HiL Squib Firing Simulator & Load Resistor Pulse Integration)
* **Traceability:** ISO 26262-5:2018; FMVSS 305
* **Inputs:**
  * `HW_Trip_Latch_Output` (Data Type: `boolean`, Unit: `discrete`, Source: Fast Analog Comparator)
  * `SW_Crash_Validation_Flag` (Data Type: `boolean`, Unit: `bool`, Source: BMU Software Safety Core)
  * `Pyro_Reserve_Capacitor_Voltage` (Data Type: `uint16_t`, Unit: `mV`, Range: `0..30000`, Source: Internal ADC)
* **Outputs:**
  * `Gate_Driver_HighSide` (Data Type: `boolean`, Unit: `discrete`, Destination: P-MOSFET Firing Switch)
  * `Gate_Driver_LowSide` (Data Type: `boolean`, Unit: `discrete`, Destination: N-MOSFET Firing Switch)
* **Timing & Debounce:** Driver assertion latency: $\le 10\,\mu\text{s}$ from HW latch; pulse duration: $10.0\text{ ms} \pm 0.5\text{ ms}$; minimum firing current: $1.75\text{ A}$.
* **Description:**  
  The BMS shall control pyrofuse detonation through a dual-channel interlocked H-bridge topology:
  1. Detonation requires concurrent conduction of both `Gate_Driver_HighSide` (connected to a dedicated $470\,\mu\text{F} / 25\text{V}$ energy storage reserve capacitor) and `Gate_Driver_LowSide` (connected to power ground).
  2. Detonation shall be triggered by:
     * **Autonomous Path:** `HW_Trip_Latch_Output == TRUE` (Direct short circuit), OR
     * **Software Safety Path:** `SW_Crash_Validation_Flag == TRUE` (Hardwired crash or E2E CAN severe impact).
  3. The firing bridge shall sustain a guaranteed constant-current discharge pulse of $I_{\text{fire}} \ge 1.75\text{ A}$ through the $2.0\,\Omega$ squib loop for exactly $10.0\text{ ms}$.
  4. Single-point failure tolerance: A short circuit of either the high-side MOSFET to 12V or the low-side MOSFET to GND shall **NEVER** detonate the squib in the absence of an intentional trigger command.
* **Rationale:** Accidental detonation of the pyrofuse during normal highway driving would cause abrupt loss of vehicle power; dual-switch interlocking prevents unintended actuation.
* **Acceptance Criteria:**  
  1. The firing pulse through a simulated $2.0\,\Omega$ dummy squib must exceed $1.75\text{ A}$ within $\le 10\,\mu\text{s}$ of latch trigger.  
  2. Forcing either high-side or low-side driver gate high individually must not produce measurable current through the squib.

---

### Requirement: BMS-REQ-SC-0903
* **Title:** Post-Detonation Plasma Arc Extinction & Galvanic Clearance Verification
* **ASIL Level:** ASIL D
* **Verification Method:** Test (HiL Disconnect Potential Boundary Verification)
* **Traceability:** ISO 26262-4; UN ECE R100.03
* **Inputs:**
  * `Raw_V_Pack_Terminal` (Data Type: `uint32_t`, Unit: `mV`, Source: Upstream Pack Voltage Divider)
  * `Raw_V_Link_Inverter` (Data Type: `uint32_t`, Unit: `mV`, Source: Downstream Inverter Voltage Divider)
  * `Pack_Current_Validated` (Data Type: `int32_t`, Unit: `mA`, Source: Shunt Sensor)
* **Outputs:**
  * `Pyrofuse_Severed_Status` (Data Type: `uint8_t`, Unit: `enum`, Values: `{INTACT, SEVERED_VERIFIED, ARC_SUSTAINED_FAULT}`, Destination: Safety Monitor)
  * `DTC_Pyro_Arc_Extinction_Failure` (Data Type: `uint32_t`, Value: `0x9A0910`, Destination: Dem)
* **Timing & Debounce:** Verification execution: $20\text{ ms}$ post-firing pulse completion. Re-check interval: $50\text{ ms}$.
* **Description:**  
  Following the dispatch of a pyrofuse fire pulse, the BMS software shall verify physical galvanic severance and plasma arc quenching:
  1. The software shall sample the upstream battery terminal voltage $V_{\text{pack}}$ and downstream bus voltage $V_{\text{link}}$.
  2. If $|V_{\text{pack}} - V_{\text{link}}| \ge 50.0\text{ V}$ AND $|I_{\text{pack}}| \le 200\text{ mA}$:
     * Set `Pyrofuse_Severed_Status = SEVERED_VERIFIED`.
  3. If $|I_{\text{pack}}| > 1.0\text{ A}$ or $|V_{\text{pack}} - V_{\text{link}}| < 10.0\text{ V}$ after $20\text{ ms}$ post-detonation:
     * Flag `Pyrofuse_Severed_Status = ARC_SUSTAINED_FAULT`.
     * Store critical emergency DTC `0x9A0910` (Plasma Arc Failed to Extinguish).
     * Command secondary mechanical contactor opening backup immediately.
* **Rationale:** In rare ultra-high current scenarios, conductive vaporized metal plasma can form across the severed gap; verifying potential separation confirms complete galvanic isolation.
* **Acceptance Criteria:**  
  `Pyrofuse_Severed_Status` must evaluate to `SEVERED_VERIFIED` within $25.0\text{ ms}$ after firing when bus potential drops to zero.

---

### Requirement: BMS-REQ-SC-0904
* **Title:** Pyrofuse Squib Circuit Diagnostic Continuity & Loop Resistance Test
* **ASIL Level:** ASIL C
* **Verification Method:** Test (HiL Programmable Squib Loop Resistance Sweep)
* **Traceability:** ISO 26262-5:2018 Clause 7; SAE J1798
* **Inputs:**
  * `Squib_Diag_Current_Sense` (Data Type: `uint16_t`, Unit: `0.1 mA`, Range: `0..500`, Source: Internal Diagnostic ADC)
  * `Squib_Diag_Voltage_Sense` (Data Type: `uint16_t`, Unit: `mV`, Range: `0..5000`, Source: Internal Diagnostic ADC)
* **Outputs:**
  * `Squib_Loop_Resistance_mOhm` (Data Type: `uint16_t`, Unit: `mOhm`, Range: `0..10000`, Destination: Diagnostics)
  * `DTC_Squib_Open_Circuit` (Data Type: `uint32_t`, Value: `0x9A0911`, Destination: Dem)
  * `DTC_Squib_Short_Circuit` (Data Type: `uint32_t`, Value: `0x9A0912`, Destination: Dem)
* **Timing & Debounce:** Diagnostic frequency: Executed at every boot-up (`ST_01`) and periodically every $60\text{ s}$ in `ST_02_STANDBY`. Test pulse current: $\le 15.0\text{ mA}$ (must never exceed $50\text{ mA}$ non-fire threshold).
* **Description:**  
  The BMS shall continuously monitor the operational health of the pyrofuse initiator squib:
  1. The diagnostic circuit shall inject a micro-current pulse of $I_{\text{diag}} = 10.0\text{ mA} \pm 1.0\text{ mA}$ for a duration of $2.0\text{ ms}$.
  2. The software shall calculate squib loop impedance:
     $$R_{\text{squib}} = \frac{V_{\text{meas}}}{I_{\text{diag}}}$$
  3. Acceptance bounds for nominal squib:
     * **Nominal:** $1.70\,\Omega \le R_{\text{squib}} \le 2.50\,\Omega$ ($1700\text{ m}\Omega$ to $2500\text{ m}\Omega$).
     * **Open Circuit:** If $R_{\text{squib}} > 4.50\,\Omega$, qualify `DTC_Squib_Open_Circuit = 0x9A0911`.
     * **Short Circuit:** If $R_{\text{squib}} < 1.00\,\Omega$, qualify `DTC_Squib_Short_Circuit = 0x9A0912`.
  4. If either fault is qualified, transition operating state to `ST_08_DEGRADED` and prohibit fast charging.
* **Rationale:** Corrosion, connector pin fretting, or harness pinch can open the squib loop, leaving the vehicle defenseless against catastrophic short circuits.
* **Acceptance Criteria:**  
  When an external resistor of $4.8\,\Omega$ is switched into the squib circuit, `DTC_Squib_Open_Circuit` must qualify within $\le 2$ consecutive test cycles without triggering the pyrofuse.

---

### Requirement: BMS-REQ-SC-0905
* **Title:** Contactor Breaking Current Arc Threshold Coordination
* **ASIL Level:** ASIL D
* **Verification Method:** Test (HiL Current Threshold Boundary Arbitration)
* **Traceability:** ISO 26262-4
* **Inputs:**
  * `Pack_Current_Validated` (Data Type: `int32_t`, Unit: `mA`, Source: Shunt)
  * `BMS_Operational_State` (Data Type: `uint8_t`, Unit: `enum`, Source: FSM)
  * `Emergency_Shutdown_Req` (Data Type: `boolean`, Unit: `bool`, Source: Safety Engine)
* **Outputs:**
  * `Contactor_Open_Permitted` (Data Type: `boolean`, Unit: `bool`, Destination: Contactor Driver)
  * `Pyrofuse_Fire_Immediate_Req` (Data Type: `boolean`, Unit: `bool`, Destination: Firing Bridge)
* **Timing & Debounce:** Arbitration cycle: $1\text{ ms} \pm 0.05\text{ ms}$.
* **Description:**  
  When an emergency shutdown is requested (`Emergency_Shutdown_Req == TRUE`):
  1. The software shall evaluate instantaneous pack current magnitude:
     * **Zone 1 ($|I_{\text{pack}}| \le 2000.0\text{ A}$):** Current is within the mechanical contactor rated breaking envelope. Set `Contactor_Open_Permitted = TRUE`. Open Main Positive K1 and Main Negative K2.
     * **Zone 2 ($|I_{\text{pack}}| > 2000.0\text{ A}$):** Current exceeds contactor safe interruption capacity. Set `Contactor_Open_Permitted = FALSE` (lock contactors CLOSED to avoid contactor explosion/plasma arc). Assert `Pyrofuse_Fire_Immediate_Req = TRUE`.
  2. Once the pyrofuse has severed the circuit and $|I_{\text{pack}}| < 5.0\text{ A}$, release `Contactor_Open_Permitted = TRUE` to open the mechanical contactors as a secondary air gap.
* **Rationale:** Opening a standard sealed vacuum or gas-filled contactor under 3000+ Amps can vaporize the contacts and rupture the contactor ceramic casing.
* **Acceptance Criteria:**  
  At an injected current of $2200\text{ A}$, mechanical contactor open commands must be suppressed while `Pyrofuse_Fire_Immediate_Req` asserts within $\le 1\text{ ms}$.

