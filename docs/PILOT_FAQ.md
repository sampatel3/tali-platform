# Pilot FAQ — getting started with Taali

Your one-page guide to the platform. Bookmark this; it covers everything you need
for the first 30 days.

## Accounts and access

**Q: How do I sign up?**
Go to [taali.ai/register](https://www.taali.ai/register). One email + password,
and you'll get a fresh workspace seeded with free credits. We'll send a
verification email — click the link and you're in.

**Q: How do I add my team?**
Settings → Members → **+ Invite teammate**. They get a sign-in link by email.
Roles: Owner / Admin / Recruiter / Hiring manager.

**Q: I forgot my password.**
[taali.ai/forgot-password](https://www.taali.ai/forgot-password) — reset link
arrives in a minute. If it doesn't, check spam, then ping us.

## Agent mode (the autonomous bit)

**Q: What does agent mode do?**
For each role you turn it on, Taali continuously: scores incoming CVs, invites
the strongest matches to your assessment, watches the assessment session, and
queues "advance" or "reject" recommendations for you to approve. You stay in
charge of every consequential decision — interviews and final hire stay
human-only.

**Q: How do I turn it on?**
Open a role → in the top-right panel of the role hero, set a monthly cap (e.g.
$50) and click **Turn on agent**. That's it. The agent activates and starts
working the role within ~30 seconds.

**Q: Per-role budgets — why?**
Every role has its own cap. Spend across scoring, pre-screen, assessment grading,
and agent decisions all draws against that one number. When the cap is reached
the agent auto-pauses for that role; raise the cap or click Resume in the role
hero to continue. Other roles keep going.

**Q: How do I pause the agent?**
Same panel — click **Pause**. Toggles `agent mode = off` for that role. Click
**Turn on** again to resume.

**Q: Where do I see what the agent did?**
Reporting tab — narrative-first summary of the last 30 days, with drill-downs
into individual decisions, anomalies, and budget burn.

## Candidates and roles

**Q: How do I create a role?**
Jobs → **+ New role** → paste the job spec and pick a task. Done.

**Q: How do I get candidates in?**
Three options: (a) Workable sync if your account is connected — candidates
flow in automatically; (b) Manual invite — Role detail → **Invite candidate**;
(c) Public application link — share the role's public URL.

**Q: Where do I see a candidate's full profile?**
Click the candidate's name from any list. The standing report shows the score
ring, recommendation, signal breakdown, AI fluency radar, CV match, and
interview prep notes — all evidence-linked back to the assessment session.

**Q: How do I share a candidate with a hiring manager?**
On the candidate page, **Share internally** for panel members (full report) or
**Share with client** for external stakeholders (client-safe summary, no
recruiter notes). Both produce expiring links — no PDFs, no leaks.

## Assessments

**Q: What does the candidate experience look like?**
A real IDE in the browser (Monaco editor + sandboxed runtime + Claude
pair-programming). They get the task brief, work the problem, and submit.
Every prompt, paste, and edit is captured for the report.

**Q: Can I preview a task before sending it to a candidate?**
Tasks → click any task → **Preview as candidate**. You see exactly what they
see.

**Q: Do you support custom tasks?**
Yes — Tasks → **Request bespoke task**. Our engineers build it (3-5 working
days), you approve the draft, then it's in your library forever.

## Billing

**Q: How does pricing work?**
Pay-as-you-go credits. Each scoring/assessment/agent decision draws from your
credit balance. New workspaces get free credits to start; top up from
Settings → Billing when you're ready.

**Q: How do I monitor spend?**
Reporting page shows monthly burn at a glance. Each role shows its own monthly
cap + spend in the role hero. Settings → Usage shows the per-event ledger.

## Support

**Q: Something's broken / I have a question.**
Email us — your account manager has the address. Include the role ID, candidate
ID, or screenshot if it helps. We typically respond within a few hours during
the pilot.

**Q: What if I want to delete my data?**
Settings → Organization → **Delete workspace** removes everything. Or email us
and we'll do it for you — usually within 24h.

**Q: Do you train models on our data?**
No. Anthropic doesn't train on Claude API traffic, and we don't aggregate
candidate sessions for any external use. Your data stays yours.
