"""
Anthropic Claude AI service for code analysis and conversational assistance.

Provides chat-based interactions and automated code quality analysis
powered by Claude for use during candidate assessments.
"""

import json
import logging

from anthropic import Anthropic

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful coding assistant helping a candidate debug code."
)


class ClaudeService:
    """Service for interacting with the Anthropic Claude API."""

    def __init__(self, api_key: str):
        """
        Initialise the Claude service.

        Args:
            api_key: Anthropic API key.
        """
        self.client = Anthropic(api_key=api_key)
        self.model = "claude-sonnet-4-20250514"
        logger.info("ClaudeService initialised with model=%s", self.model)

    def chat(self, messages: list, system: str = None) -> dict:
        """
        Send a conversation to Claude and return the response.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            system: Optional system prompt. Defaults to the coding assistant prompt.

        Returns:
            Dict with keys: success, content, tokens_used.
        """
        try:
            system_prompt = system or DEFAULT_SYSTEM_PROMPT
            logger.info(
                "Sending chat request to Claude (messages=%d, model=%s)",
                len(messages),
                self.model,
            )

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
                messages=messages,
            )

            content = response.content[0].text
            tokens_used = response.usage.input_tokens + response.usage.output_tokens

            logger.info(
                "Claude chat response received (tokens_used=%d)", tokens_used
            )

            return {
                "success": True,
                "content": content,
                "tokens_used": tokens_used,
            }
        except Exception as e:
            logger.error("Claude chat request failed: %s", str(e))
            return {
                "success": False,
                "content": "",
                "tokens_used": 0,
            }

    def analyze_code_quality(self, code: str) -> dict:
        """
        Analyse code quality across multiple dimensions.

        Evaluates correctness, readability, efficiency, and best-practices
        adherence, each scored 0-10.

        Args:
            code: The source code to analyse.

        Returns:
            Dict with keys: success, analysis.
        """
        try:
            logger.info(
                "Requesting code quality analysis (code_length=%d chars)", len(code)
            )

            analysis_prompt = (
                "Analyse the following code and provide a JSON response with these fields:\n"
                "- correctness: score 0-10 and brief explanation\n"
                "- readability: score 0-10 and brief explanation\n"
                "- efficiency: score 0-10 and brief explanation\n"
                "- best_practices: score 0-10 and brief explanation\n"
                "- overall_score: weighted average 0-10\n"
                "- summary: one paragraph overall assessment\n\n"
                "Respond ONLY with valid JSON, no markdown formatting.\n\n"
                f"Code:\n```\n{code}\n```"
            )

            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system="You are an expert code reviewer. Respond only with valid JSON.",
                messages=[{"role": "user", "content": analysis_prompt}],
            )

            analysis_text = response.content[0].text

            # Validate that the response is parseable JSON
            try:
                json.loads(analysis_text)
            except json.JSONDecodeError:
                logger.warning("Claude returned non-JSON analysis, returning raw text")

            logger.info("Code quality analysis completed successfully")

            return {
                "success": True,
                "analysis": analysis_text,
            }
        except Exception as e:
            logger.error("Code quality analysis failed: %s", str(e))
            return {
                "success": False,
                "analysis": "",
            }
