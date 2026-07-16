"""Cross-chat contract for evidence-first candidate discovery."""

from app.agent_chat import system_prompt as agent_prompt
from app.agent_chat.tools import AGENT_CHAT_TOOLS
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


def test_search_chat_distinguishes_exhaustive_retrieval_from_bounded_evidence():
    prompt = search_prompt.SYSTEM_PROMPT
    assert "exhaustive QUALITATIVE ask" in prompt
    assert "complete retrieval count" in prompt
    assert "deep_verify=true" in prompt
    assert "unchecked remainder" in prompt
    assert "Only `pool_size=0`" in prompt


def test_agent_chat_uses_same_qualitative_and_report_contract():
    prompt = agent_prompt.SYSTEM_PROMPT
    assert "BOUNDED qualitative candidate discovery" in prompt
    assert "evidence_basis=stored_role_requirements" in prompt
    assert "criteria_unchecked" in prompt
    assert "report_url" in prompt

    grounded = _agent_tool("find_top_candidates")["description"]
    exhaustive = _agent_tool("search_candidates")["description"]
    assert "stored scorecard evidence" in grounded
    assert "criteria_unchecked" in grounded
    assert "Exhaustive/deterministic" in exhaustive
    assert "bounded qualitative discovery" in exhaustive
