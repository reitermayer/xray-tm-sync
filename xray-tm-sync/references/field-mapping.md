# Field mapping: Xray test → TM test case

| TM field | Source | Notes |
|---|---|---|
| Name | Jira `summary` | Truncated to 250 characters; falls back to the Xray key |
| Description | Jira `description` → plain text, plus Gherkin / generic definition, plus origin footer | ADF (Jira Cloud) is flattened to text with `-`/`1.` lists; wiki markup passes through unchanged. Footer: `— Imported from Xray test CALC-123 (<jira>/browse/CALC-123)` |
| Precondition | Xray preconditions | `[CALC-9] <summary>` + newline + definition, joined by blank lines. Only the first 10 are read; a warning names the rest |
| Steps | Xray steps (Manual only) | action → description (`(no action)` if empty), data → clipboard data, expected result → expected result. Called Xray tests are inlined (`getExpandedTests`) |
| Labels | Derived | `XR_Imported`, `XR_Type_<Manual\|Cucumber\|Generic>`, `XR_Folder_<repository path>` (not for `/`), `XR_Label_<jira label>`, `XR_Set_<xray test set key>`; spaces → `_` |
| Custom field `Xray Key` (Text) | Xray key | Origin marker, written right after the TM test case is created |
| Custom field `Xray Intake` (Text) | JSON | `{"xrayKey","lastModified","xrayHash","tmHash","intakeAt"}`; written last |

Cucumber and Generic Xray tests get no TM steps; their definition goes into the description.

**TM-owned steps.** When an Xray test has no Xray steps (description-only tests, Cucumber, Generic),
the TM steps belong to TM — for example generated from the description with GenAI in Test Manager.
Intake never adds, changes or deletes them, and they are excluded from `tmHash`, so generating them is
not drift. The intake record stores `stepsManaged: false`. Once the Xray test gets Xray steps, Xray owns
the TM steps again and the next update replaces them.

## Re-sync rules

| Xray changed? | TM edited since last intake? | Result |
|---|---|---|
| no | — | `unchanged` |
| yes | no | `update` — name, description, precondition, steps (replaced only if different), `XR_*` labels reconciled |
| yes | yes | `drift` — skipped unless the user passes `--accept-drift <XRAY_KEY>` |
| marker present, intake record empty | — | `update` (repairs a partial earlier intake) |

`tmHash` covers name, description and steps as read back from TM. The TM precondition is not part of it
because `uip tm testcases list` does not return it. Non-`XR_` labels are never added or removed.

## TM behaviour this relies on (verified 17.10.2026, uip 1.203)

- `testcases create` accepts only name, description, version → precondition, steps, labels, custom
  fields are separate calls (about 7–9 `uip` calls per new TM test case).
- TM seeds an empty value row for every custom field definition when a TM test case is created;
  `customfield value create` on such a row returns Success **without writing**. The script therefore
  uses `value update`, falls back to `create`, and reads the value back.
- `customfield value list` returns nothing without `--object-id`; there is no project-wide lookup by
  value. The script reads markers per TM test case once and caches them in `index-<TM project>.json`.
- `testcases update --pre-condition ""` clears the TM precondition.
- Labels may contain `/`, `-` and non-ASCII characters.
- TM adds its own system label `manual` to new TM test cases; it is left alone.
