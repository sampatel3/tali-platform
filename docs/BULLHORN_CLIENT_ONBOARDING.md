# Bullhorn Client Onboarding Pack

What a Bullhorn-based client must do before Taali can connect. Send §1 as the email;
§2–§5 are its attachments/inserts. Runtime flow this maps to: `POST /api/v1/bullhorn/connect`
(discovery → automated OAuth → entitlement pre-flight → status-list fetch → stage-map seed).

Facts this pack encodes (research 2026-07-02, memory `bullhorn_integration_assessment`):
credentials are requested BY THE CLIENT via a Bullhorn support ticket (Fair Use Policy
permits single-customer vendor use); refresh-token capability is granted conditionally, so
it is requested explicitly; API calls count against the client's monthly quota (default 100k).

---

## §1 Email to the client (copy, adjust names)

Subject: Bullhorn access for the Taali integration — three small setup steps

Hi <name>,

Great news — the Taali↔Bullhorn integration is built and tested on our side. To connect it
to your Bullhorn we need three things, all quick, and all fully under your control:

1. **File one support ticket with Bullhorn** (via the Bullhorn Resource Center in your
   account) using the exact text in the attached "ticket text" — it asks Bullhorn to issue
   API credentials for our integration on your account. This is Bullhorn's standard route
   for third-party integrations and usually turns around in a few business days.

2. **Have your Bullhorn admin set up the API user** per the attached checklist (a dedicated
   user Taali acts as, with the listed permissions — nothing broader).

3. **Create three test records** (one job, two candidates — details attached) clearly named
   "TAALI TEST". We validate everything against those records only, before touching any real
   workflow, and you can delete them afterwards.

When the ticket comes back, Bullhorn will deliver a Client ID and Client Secret in the case
thread. Please send those plus the API user's username/password to us via <secure channel —
1Password link / one-time secret>, not plain email.

One question for your admin meanwhile: what is your instance's monthly REST API call limit
and roughly how much of it is currently used? (Default is 100k/month; our sync is designed
to stay well inside it, we just want to confirm headroom.)

Thanks — once we have the credentials we'll run a read-only validation first, share the
results with you, and only then enable anything that writes.

---

## §2 Ticket text (client pastes into a Bullhorn Resource Center support case)

> Subject: REST API credentials for a third-party integration (Taali)
>
> We would like to request OAuth API credentials (Client ID and Client Secret) associated
> with our company account, for an integration with Taali (taali.ai), an AI candidate
> screening and assessment platform performing services solely for our benefit.
>
> Please:
> 1. Issue a REST API Client ID and Client Secret for this integration.
> 2. Register the redirect URI: https://www.taali.ai/integrations/bullhorn/callback
> 3. Enable refresh-token capability (offline access) for this API client, so the
>    integration can maintain access without storing our user password.
> 4. Confirm REST event-subscription API access (/event/subscription) is enabled for it.
> 5. Associate it with the dedicated API user we have created: <API_USERNAME>.
>
> Delivery of the Client ID/Secret through this case thread is fine.

(If support asks which product: classic Bullhorn ATS, REST API — not Recruitment Cloud.)

## §3 Bullhorn admin checklist — the API user

Create a dedicated user (suggested name: "Taali Integration") the integration will act as.
Notes and status changes made by Taali will show under this user's name in Bullhorn.

Permissions (entitlements) it needs — read unless stated:
- Candidate (incl. file attachments read)
- JobOrder
- JobSubmission — read + **edit/status update**
- JobSubmissionHistory (read, via query)
- Note — **create**
- ClientCorporation (read — job orders reference it)
- Settings read (job submission status list)

Row-level visibility: the user must be able to SEE the jobs/candidates in scope for Taali
(corporate-wide read is simplest; department-scoped works if all in-scope jobs are covered).
Private records stay invisible to Taali — that is fine and expected.

## §4 Test records (client creates, ~10 minutes)

All names prefixed **"TAALI TEST — DO NOT USE"** so nobody mistakes them for real records:
1. One JobOrder (any dummy client corporation is fine).
2. Two Candidates, each with a CV file attached (any PDF/DOCX — we test file retrieval
   and parsing with it).
3. JobSubmissions linking both candidates to the test job — one left in your default new
   status; one the client is happy for Taali to MOVE and REJECT during validation (those
   two writes are the only write tests we run, and only on these records).

## §5 Credential hand-back + what happens next

We need, via a secure channel (not plain email): Client ID, Client Secret, API user
username, API user password (used once during connect to establish tokens; Taali stores
only encrypted tokens — the password is never persisted).

Our side after receipt (client sees/approves each step):
1. Read-only: connect + entitlement pre-flight + full sync into an isolated staging org;
   verify field mapping, status list, CV retrieval, API-call consumption vs their quota.
2. Write validation on the §4 test records only — move, reject, note — each explicitly
   approved before it runs.
3. 3–5 day shadow run (reads live, writes queued-and-held, diffed against what their
   recruiters actually did) → sign-off → production enablement for their org.
