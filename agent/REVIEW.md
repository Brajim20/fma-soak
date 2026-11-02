# FMA soak triage: bug review

The triage pass called one app a Fleet bug and drafted the issue. Your job is to **try to prove it wrong** before
Brayan, the QA engineer, sees it. A wrong bug costs QA credibility with engineering. Read `PROMPT.md` (same folder as
this file) for the inputs, hosts, known patterns, sandbox limits and draft format; the same rules apply to you.

In the app's folder you were given: `decision.json`, `notes.md`, the `BUG_DRAFT_*.md`. Its records are in
`<output>/records/` (see `<output>/queue.json` for which).

1. **Check every claim in the draft against the records and the catalog** (`git -C /Users/brayan/Documents/GitHub/fleet
   show origin/main:<path>`): the values, the manifest paths, the queries, the issue numbers.
2. **Look for the innocent explanation**: environment (ARM, the 4 GB VM, a hung install ahead in the queue, a host
   restart), the harness (a check reading the wrong thing; see the S6 meanings in PROMPT.md), intended behavior, or an
   existing issue (search GitHub by app name and by mechanism; check INDEX.md and triage.json).
3. **Decide:**
   - It holds: tighten the draft (short, plain, Fleet's template, no speculation, no secrets) and leave
     `decision.json` as `bug`. Add `"reviewed": "holds: <one line why>"` to it.
   - It doesn't: delete nothing, but rewrite `decision.json` with the right decision, its `triage_entry`, and
     `"reviewed": "overturned: <one line why>"`; rename the draft to `REJECTED_<name>.md`.
   - You can't tell: set the decision to `needs-you`, put the one check that settles it in `notes.md`, and add
     `"reviewed": "unsettled: <what's missing>"`.

Write `decision.json` last.
