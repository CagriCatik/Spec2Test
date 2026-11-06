# Review list for the retrieval comparison

The technical usefulness of the drafts and the relevance of the retrieved
passages are **not assessed** in this report: no tester has reviewed these
runs. The numbers below are machine-countable only.

This list names exactly what a reviewer has to decide. Open the stored run
file, judge the draft, and record the decision through the review panel so
the next comparison can use real decisions instead of counts.

## What to judge per draft

1. Is the draft technically usable for this requirement, as written?
2. Which retrieved passage was relevant, which was noise?
3. Did the draft take a value from the context that the requirement did
   not ask for?
4. How much rework would it take: minor edit, rewrite, or discard?

## Pairs to review

| Requirement | without retrieval | with retrieval | retrieved passages |
| - | - | - | - |
| BMS-REQ-AFE-0101 | d44eaf5ab484 | 4205fbb83945 | 1. Scope & Protocol Topography, 3. Synchronous DAQ Configuration & High-Rate Acquisition Sequence |
| BMS-REQ-AUX-1401 | 51d1dae4bbaa | ed63cec32a95 | 2. Operational State Machine Definition |
| BMS-REQ-BAL-0501 | 9c8eb66b5795 | 7569f3ba7f47 | 1. Scope & Balancing Topography, 2. Operational State Machine Definition |
| BMS-REQ-BRK-1701 | b80429b7c83d | a6f40ecdc218 | none |
| BMS-REQ-CHG-0601 | 41e1b48e736f | f1913e0591bd | 1. Scope & Electrochemical Plating Physics, 2. CAN-FD Broadcast Message Matrix, 2. Operational State Machine Definition |
| BMS-REQ-COM-0801 | da0cd2e78f2f | 5d2d3d94b1b9 | 1. Network Topography & Communication Stack, 1. Scope & System Interface Topography |
| BMS-REQ-DIA-0701 | 00d6f8d3b398 | f7fedfeb47bd | 1. ISO 26262 Functional Safety Goals & Safe State Hierarchy, 2. Comprehensive DTC Master Table |
| BMS-REQ-DSC-1501 | 9b0ada0b7c71 | 7fb689af6c50 | none |
| BMS-REQ-EST-0201 | 22717ffcc516 | a8389980c28e | 1. Scope & Mathematical Overview |
| BMS-REQ-HVC-0301 | 019d7ed71970 | fc479dd547c8 | 2. Operational State Machine Definition |
| BMS-REQ-MECH-1101 | e9d9d8af7eb6 | 7f1841bcfc81 | 1. Scope & Degradation Physics Overview |
| BMS-REQ-PWR-0901 | 3e45364e015c | dbaaa70a2bc8 | 1. Scope & Low-Voltage Power Tree Overview |
| BMS-REQ-SC-0901 | 508c4687b581 | 44cbc19e9641 | 1. Scope & Ultra-Fast Safety Topography, 2. Ultra-Fast Short-Circuit Trip Timing Sequence |
| BMS-REQ-SEC-1001 | fd7de20fb9cd | 4f33543539f5 | 1. Scope & Threat Mitigation Topography, 2. Secure Boot Authentication Sequence |
| BMS-REQ-SYS-0010 | bb28d40b35c5 | 6f15a9a59996 | 2. Operational State Machine Definition |
| BMS-REQ-THM-0401 | 738012796add | 5ff917db02e9 | 1. Scope & System Interface Topography, 2. CAN-FD Broadcast Message Matrix, 2. Odd-Even Interleaved Thermal Management Strategy |
| BMS-REQ-UDS-1301 | f6064c06d15a | 9e9d065d3f5f | 1. ISO 26262 Functional Safety Goals & Safe State Hierarchy, 2. Operational State Machine Definition |
| BMS-REQ-VNV-1201 | 04c2766890ec | 6d6f97865434 | 2. Operational State Machine Definition, 3. Breaktor® Interruption & Automated Electronic Reset Timing Sequence |
| BMS-REQ-XCP-1801 | b90380586148 | 024b60179d7a | 1. Scope & Protocol Topography, 1. Scope & System Interface Topography |
| BMS-REQ-XFC-1601 | fe7bf92059d0 | 89e6b3468cc9 | 1. Scope & Electrochemical Plating Physics |

80 runs in total; the run files are not committed (they contain
full model answers). Regenerate them with docs/benchmarks/compare.py.
