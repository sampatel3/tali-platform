# Eval fixtures — consent and PII rules

Real CVs and JDs of Taali / DeepLight team members are used as ground
truth for the CV match eval harness. Treat them with care.

## Rules

1. **Consent first.** Add a fixture only after the person has explicitly
   approved their CV being used for internal regression testing.
2. **Anonymize before commit.** Strip:
   - real names (replace with placeholder, e.g. "Senior Engineer A")
   - email addresses, phone numbers
   - personal URLs (LinkedIn, GitHub, personal site)
   - exact dates of birth, addresses
   - photos
   Company names and role titles can stay — they're how the LLM matches.
3. **No PII in commit messages or PR descriptions.**
4. **Mapping kept outside the repo.** The `case_id ↔ person` mapping
   lives in a private 1Password note (Taali Engineering vault, item
   "CV match eval fixtures"). Do not put it in this repo, even encrypted.
5. **Revoke on request.** If a team member asks for their fixture to be
   removed, delete it the same day and force-update any baseline
   snapshots that referenced it.

## How to add a fixture

1. Get explicit consent (Slack DM is fine — screenshot for the 1Password
   note).
2. Anonymize per the rules above.
3. Add the CV file to `cvs/` and the JD they were hired against to
   `jds/` (one JD can be reused across multiple cases).
4. Append a case to `golden_cases.yaml` with:
   - a non-PII `case_id` like `taali_eng_004` or `deeplight_pm_002`
   - tight `must_meet_requirements` (the things you'd actually want
     flagged if a regression dropped them)
   - wide `role_fit_score_range` and `recommendation_in` set — the goal
     is regression detection, not score-pinning.
   - hiring manager notes for context (no PII).
5. Update the 1Password mapping note.

## Why three checks per case

The harness asserts on three things, in priority order:

1. **recommendation in expected set** — coarse-grained: would we have
   actually advanced this person?
2. **role_fit_score in range** — medium-grained: are we in the right
   tier (strong yes vs lean no)?
3. **must_meet_requirements have status=met** — fine-grained: did the
   model recognize the specific signals that mattered?

A regression on (3) but not (1) or (2) is a soft signal. A regression
on (1) is a hard fail.

## Synthetic placeholder

`placeholder_eng.txt` and `placeholder_eng.txt` (CV and JD) ship in this
directory so the harness runs end-to-end before any real fixtures land.
Keep the placeholder permanently — it's the smoke test. Sam will add the
real cases separately.
