# Example output

`TS-BAL-001-example.md` is a complete test specification as Spec2Test writes
it: heading data, status counts with the coverage note, the requirements under
test with their document and version, the test cases with numbered steps and
their expected results, the validation findings, and the sources of the
supporting context that was sent.

## How much of this is retrieval?

Three different things are easy to confuse here.

**The pipeline is retrieval-based.** The index was built over all 22 documents
(203 passages), context was selected per requirement, sent in a separate
section of the prompt, stored with the run and rendered into the document.
Every "Supporting context used" block in the example names the document, its
declared ID, its version, the content hash and the character range - so a
reader can check the quotation against the source.

**The draft wording is not from a language model.** This example was produced
with the rule-based fake model so it can live in the repository without a
model call. That model extracts values and identifiers from the prompt
mechanically. It is a test double, not a writer: judge the structure here, not
the prose.

**The fake model does read the context, and does it badly - on purpose.** With
retrieval switched on it picks up `120 ms`, a value that stands only in the
retrieved section and not in the requirement. The validation catches exactly
that and marks it `C_VALUE_FROM_CONTEXT`, which blocks approval until a
reviewer confirms the attribution. That is the mechanism this example is meant
to demonstrate.

With a real model the structure is identical. Whether the model uses the
context well is a separate question, measured in
[`../benchmarks/`](../benchmarks/README.md): across 80 runs only 26 % of the
retrieved passages were referenced in the resulting drafts at all.

## Status of the content

Every case in the example is a **draft**. Nothing is approved: approval is a
deliberate decision in the review panel, and the export of approved cases is a
separate choice.

## Reproduce

```bash
spec2test run --spec requirements/06_cell_balancing_and_capacity_retention.md \
    --mode fake --retrieval --knowledge requirements \
    --title "Testspezifikation: Zellbalancierung" --spec-id TS-BAL-001
```
