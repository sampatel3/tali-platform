# Closed assessment workspace

## Product promise

Candidates may seek general direction from another person or AI, but the protected task repository and workspace documents remain operable only through the Tali assessment IDE. A valid result requires substantive work in the declared deliverable, not answers to questions or checkbox activity.

This is a containment and evidence boundary, not a claim that a web page can control the candidate's whole computer.

## Enforced boundary

| Layer | Enforcement |
| --- | --- |
| Workspace | A dedicated E2B sandbox is created with secure mode and internet access disabled. Production fails closed if the SDK or configuration cannot establish that mode. |
| Repository | The frozen task snapshot is written into a local Git worktree with no remote, credentials, hooks, or candidate-specific GitHub branch. |
| Dependencies | Published tasks must use an offline bootstrap and the baked sandbox image. Network installers, package managers, virtual-environment creation, and unbaked dependencies are rejected at publication. |
| Browser session | Start binds the assessment to a non-exportable browser P-256 key. Live requests require a short-lived signed proof over the method, exact request target, body digest, timestamp, and one-time nonce. Replays and copied session credentials are rejected. A recruiter can rotate the browser binding without replacing the workspace or resetting the timer. |
| Repository API | Candidate reads and writes are single-file, size-bounded, path-normalized, revision-checked, session-bound operations. The editor autosaves after a short idle period; stale browser or Claude writes conflict instead of overwriting newer work. Protected verifier files and bulk repository export are unavailable. |
| Submission | Submit atomically leases the runtime and freezes a bounded, immutable artifact with a server-computed SHA-256 digest and capture time. The candidate receives that durable receipt immediately; grading continues asynchronously. |
| Work requirement | The declared primary artifact must exist, contain content, and differ from the frozen starter version. Chat activity or unrelated file edits cannot satisfy the gate. |
| Verification | Server-owned tests and configuration are restored into a fresh network-disabled sandbox. The repository is locked read-only for an unprivileged grader, which runs through an isolated interpreter and a root-owned completion wrapper. |
| Scoring | No headline score is published when substantive work is absent, the verifier is untrustworthy, or a required rubric is incomplete. Candidate content is untrusted evidence and cannot override grading instructions. |
| Evidence | Task, CV, submission, timing, and session state are frozen durably. Focus, clipboard, paste, print, and related browser events are advisory signals, not automatic guilt. |

## Clipboard and screen capture

The assessment UI blocks ordinary external copy, paste, cut, drag/drop, context-menu, and print paths while preserving an internal IDE clipboard. It also displays a candidate-specific watermark and records relevant browser events.

A recruiter may enable the explicit pre-start external-clipboard accommodation for a candidate. This is a single persisted capability, not a general client-controlled policy override.

These controls raise the cost of exfiltration and improve review evidence. They cannot reliably stop:

- operating-system screenshots or screen recording;
- a phone camera;
- manual transcription or retyping;
- browser extensions, accessibility tooling, or a compromised device;
- a candidate using DevTools in the authorized browser as a signing oracle.

The defensible claim is therefore: **copying an invite URL, session credential, or request into another browser or command-line client is insufficient to operate the protected workspace.** It is not defensible to claim that screenshots or all outside assistance are technically impossible.

## Task design contract

Every publishable assessment must:

1. declare one primary work artifact;
2. spend at least 85% of its expected effort on producing or changing workspace content, with at most 15% on question-and-answer activity;
3. include an offline bootstrap and a fixed expected verifier test count;
4. run its authoritative verifier through isolated `python3 -I -m pytest` without network or candidate-owned bootstrap code;
5. avoid secrets, personal data, and proprietary material that would be catastrophic if photographed or manually transcribed.

Assessment design remains part of the security model. Prefer bespoke, multi-step tasks that require reading local evidence, making tradeoffs, editing the deliverable, and validating the result. A one-shot question whose answer can be pasted from an external model is not a valid assessment.

## Production release checklist

1. Apply every Alembic migration before serving the new frontend.
2. Build and verify the E2B template from `e2b.Dockerfile`; set both `E2B_API_KEY` and `E2B_TEMPLATE`.
3. Keep `LIVE_ASSESSMENT_DEMO_ENABLED=false` in production.
4. Configure `FRONTEND_URL` and any `CORS_EXTRA_ORIGINS` as exact trusted origins. Do not use preview-domain wildcards.
5. Deploy frontend and backend together so proof-bound requests and verification headers agree.
6. Run a real candidate smoke test: start, refresh recovery, read/write, Claude work, execute, submit, immutable-artifact verification, and score publication.
7. Confirm the sandbox has no internet route, Git remote, credential material, or successful package download.

## Operating guidance

Treat clipboard, focus, and timing telemetry as context for human review, never a standalone cheating verdict. If identity assurance is required, add an explicitly disclosed live identity check or follow-up defense interview. For high-stakes roles, ask the candidate to explain one or two decisions from the captured artifact and process trace; this tests ownership without pretending browser proctoring is infallible.
