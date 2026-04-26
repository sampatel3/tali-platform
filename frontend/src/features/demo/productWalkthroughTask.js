export const AI_GENAI_PRODUCTION_READINESS_TASK = {
  name: 'GenAI Production Readiness Review',
  task_id: 'ai_eng_genai_production_readiness',
  duration_minutes: 30,
  scenario: `You joined Deeplight as an AI Engineer embedded in a UAE-licensed bank's Customer Intelligence team. You have inherited a GenAI feature that summarises support tickets and recommends churn-risk actions for relationship managers.

**Message from the product director:**

> We have a working prototype that demos well, but three functions are blocked one week before an executive preview with the bank's Chief Risk Officer.
>
> Legal has flagged that our recommendations could constitute financial advice under UAE CBUAE regulations if shown to customers without a human review gate - and the code currently shows high-stakes actions immediately regardless of confidence.
>
> Security found that customer names, emails, and phone numbers from ticket bodies are flowing directly into LLM prompts. Under our data residency agreement, PII must be redacted before leaving our processing boundary.
>
> Our prompts are pulling from raw ticket text with no grounded retrieval - the support team does not trust recommendations that cannot be traced to specific customer evidence. Legal specifically asked for citations.
>
> Finance is alarmed because every page refresh calls the model again, and retry storms during the last batch doubled our spend.
>
> Finally, there is no audit trail. The compliance team needs to know which insights were shown to which relationship managers, what confidence the model had, and whether human review was triggered - none of this is logged.
>
> The previous engineer left behind a partially wired review flow, a cache, a safety module, and a lot of TODOs. We need your judgment more than heroics.
>
> In 30 minutes I need to know:
> 1. What should block launch today?
> 2. What is the smallest credible slice we can ship to relationship managers next week?
> 3. What production fixes can you land right now?

**Your mission:**
Read the repo carefully, identify the highest-risk failure modes, and implement the most important production-readiness improvements you can in the time available. You are being assessed on how you balance regulatory safety, grounded AI behaviour, audit requirements, and delivery pressure in a banking context.`,
  repo_structure: {
    files: {
      'README.md': `# Customer Intelligence GenAI

Prototype service for a UAE-licensed bank that summarises support tickets and recommends next actions for relationship managers.

## Current launch pressure
- Executive preview with the Chief Risk Officer in 7 days
- Legal review blocked - regulatory risk under CBUAE guidelines
- PII leaking into LLM prompts - data residency violation
- Recommendations not grounded in retrieved customer evidence - no traceability
- No compliance audit trail - logging is a stub
- Spend spikes during retries

## Local workflow
1. Create a virtualenv
2. Install \`requirements.txt\`
3. Run \`pytest -q --tb=short\`

The local tests do not require OpenAI credentials or outbound network access.

The codebase is intentionally incomplete. Focus on production-safe improvements, not cosmetic refactors.
`,
      'RISKS.md': `# Known Risks

## Regulatory & Safety
- Ticket bodies contain names, emails, and phone numbers when prompts are built - PII redaction is disabled by default
- High-stakes churn recommendations are shown immediately even when confidence is low or evidence is weak
- No human review gate is wired for regulated financial advice scenarios
- There is no degraded-mode path when the model call fails

## Grounding & Traceability
- Recommendations are built from raw ticket text without a retrieval step
- The support and compliance teams require each recommendation to be traceable to specific retrieved customer evidence
- intelligence/retriever.py always returns an empty context - the retrieval integration is a stub
- The CHURN_PROMPT does not include a {retrieved_context} slot yet

## Compliance & Audit
- intelligence/observability.py is a stub - audit events are never stored
- The compliance team cannot determine which insights were shown, at what confidence, or whether review was triggered
- High-risk events must be surfaced separately for the compliance review queue

## Reliability & Cost
- Cache plumbing exists but sentiment analysis never uses it
- Prompt JSON parsing assumes well-formed output every time
- Every refresh calls the model - no duplicate-run protection
- LLM failures propagate as unhandled exceptions rather than triggering degraded mode

## Stakeholder expectations
- Legal: human review gate required for any recommendation that could constitute financial advice
- Compliance: full audit trail for every insight shown to a relationship manager
- Support leads: recommendations must cite specific retrieved evidence, not raw ticket text
- Finance: no duplicate model calls for the same customer within the cache TTL
`,
      'docs/launch_checklist.md': `# Launch Checklist

A credible launch for this feature in a regulated banking environment needs:
- PII redaction enabled by default before any prompt is constructed
- Grounded retrieval context included in every churn recommendation prompt
- Human review gate enforced for high-stakes actions
- Compliance audit log created for every insight surfaced to a relationship manager
- Degraded-mode response when the model is unavailable
- Cache reuse to avoid duplicate spend within the TTL window
- Clear documentation of what remains blocked for the next release
`,
      'intelligence/config.py': `from dataclasses import dataclass


@dataclass
class Config:
    default_model: str = 'gpt-4o-mini'
    require_human_review: bool = False       # BUG: should default True for regulated use
    pii_redaction_enabled: bool = False      # BUG: should default True
    cache_ttl_seconds: int = 900
    degraded_mode_summary: str = 'Model unavailable - manual review required.'
`,
      'intelligence/review.py': `from __future__ import annotations

from typing import Dict


HIGH_RISK_TYPES = {'churn_risk'}


def should_require_human_review(*, insight_type: str, confidence: float, has_action: bool) -> bool:
    if insight_type not in HIGH_RISK_TYPES:
        return False
    # BUG: high confidence (>=0.85) suppresses review - but in a regulated context
    # ANY churn_risk action with a recommended action requires review regardless of confidence
    if confidence >= 0.85:
        return False
    return has_action


def degraded_payload(summary: str) -> Dict[str, object]:
    return {
        'summary': summary,
        'confidence': 0.0,
        'evidence': ['model unavailable'],
        'action': None,
    }
`,
      'intelligence/analyzer.py': `from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from intelligence.cache import InsightCache
from intelligence.config import Config
from intelligence.observability import ObservabilityLogger
from intelligence.prompts import CHURN_PROMPT, SENTIMENT_PROMPT
from intelligence.retriever import CustomerContextRetriever
from intelligence.review import degraded_payload, should_require_human_review
from intelligence.safety import redact_pii


@dataclass
class Insight:
    insight_type: str
    customer_id: str
    summary: str
    confidence: float
    supporting_evidence: List[str]
    recommended_action: Optional[str]
    requires_review: bool = False
    degraded_mode: bool = False


class CustomerAnalyzer:
    def __init__(
        self,
        config: Config,
        llm_client: Any,
        db_client: Any,
        cache: Optional[InsightCache] = None,
        retriever: Optional[CustomerContextRetriever] = None,
        observability: Optional[ObservabilityLogger] = None,
    ):
        self.config = config
        self.llm = llm_client
        self.db = db_client
        self.cache = cache or InsightCache(ttl_seconds=config.cache_ttl_seconds)
        self.retriever = retriever or CustomerContextRetriever(db_client)
        self.observability = observability or ObservabilityLogger()

    def analyze_customer(self, customer_id: str) -> List[Insight]:
        customer = self._get_customer_data(customer_id)
        tickets = self._get_recent_tickets(customer_id)
        insights = [
            self._analyze_churn_risk(customer, tickets),
            self._analyze_sentiment(customer, tickets),
        ]
        return insights

    def _analyze_churn_risk(self, customer: Dict[str, Any], tickets: List[Dict[str, Any]]) -> Insight:
        cache_key = f"churn:{customer['id']}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return Insight(**cached)

        docs = self.retriever.retrieve_relevant_context(
            customer_id=customer['id'],
            query='churn risk renewal escalation',
        )
        retrieved_context = self.retriever.format_context_for_prompt(docs)

        prompt = CHURN_PROMPT.format(
            customer_name=customer['name'],
            arr=customer['arr'],
            retrieved_context=retrieved_context,
            ticket_summary=self._prepare_ticket_context(tickets),
        )
        response = self.llm.complete(prompt)
        parsed = json.loads(response.content)
        insight = Insight(
            insight_type='churn_risk',
            customer_id=customer['id'],
            summary=parsed.get('summary', ''),
            confidence=float(parsed.get('confidence', 0.0)),
            supporting_evidence=list(parsed.get('evidence', [])),
            recommended_action=parsed.get('action'),
            requires_review=should_require_human_review(
                insight_type='churn_risk',
                confidence=float(parsed.get('confidence', 0.0)),
                has_action=bool(parsed.get('action'))
            ),
        )
        self.observability.log_insight(insight, model=self.config.default_model)
        self.cache.set(cache_key, insight.__dict__)
        return insight

    def _analyze_sentiment(self, customer: Dict[str, Any], tickets: List[Dict[str, Any]]) -> Insight:
        prompt = SENTIMENT_PROMPT.format(
            customer_name=customer['name'],
            ticket_summary=self._prepare_ticket_context(tickets),
        )
        response = self.llm.complete(prompt)
        parsed = json.loads(response.content)
        return Insight(
            insight_type='sentiment',
            customer_id=customer['id'],
            summary=parsed.get('summary', ''),
            confidence=float(parsed.get('confidence', 0.0)),
            supporting_evidence=list(parsed.get('evidence', [])),
            recommended_action=None,
        )

    def _prepare_ticket_context(self, tickets: List[Dict[str, Any]]) -> str:
        lines = []
        for ticket in tickets[:8]:
          content = str(ticket.get('content', ''))
          if self.config.pii_redaction_enabled:
              content, _ = redact_pii(content)
          lines.append(f"[{ticket.get('created_at', 'unknown')}] {ticket.get('subject', '')}: {content}")
        return '\\n'.join(lines)

    def _fallback_insight(self, customer_id: str) -> Insight:
        payload = degraded_payload(self.config.degraded_mode_summary)
        return Insight(
            insight_type='churn_risk',
            customer_id=customer_id,
            summary=str(payload['summary']),
            confidence=float(payload['confidence']),
            supporting_evidence=list(payload['evidence']),
            recommended_action=None,
            requires_review=True,
            degraded_mode=True,
        )

    def _get_customer_data(self, customer_id: str) -> Dict[str, Any]:
        return self.db.query_customer(customer_id)

    def _get_recent_tickets(self, customer_id: str) -> List[Dict[str, Any]]:
        return self.db.query_tickets(customer_id)
`,
      'tests/test_analyzer.py': `import json
from unittest.mock import Mock

import pytest

from intelligence.analyzer import CustomerAnalyzer
from intelligence.cache import InsightCache
from intelligence.config import Config
from intelligence.observability import ObservabilityLogger
from intelligence.retriever import CustomerContextRetriever


@pytest.fixture
def mock_db():
    db = Mock()
    db.query_customer.return_value = {'id': 'cust-1', 'name': 'Acme Corp', 'arr': 120000}
    db.query_tickets.return_value = [
        {
            'created_at': '2026-02-10',
            'subject': 'Escalation before renewal',
            'content': 'Please contact jane@acme.com or 555-123-4567 before renewal.',
        }
    ]
    db.query_context_docs.return_value = [
        {'source': 'contract_note_2026-01', 'content': 'Renewal at risk - CSM flagged pricing concerns.'},
        {'source': 'renewal_call_2025-11', 'content': 'Customer considering competitor offer.'},
    ]
    return db


def test_redacts_ticket_pii_before_prompt(mock_db):
    llm = Mock()
    llm.complete.return_value = Mock(content=json.dumps({
        'summary': 'risk', 'confidence': 0.7, 'evidence': [], 'action': 'call'
    }))

    analyzer = CustomerAnalyzer(Config(), llm, mock_db, cache=InsightCache())
    analyzer._analyze_churn_risk(mock_db.query_customer.return_value, mock_db.query_tickets.return_value)

    prompt = llm.complete.call_args[0][0]
    assert 'jane@acme.com' not in prompt, 'PII email must be redacted before prompt construction'
    assert '555-123-4567' not in prompt, 'PII phone must be redacted before prompt construction'


def test_handles_llm_failure_gracefully(mock_db):
    llm = Mock()
    llm.complete.side_effect = RuntimeError('provider timeout')

    analyzer = CustomerAnalyzer(Config(), llm, mock_db, cache=InsightCache())
    insights = analyzer.analyze_customer('cust-1')

    churn = next(item for item in insights if item.insight_type == 'churn_risk')
    assert churn.degraded_mode is True, 'LLM failure must return a degraded-mode insight, not raise an exception'
    assert churn.requires_review is True


def test_high_risk_actions_always_require_review_regardless_of_confidence(mock_db):
    llm = Mock()
    llm.complete.return_value = Mock(content=json.dumps({
        'summary': 'Likely churn',
        'confidence': 0.91,
        'evidence': ['multiple escalations'],
        'action': 'escalate to senior relationship manager',
    }))

    analyzer = CustomerAnalyzer(Config(require_human_review=True), llm, mock_db, cache=InsightCache())
    insight = analyzer._analyze_churn_risk(
        mock_db.query_customer.return_value,
        mock_db.query_tickets.return_value,
    )
    assert insight.requires_review is True, 'High-confidence actions still require human review in a regulated context'


def test_reuses_cached_results_to_reduce_cost(mock_db):
    llm = Mock()
    llm.complete.return_value = Mock(content=json.dumps({
        'summary': 'risk', 'confidence': 0.7, 'evidence': [], 'action': 'call'
    }))

    analyzer = CustomerAnalyzer(Config(), llm, mock_db, cache=InsightCache())
    analyzer._analyze_churn_risk(mock_db.query_customer.return_value, mock_db.query_tickets.return_value)
    analyzer._analyze_churn_risk(mock_db.query_customer.return_value, mock_db.query_tickets.return_value)

    assert llm.complete.call_count == 1, 'Second call must reuse cache - model should not be called twice'
`,
    },
  },
};

export default AI_GENAI_PRODUCTION_READINESS_TASK;
