# Terminal states

| State | Meaning | Candidate changes allowed? | Resume allowed? |
|---|---|---:|---:|
| `SUCCESS` | Frozen success criteria passed on a valid final test. | No | No |
| `VALID_TEST_FAILED` | The final test completed validly but criteria failed. | No | No |
| `INCONCLUSIVE` | Protocol-valid data cannot support the preregistered inference. | No | No |
| `INFRA_INCOMPLETE` | Final-test work was interrupted by infrastructure. | No | Yes, only the missing work under the identical profile |
| `PROTOCOL_INVALID` | A required protocol, isolation, integrity, or reproducibility gate failed. | No | Only after starting a new run/profile |
| `BUDGET_EXHAUSTED` | No legal reservation remains outside protected closeout capacity. | No new exploration | Only as a new authorized run |
| `BLOCKED` | Required external authority or input is unavailable. | No | Yes, after the external condition changes |

`VALID_TEST_FAILED` is never routed back into the dev DAG. Treating a valid
failure as an infrastructure retry would be a protocol violation.
