# Retrieval comparison

Does supporting context from the knowledge index make the drafts better?

## Setup

| | |
| - | - |
| Requirements | 20, one from each document of `requirements/` |
| Variants | without retrieval, with retrieval |
| Repetitions | 2 per requirement and variant |
| Runs | 80 |
| Model | `gpt-oss:120b-cloud` through a local Ollama daemon |
| Settings | identical in both arms: at most 2 drafts per unit, 1 repair, 420 s budget, explicit reference context on in both |

The only difference between the arms is `use_retrieval`. Reproduce with
`docs/benchmarks/compare.py`, evaluate with `analyse.py`. Raw per-run metrics
are in `retrieval-comparison-rows.json`, the evaluation in
`retrieval-comparison-result.txt`.

## Result

| Metric (mean per run) | without retrieval | with retrieval |
| - | -: | -: |
| completed / failed | 37 / 3 | 37 / 3 |
| test drafts | 1.82 | 1.85 |
| open questions | 8.28 | 8.10 |
| unsupported values | 1.27 | **1.62** |
| values traced to context | 0.00 | 0.07 |
| expectations without a measurable criterion | 6.00 | 5.45 |
| possible value conflicts | 0.00 | 0.20 |
| context items sent | 0.00 | 1.55 |
| model calls | 1.50 | 1.55 |
| repairs | 0.50 | 0.55 |
| seconds | 10.8 | 10.3 |

**Retrieval did not improve the drafts in this configuration.** The success
rate is identical, open questions are unchanged within noise, and unsupported
values are slightly *worse*. Only 16 of 62 retrieved passages (26 %) were
referenced in the resulting drafts at all, and almost no value was traced back
to the context.

Per requirement the picture is mixed rather than systematic: open questions
drop for 8 requirements, rise for 7 and stay equal for 5. The spread between
the two repetitions of one requirement is often larger than the difference
between the arms.

## Failures

All six failures were answer errors; none was a transport error, a budget
problem or a retrieval problem.

| Requirement | Variant | Cause |
| - | - | - |
| BMS-REQ-CHG-0601 | without | `E_INVALID_JSON`: the model mis-escaped a quote inside a JSON string. The repair returned a **byte-identical** answer. |
| BMS-REQ-UDS-1301 | without | `E_STEP_ORDER` |
| BMS-REQ-EST-0201 | without | `E_STEP_ORDER` |
| BMS-REQ-MECH-1101 | with | `E_STEP_ORDER` |
| BMS-REQ-CHG-0601 | with | `E_STEP_ORDER` |
| BMS-REQ-DIA-0701 | with | `E_STEP_ORDER` |

Five of six failures were caused by one rule of this application, not by the
model: the model numbered steps continuously across preconditions, steps and
postconditions (1,2 | 3,4,5 | 6) while the validator demanded 1..n per list.
The drafts were otherwise valid. Both findings are fixed:

* a draft is renumbered to 1..n per list; only an ambiguous order (repeated or
  descending) is still an error;
* a repair that returns the identical answer stops the unit instead of
  spending another call.

With those fixes the comparison would have shown one failure out of 80, not
six. The figures above are the measurement **as it ran**, with the original
failures kept.

## What is not assessed

Technical usefulness of the drafts and the relevance of the retrieved
passages are **pending**: no tester reviewed these runs, and no human judgement
is invented here. `review-list.md` names the pairs and the questions to decide.

## What follows from this

The retriever finds plausible sections - the DTC master table for a diagnostics
requirement, the CAN-FD message matrix for thermal and charging requirements,
the interface topography for communication. The drafts mostly ignore them.

That points at generation and validation rather than at the search: before
adding embeddings, the context should be made usable - for example by asking
the model explicitly which context it used, and by making an unused context
item visible in the review. An embedding index would improve recall for
differently worded sections, but recall is not what limited this result.
