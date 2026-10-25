# Week 2 feedback carried into Week 3

The copied `data/week2-evaluation.json` contains 128 recorded development responses across four retrieval configurations. Its scores describe the old retrieval experiment, not the new application.

Direct inspection of the lowest-scoring examples found that removing a claim did not remove the same unsupported assertion from other response sections:

| Recorded example | Score | Observed issue | Week 3 treatment |
|---|---:|---|---|
| CASE-1001, requested amount, semantic | 0.0 | Claims were empty but the suggested action repeated a specific $1,099 amount. | Amounts in actions come from validated reconciliation; every generated action is included in whole-output review. |
| CASE-1001, requested amount, semantic + reranker | 0.0 | The next step repeated $1,099 and shifted from requested amount to processing/posting. | Separate the requested, approved, submitted and posted states; verify relevance as well as source membership. |
| CASE-1002, approved amount, semantic + reranker | 0.0 | Claims were removed while the next action asserted an $899 approval. | Review actions against original approval evidence, not another agent's conclusion. |
| CASE-1008, captured payment, semantic | 0.2 | An unknown referred to a specific $1,299 debit after the generated claim was removed. | Unknowns are reviewed as factual language; a qualifier does not exempt the embedded assertion. |
| CASE-1008, captured payment, semantic + reranker | 0.2 | Unknowns and follow-up recommendations shifted to payment instrument and settlement details. | Check that uncertainty and actions are justified by the task and source scope. |

These are qualitative observations of recorded low-score rows, not newly calculated faithfulness scores. Exact retrieved chunk text is not present in every stored evaluation row, so this review does not claim an exact replay of the old judge context.

The new independent deterministic cases include a $500 approval with a $200 partial refund, contradictory statement/bank values, provisional credit without a merchant refund, and a matched $149 refund. Tests also inject mismatched card identity, tool failure and stale evidence. They check calculations and workflow behavior independently from explanation quality.

Model mode adds scoped follow-up tool selection, structured claim references and independent whole-output model review, with one bounded repair attempt. The model reviewer can still be wrong; fresh provider evaluation and human assessment remain necessary. Until credentials are configured, model generation, faithfulness and live API behavior remain unverified.
