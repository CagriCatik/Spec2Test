# BMS Software Requirements Specification: Database Schema & RAG Indexing Manifest

**Document ID:** BMS-SRS-10-RAG-DB-SCHEMA  
**Version:** 2.4.0  
**Classification:** Metadata, Extraction Schema & Retrieval Optimization  
**Purpose:** Direct automated ingestion into Relational Databases (PostgreSQL/SQLite), Graph Databases (Neo4j), and Vector RAG Pipelines (Chroma/Pinecone/Milvus).

---

## 1. RAG & Database Ingestion Strategy

To ensure optimal performance for Retrieval-Augmented Generation (RAG) and structured SQL/Graph database querying, the requirements in documents `01_` through `09_` have been authored with deterministic syntactic patterns and explicit metadata keys.

```text
                                  Requirement Markdown Documents (01_ to 09_)
                                                    |
                         +--------------------------+--------------------------+
                         |                                                     |
                         v                                                     v
            [ Semantic Document Chunker ]                             [ Regex / AST Parser ]
                         |                                                     |
                         v                                                     v
            [ Embedding Model (e.g. text-embedding-3-large) ]         [ SQL & Graph Ingestion Engine ]
                         |                                                     |
                         v                                                     v
            +--------------------------+                              +--------------------------+
            | Vector Store (ChromaDB)  |                              | Relational / Graph DB    |
            |  - Chunk Text            |                              |  - PostgreSQL Tables     |
            |  - Metadata Filter Tags  |                              |  - Neo4j Triplets        |
            +--------------------------+                              +--------------------------+
```

---

## 2. Relational Database Schema (SQL DDL)

The following PostgreSQL / SQLite compatible schema enables direct tabular persistence of all parsed requirement entities, signals, and diagnostic links.

```sql
-- 1. Requirements Core Table
CREATE TABLE bms_requirements (
    requirement_id VARCHAR(32) PRIMARY KEY,       -- e.g., 'BMS-REQ-SYS-0010'
    document_id VARCHAR(32) NOT NULL,             -- e.g., 'BMS-SRS-01-ARCH-MODES'
    title VARCHAR(255) NOT NULL,
    asil_level VARCHAR(8) NOT NULL,               -- 'QM', 'ASIL A', 'ASIL B', 'ASIL C', 'ASIL D'
    category VARCHAR(64) NOT NULL,                -- 'System', 'AFE', 'Estimation', 'Contactor', etc.
    verification_method VARCHAR(64) NOT NULL,     -- 'Test (HiL)', 'Analysis', 'Inspection', etc.
    traceability_standards TEXT,                  -- 'ISO 26262-5:2018; UN ECE R100.03'
    cycle_time_ms NUMERIC(6, 2),                  -- e.g., 10.00
    debounce_time_ms NUMERIC(6, 2),               -- e.g., 50.00
    description TEXT NOT NULL,
    rationale TEXT NOT NULL,
    acceptance_criteria TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Input/Output Signals Table
CREATE TABLE bms_signals (
    signal_id SERIAL PRIMARY KEY,
    requirement_id VARCHAR(32) REFERENCES bms_requirements(requirement_id) ON DELETE CASCADE,
    direction VARCHAR(8) CHECK (direction IN ('INPUT', 'OUTPUT')),
    signal_name VARCHAR(128) NOT NULL,
    data_type VARCHAR(32) NOT NULL,               -- 'uint16_t', 'int32_t', 'boolean', etc.
    unit VARCHAR(32),                             -- 'mV', 'mA', '0.1 °C', '0.01 %', etc.
    range_min VARCHAR(32),
    range_max VARCHAR(32),
    source_destination VARCHAR(128)               -- 'AFE isoSPI', 'CAN-FD 0x100', 'Shunt', etc.
);

-- 3. Diagnostic Trouble Codes (DTC) Reference Table
CREATE TABLE bms_dtc_catalog (
    dtc_code VARCHAR(16) PRIMARY KEY,             -- e.g., '0x9A0113'
    requirement_id VARCHAR(32) REFERENCES bms_requirements(requirement_id),
    diagnostic_name VARCHAR(128) NOT NULL,
    qualification_condition TEXT NOT NULL,
    debounce_time_ms INTEGER NOT NULL,
    healing_criteria TEXT NOT NULL,
    safety_reaction_level VARCHAR(32) NOT NULL    -- 'LEVEL_1' to 'LEVEL_5'
);

-- 4. Operational State Machine Definition Table
CREATE TABLE bms_state_machine (
    state_id VARCHAR(16) PRIMARY KEY,             -- e.g., 'ST_01'
    state_name VARCHAR(64) NOT NULL,              -- e.g., 'INIT_SELFTEST'
    description TEXT NOT NULL,
    contactors_allowed_state VARCHAR(128) NOT NULL
);

-- 5. Indexes for High-Speed Retrieval
CREATE INDEX idx_req_asil ON bms_requirements(asil_level);
CREATE INDEX idx_req_category ON bms_requirements(category);
CREATE INDEX idx_sig_name ON bms_signals(signal_name);
CREATE INDEX idx_dtc_code ON bms_dtc_catalog(dtc_code);
```

---

## 3. Knowledge Graph Triplets (Entity-Relationship Graph)

For graph databases (e.g. Neo4j / Amazon Neptune), nodes and relationships are mapped as follows:

```text
(:Requirement {id: "BMS-REQ-SYS-0010"}) -[:REQUIRES_SIGNAL]-> (:Signal {name: "KL15_Ignition_State"})
(:Requirement {id: "BMS-REQ-SYS-0010"}) -[:PRODUCES_SIGNAL]-> (:Signal {name: "BMS_Operational_State"})
(:Requirement {id: "BMS-REQ-SYS-0010"}) -[:MAPPED_TO_STATE]-> (:State {id: "ST_01", name: "INIT_SELFTEST"})
(:Requirement {id: "BMS-REQ-SYS-0040"}) -[:TRIGGERS_FAULT]-> (:DTC {code: "0x9A0113", name: "HVIL Broken"})
(:Requirement {id: "BMS-REQ-THM-0403"}) -[:MITIGATES_HAZARD]-> (:SafetyGoal {id: "SG-03", asil: "ASIL D"})
(:Requirement {id: "BMS-REQ-HVC-0301"}) -[:DEPENDS_ON]-> (:Requirement {id: "BMS-REQ-AFE-0105"})
```

---

## 4. Vector Chunking Specification for RAG Ingestion

When chunking files `01_` through `09_` for vector stores:
1. **Primary Chunk Boundary:** Split at Level 3 Headings (`### Requirement: BMS-REQ-...`).
2. **Chunk Size:** Approximately $350 - 600$ tokens per requirement card.
3. **Chunk Overlap:** $0$ tokens required because each requirement is self-contained with its inputs, outputs, mathematical formulation, and acceptance criteria.
4. **Metadata Extraction Fields (Payload Metadata):**
   * `req_id`: Extracted from heading (e.g., `BMS-REQ-SYS-0010`)
   * `title`: Extracted from `* **Title:**`
   * `asil`: Extracted from `* **ASIL Level:**`
   * `module`: Derived from filename (e.g., `01_system_architecture`, `02_cell_monitoring`)
   * `verification`: Extracted from `* **Verification Method:**`
   * `inputs`: Array of input signal names
   * `outputs`: Array of output signal names
   * `dtc`: Array of referenced DTC codes

---

## 5. Master Requirements Cross-Reference Index

| Requirement ID | Module | Title | ASIL | Primary Observation Channel |
| :--- | :--- | :--- | :--- | :--- |
| `BMS-REQ-SYS-0010` | 01_Architecture | Bootstrapping & Self-Test | ASIL D | `BMS_Boot_Status`, SPI bus |
| `BMS-REQ-SYS-0020` | 01_Architecture | State Machine Flow Control | ASIL C | `BMS_Operational_State`, CAN `0x100` |
| `BMS-REQ-SYS-0030` | 01_Architecture | Safe Operating Area (SOA) | ASIL D | `SOA_Violation_Flag`, `Cell_V_Min/Max` |
| `BMS-REQ-SYS-0040` | 01_Architecture | HVIL Interlock Continuity | ASIL D | `HVIL_Status`, PWM loop timer |
| `BMS-REQ-SYS-0050` | 01_Architecture | Crash Signal & Pyro Actuation | ASIL D | `Pyrofuse_Fire_Command`, Pyro Bridge |
| `BMS-REQ-SYS-0060` | 01_Architecture | Sleep & Wakeup Management | ASIL B | `SBC_Power_Mode_Command`, KL30 current |
| `BMS-REQ-AFE-0101` | 02_AFE Sensing | Synchronous Cell Voltage ADC | ASIL D | `Filt_Cell_Voltage[1..192]`, CAN `0x101` |
| `BMS-REQ-AFE-0102` | 02_AFE Sensing | Sense Wire Open-Circuit | ASIL C | `Open_Wire_Fault_Bitmap`, DTC `0x9A0201` |
| `BMS-REQ-AFE-0103` | 02_AFE Sensing | NTC Temperature & Gradient | ASIL C | `Module_Temperature[1..64]`, CAN `0x102` |
| `BMS-REQ-AFE-0104` | 02_AFE Sensing | Shunt/Hall Current Sensing | ASIL D | `Pack_Current_Validated`, CAN `0x103` |
| `BMS-REQ-AFE-0105` | 02_AFE Sensing | Pack & Link Voltage Divider | ASIL C | `Pack_Voltage_Bus`, `Link_Voltage_Bus` |
| `BMS-REQ-AFE-0106` | 02_AFE Sensing | Chassis Insulation Monitoring| ASIL D | `Isolation_Resistance_kOhm`, CAN `0x104`|
| `BMS-REQ-EST-0201` | 03_Estimation | AEKF State of Charge (SOC) | ASIL C | `BMS_Pack_SOC`, CAN-FD `0x110` |
| `BMS-REQ-EST-0202` | 03_Estimation | OCV Standstill Relaxation | ASIL B | `SOC_Recalibration_Flag`, Rest Timer |
| `BMS-REQ-EST-0203` | 03_Estimation | Capacity Fade SOH_C | ASIL B | `Pack_Capacity_Actual_Ah`, CAN `0x111` |
| `BMS-REQ-EST-0204` | 03_Estimation | Ohmic Resistance R0 / SOH_R | ASIL B | `Cell_R0_Ohmic[1..192]`, CAN `0x111` |
| `BMS-REQ-EST-0205` | 03_Estimation | Dynamic SOP Power Solver | ASIL C | `BMS_Max_Discharge/Charge_Power` |
| `BMS-REQ-EST-0206` | 03_Estimation | Usable Energy (SOE) & Range | ASIL B | `Remaining_Usable_Energy_kWh`, CAN `0x113`|
| `BMS-REQ-HVC-0301` | 04_Contactors | High-Voltage Precharge | ASIL D | K1, K2, K3 drive signals, $\Delta V$ check |
| `BMS-REQ-HVC-0302` | 04_Contactors | Precharge Resistor Energy | ASIL C | $E_{\text{pre}}$ Joules, Inhibit Flag |
| `BMS-REQ-HVC-0303` | 04_Contactors | Passive/Active Weld Detection| ASIL D | `Contactor_Weld_Status_K1/K2` |
| `BMS-REQ-HVC-0304` | 04_Contactors | High-Current Open Under Load | ASIL D | VCU torque rampdown, current clamp |
| `BMS-REQ-HVC-0305` | 04_Contactors | Coil Economizer PWM | ASIL C | Coil PWM Duty Cycle, Holding Current |
| `BMS-REQ-HVC-0306` | 04_Contactors | DC Fast Charge Interlock | ASIL D | K4, K5 drive signals, Inlet match |
| `BMS-REQ-THM-0401` | 05_Thermal | Operating Zones & Coolant Flow| ASIL B | `Coolant_Target_Flow_Rate`, CAN `0x210` |
| `BMS-REQ-THM-0402` | 05_Thermal | Sub-Zero Pre-Heating & Inhibit| ASIL C | `PTC_Heater_Power_Request`, Current Lim |
| `BMS-REQ-THM-0403` | 05_Thermal | Thermal Runaway Sensor Fusion| ASIL D | `Thermal_Runaway_Detected_Flag` |
| `BMS-REQ-THM-0404` | 05_Thermal | ECE R100 5-Minute Alarm | ASIL D | CAN-FD `0x020`, eCall Discrete Trigger |
| `BMS-REQ-THM-0405` | 05_Thermal | Enclosure Vent & Deluge Flush| ASIL D | `Emergency_Coolant_Pump_Cmd`, Contactors |
| `BMS-REQ-BAL-0501` | 06_Balancing | Balancing Prerequisites | ASIL B | `Balancing_Permitted_Flag` |
| `BMS-REQ-BAL-0502` | 06_Balancing | Target Voltage & Bleed List | ASIL B | `Cell_Balancing_Candidate_Mask[1..192]` |
| `BMS-REQ-BAL-0503` | 06_Balancing | Odd-Even Interleaved Thermal | ASIL B | `CMU_Balancing_Command[1..16]`, Die Temp|
| `BMS-REQ-BAL-0504` | 06_Balancing | Bleed FET Stuck Diagnostics | ASIL C | DTC `0x9A0701`, DTC `0x9A0702` |
| `BMS-REQ-BAL-0505` | 06_Balancing | Long-Term Imbalance Logging | QM | `Pack_Cell_Imbalance_Severity`, DID `0x4210`|
| `BMS-REQ-CHG-0601` | 07_Charging | DC HPC Target Negotiation | ASIL C | `BMS_Target_Current_Req`, CAN `0x131` |
| `BMS-REQ-CHG-0602` | 07_Charging | CC-CV Current Taper Control | ASIL C | PI current trim, $V_{\text{cell,max}} \le 4200\text{ mV}$ |
| `BMS-REQ-CHG-0603` | 07_Charging | Charge Port Pin Overtemp | ASIL C | `Inlet_Thermal_Derate_Factor`, DTC `0x9A0801`|
| `BMS-REQ-CHG-0604` | 07_Charging | Automatic Cutoff & Top-Off | ASIL B | `Charge_Complete_Flag`, Taper $< 4.5\text{ A}$ |
| `BMS-REQ-CHG-0605` | 07_Charging | Emergency Fast-Stop ISO 15118| ASIL D | `BMS_Fast_Stop_Request`, K4/K5 Open |
| `BMS-REQ-CHG-0606` | 07_Charging | V2L Bidirectional Discharge | ASIL C | `V2L_Max_Continuous_Discharge_Power` |
| `BMS-REQ-DIA-0701` | 08_Diagnostics | ASIL Fault Classification | ASIL D | `Active_Fault_Severity`, Trip Line |
| `BMS-REQ-DIA-0702` | 08_Diagnostics | Dynamic Power Derating Gov | ASIL C | `VCU_Allowed_Discharge/Regen_Power` |
| `BMS-REQ-DIA-0703` | 08_Diagnostics | AUTOSAR Dem Debounce Filter | ASIL C | `Dem_Event_Status`, $+10/-5$ step counter |
| `BMS-REQ-DIA-0704` | 08_Diagnostics | Freeze Frame Snapshot Capture| ASIL B | 64-byte Flash Freeze Frame Record |
| `BMS-REQ-DIA-0705` | 08_Diagnostics | Limp-Home Mode Enforcement | ASIL C | `Limp_Home_Active_Flag`, Speed $\le 60\text{ km/h}$|
| `BMS-REQ-COM-0801` | 09_Communication| Cyclic CAN-FD Telemetry | ASIL B | Jitter $\le \pm 1\text{ ms}$, CAN IDs `0x100..0x131`|
| `BMS-REQ-COM-0802` | 09_Communication| AUTOSAR E2E Profile CRC8 | ASIL D | Alive Counter, CRC `0x1D`, Data ID |
| `BMS-REQ-COM-0803` | 09_Communication| UDS Core Server Support | ASIL B | Services `$10, $11, $14, $19, $22, $2E, $3E`|
| `BMS-REQ-COM-0804` | 09_Communication| Security Access Level 1/2 | ASIL B | Service `$27`, TRNG seed, 600s lockout |
| `BMS-REQ-COM-0805` | 09_Communication| EOL Manufacturing Routines | ASIL C | Services `$2F, $31`, Speed interlock |

