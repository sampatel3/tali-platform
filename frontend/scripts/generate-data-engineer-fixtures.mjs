#!/usr/bin/env node
import fs from 'node:fs/promises';
import path from 'node:path';

const API_KEY = process.env.ANTHROPIC_API_KEY || '';
const MODEL = process.env.ANTHROPIC_MODEL || 'claude-3-opus-latest';
const ROOT = process.cwd();
const OUTPUT = path.join(ROOT, 'src/features/marketing/demoFixtures/dataEngineerFixtures.generated.json');

const SYSTEM_PROMPT = [
  'You produce deterministic product demo fixture JSON for a recruiting platform.',
  'Return only strict JSON.',
  'Use realistic but fake candidate and incident data.',
  'Scenario: data engineer incident recovery task in production.',
  'Tone: concise, evidence-focused, recruiter-readable.',
].join(' ');

const USER_PROMPT = `
Create JSON with keys:
runtime_preview, candidate_preview.

runtime_preview must include:
task_name, task_context, repo_files (path/content), conversation (role/content), output, claude_prompt, remaining_credit_usd, credit_limit_usd, time_left_seconds.

candidate_preview must include:
name, email, position, task, time, completedDate, status, results[], promptsList[], timeline[], breakdown, _raw.

_raw should include realistic score_breakdown and cv_job_match_details.
Use TAALI scoring dimensions:
task_completion, prompt_clarity, context_provision, independence_efficiency, response_utilization, debugging_design, written_communication, role_fit.
`;

async function run() {
  if (!API_KEY) {
    console.error('Missing ANTHROPIC_API_KEY. Export a key and rerun.');
    process.exit(1);
  }

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'x-api-key': API_KEY,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: 4000,
      temperature: 0.2,
      system: SYSTEM_PROMPT,
      messages: [{ role: 'user', content: USER_PROMPT }],
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Anthropic API failed (${response.status}): ${text}`);
  }

  const payload = await response.json();
  const contentText = payload?.content?.find?.((item) => item?.type === 'text')?.text || '';
  let parsed;
  try {
    parsed = JSON.parse(contentText);
  } catch {
    const jsonStart = contentText.indexOf('{');
    const jsonEnd = contentText.lastIndexOf('}');
    if (jsonStart < 0 || jsonEnd < 0 || jsonEnd <= jsonStart) {
      throw new Error('Model output did not contain parseable JSON.');
    }
    parsed = JSON.parse(contentText.slice(jsonStart, jsonEnd + 1));
  }

  const finalPayload = {
    generated_at: new Date().toISOString(),
    model: MODEL,
    scenario: 'data_engineer_revenue_recovery_incident',
    ...parsed,
  };

  await fs.writeFile(OUTPUT, `${JSON.stringify(finalPayload, null, 2)}\n`, 'utf8');
  console.log(`Generated fixture file: ${OUTPUT}`);
}

run().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
