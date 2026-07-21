"""Cross-chat contract for evidence-first candidate discovery."""

from app.agent_chat import system_prompt as agent_prompt
from app.agent_chat.tools import AGENT_CHAT_TOOLS
from app.agent_runtime import system_prompt as runtime_prompt
from app.taali_chat import system_prompt as search_prompt


def _agent_tool(name: str) -> dict:
    return next(tool for tool in AGENT_CHAT_TOOLS if tool.get("name") == name)


def test_search_chat_routes_bounded_qualitative_discovery_to_evidence_path():
    prompt = search_prompt.SYSTEM_PROMPT
    assert "BOUNDED qualitative candidate discovery" in prompt
    assert "find_top_candidates" in prompt
    assert 'pass `query="candidates"' in prompt
    assert "evidence_basis=stored_role_requirements" in prompt
    assert "criteria_unchecked" in prompt
    assert "Unhedged qualities are required" in prompt
    assert "self-contained" in prompt


def test_search_chat_distinguishes_broad_retrieval_from_bounded_evidence():
    prompt = search_prompt.SYSTEM_PROMPT
    assert "broad QUALITATIVE ask" in prompt
    assert "is_exact_empty" in prompt
    assert "deep_verify=true" in prompt
    assert "unchecked remainder" in prompt
    assert "Only `pool_size=0`" in prompt


def test_agent_chat_uses_same_qualitative_and_report_contract():
    prompt = agent_prompt.SYSTEM_PROMPT
    assert "`search_candidates` only for broad" in prompt
    assert "`nl_search_candidates` only for broad" not in prompt
    assert "BOUNDED qualitative candidate discovery" in prompt
    assert "evidence_basis=stored_role_requirements" in prompt
    assert "criteria_unchecked" in prompt
    assert "report_url" in prompt
    assert "Unhedged qualities are required" in prompt
    assert "self-contained" in prompt

    grounded = _agent_tool("find_top_candidates")["description"]
    exhaustive = _agent_tool("search_candidates")["description"]
    assert "stored scorecard evidence" in grounded
    assert "criteria_unchecked" in grounded
    assert "hybrid retrieval" in exhaustive
    assert "is_exact_empty=true" in exhaustive
    assert "bounded qualitative discovery" in exhaustive


def test_autonomous_agent_routes_search_by_evidence_contract():
    prompt = runtime_prompt._STATIC_HEADER
    assert "nl_search_candidates with rerank=false" in prompt
    assert "find_top_candidates" in prompt
    assert "database_matches is the PostgreSQL" in prompt
    assert "retrieval_matches is the fused graph/PostgreSQL" in prompt
    assert "is_exact_empty=true" in prompt
    assert "criteria_unchecked" in prompt
    assert "Search discovers candidates; it does not authorize an action" in prompt
