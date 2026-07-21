# CRT Rule Approval Workflow

Version: 0.1  
Status: Active Workflow  
Purpose: Define how CRT rules move from research into approved ATLAX behavior.

---

## Workflow

```text
Source found
  ->
Capture in CRT_SOURCE_INTAKE.md format
  ->
Extract proposed objective rule
  ->
Check for deterministic inputs
  ->
Create example and non-example
  ->
Project lead review
  ->
Promote to CRT_RULEBOOK.md as APPROVED
  ->
Create test fixture
  ->
Implementation may consume rule
```

---

## Rule Approval Requirements

A CRT rule can be approved only when it has:

- Rule ID.
- Category.
- Source reference.
- Deterministic condition.
- Required inputs.
- Expected output.
- At least one pass example or reason no example is available.
- At least one fail example or reason no non-example is available.
- Edge-case behavior or explicit `UNKNOWN` handling.
- Project lead approval.

---

## Implementation Gate

Developers and AI agents must not implement a CRT rule unless:

- `status: APPROVED`
- The rule is present in `CRT_RULEBOOK.md`
- The expected detector output is defined
- Tests are derived from approved examples

If any requirement is missing, implementation must return `UNKNOWN`.

---

## Example Approved Rule Format

This is a template only.

```yaml
rule_id: CRT-RULE-001
status: APPROVED
category: parent_candle
approved_by: Project Lead
approved_date: YYYY-MM-DD
source_id: CRT-SOURCE-001
source_reference: "Video title at 00:00-00:00"
rule_text: UNKNOWN
deterministic_condition: UNKNOWN
required_inputs:
  - candle.open
  - candle.high
  - candle.low
  - candle.close
expected_output:
  field: UNKNOWN
  value: UNKNOWN
pass_examples:
  - CRT-EXAMPLE-001
fail_examples:
  - CRT-NON-EXAMPLE-001
edge_cases:
  - UNKNOWN
implementation_notes: UNKNOWN
```

---

## Approval States

- `PENDING_SOURCE`: A rule is suspected but not sourced.
- `PENDING_REVIEW`: Source exists but rule is not approved.
- `APPROVED`: Rule can be implemented.
- `REJECTED`: Rule must not be implemented.
- `UNKNOWN`: Behavior is intentionally unresolved.
