## FMA soak — October 2026 update

**Environment:** qa.fleetdm.com, fleet 65 · builds: 4.94.0-rc.2610272345 (937e87458c)

**1. Runs** — 262 cycles: pass 163, findings 68, flaky 4, environment 2, skipped 25

**2. Coverage** — darwin 37/984 (3.8%), round 1; windows 51/459 (11.1%), round 1

**3. Escapes caught or confirmed**

| Issue | State | Title |
|---|---|---|
| #47037 | unknown |  |
| #50077 | unknown |  |
| #55360 | unknown |  |
| #56589 | unknown |  |
| #56873 | unknown |  |
| #56891 | unknown |  |
| #56948 | unknown |  |
| #56971 | unknown |  |
| #57008 | unknown |  |

**4. Build changes**

No build change this month.

**5. Plan changes**

TODO: what changed in the plan this month.

**6. Close-out criteria**

1. Every failure shape (S1–S5) has an automated check whose teeth were shown on a pre-fix build or script — see the coverage plan's escape matrix
2. The whole catalog cycled at least once per platform — darwin: in progress, windows: in progress
3. One real upstream transition followed end to end (L3) — yes
4. The harness runs on GitHub Actions for a full week — not yet
5. Every finding in the month's digests is adjudicated (filed, linked, or triaged) — check the digests
