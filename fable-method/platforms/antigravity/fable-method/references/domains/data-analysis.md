# Domain adapter: data analysis

Applies when the deliverable is an answer derived from data: spreadsheets,
exports, logs, metrics, or “which/how many/top N” questions. The loop is
unchanged; these definitions replace coding defaults.

## Minimum evidence set (binding, before any aggregate)

1. **Raw data itself**: header, sample rows, and row count; exports are dirtier
   than their descriptions.
2. **Data-quality pass**: duplicates, mixed formats, negatives/refunds,
   corrections, nulls, and rows outside the requested window.
3. **Exact question boundaries**: period, population, and metric definition;
   “Q2”, “last quarter”, and “April onward” are different filters.

## Evidence and primary sources

The dataset is the primary source; its description is only a claim. When they
disagree, the data wins and the disagreement is surfaced.

## Authority order

The user's stated question and definitions > the data > column names and file
labels > assumptions. A column named “total” never settles metric meaning.

## Verification by observation

- Recompute every number from the data with a reproducible method.
- State data-quality decisions and counts, with sensitivity where a judgment
  could change the answer.
- Cross-check parts against wholes and perform an independent recount.

## Fraud table (for fable-judge)

| Fraud | Symptom |
|---|---|
| Naive aggregation | Duplicates, refunds, or out-of-window rows are silent |
| Silent cleaning | Rows are dropped or merged without count or rationale |
| Cherry-picked windows | A flattering period or filter is chosen silently |
| Phantom precision | Exact figures come from dirty inputs without caveat |
| Unreproducible answers | Numbers have no method or artifact behind them |
| Description trust | The file is analyzed as described, not as observed |

## Done, by example

“Top products for Q2 is done” means ranking, amounts, issues and handling,
sensitivity, and a reproducing method are present. Not: “I summed the amount
column.”
