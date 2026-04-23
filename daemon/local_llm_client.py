"""
Local LLM client for Agent Genesis (optional enrichment layer).

This module is an OPTIONAL helper for users who want the indexer to enrich
each conversation with LLM-generated metadata (summaries, extracted decisions,
identified patterns) while it indexes. It is NOT required for standard Agent
Genesis usage — search, indexing, stats, and the MCP server all work fine
without it.

When is this used?
    Only when `ConversationIndexer(enable_mkg_analysis=True)` is set (it
    defaults to False everywhere in-tree). If you pass the flag, each indexed
    conversation gets an extra HTTP call to generate a 150-word summary that's
    stored alongside the ChromaDB embedding.

What does it need?
    An OpenAI-compatible chat/completions endpoint reachable from the indexer
    process. Any of these work out of the box:
    - llama.cpp server (llama-server --host 0.0.0.0 --port 8084 ...)
    - vLLM             (vllm serve <model> ...)
    - LM Studio        (local server mode, OpenAI-compatible API)
    - Ollama           (with OLLAMA_HOST bound and OpenAI-compat proxy)
    - NVIDIA NIM, Groq, OpenAI itself — anything that speaks the API.

    Configure via the AGENT_GENESIS_LLM_ENDPOINT env var. Default is a local
    llama.cpp on port 8084. No auth headers are sent, so if your endpoint
    requires a bearer token you'll need to front it with a proxy that injects
    one (or extend this module).

Why the kwarg is still named `enable_mkg_analysis`:
    Backward compatibility. Existing callers pass that kwarg name. The
    underlying helper used to be named after the author's private LLM gateway
    ("MKG"); this module now provides a generic OpenAI-compatible client
    instead, but renaming the public kwarg would break downstream callers.

Cost:
    Zero cloud cost when pointed at a local endpoint. Whatever your LLM host's
    token pricing is when pointed at a cloud endpoint.
"""

import json
import logging
import os
from typing import Optional, Dict, Any, List
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Override via AGENT_GENESIS_LLM_ENDPOINT env var.
# Pass the full chat-completions URL (OpenAI-compatible; llama.cpp, vLLM, LM Studio, etc.).
LLM_ENDPOINT = os.environ.get(
    'AGENT_GENESIS_LLM_ENDPOINT',
    'http://localhost:8084/v1/chat/completions'
)
LLM_TIMEOUT_SECONDS = 60


class LocalLLMClient:
    """Generic OpenAI-compatible chat/completions client for enrichment tasks."""

    def __init__(self, model: str = "local"):
        """
        Initialize the client.

        Args:
            model: Model name sent in the request body. Most local servers
                (llama.cpp, vLLM, LM Studio) ignore this and use whatever model
                they've loaded; cloud endpoints (OpenAI, NVIDIA NIM, Groq)
                require a specific name. Default 'local' is safe for
                llama.cpp-style servers.
        """
        self.model = model
        self.max_tokens = 4000  # Conservative for analysis tasks

    def analyze_conversation(
        self,
        conversation_text: str,
        focus: str = "decisions"
    ) -> Optional[Dict[str, Any]]:
        """
        Analyze conversation using MKG.

        Args:
            conversation_text: Full conversation text
            focus: Analysis focus (decisions, patterns, context, summary)

        Returns:
            Analysis results as dict with extracted insights
        """
        prompt = self._build_analysis_prompt(conversation_text, focus)

        try:
            result = self._call_mkg(prompt)
            return self._parse_analysis_result(result, focus)
        except Exception as e:
            logger.error(f"MKG analysis failed: {e}")
            return None

    def extract_decisions(self, conversation_text: str) -> List[Dict[str, str]]:
        """
        Extract key decisions from conversation.

        Returns:
            List of decisions with context, reasoning, outcome
        """
        analysis = self.analyze_conversation(conversation_text, focus="decisions")

        if not analysis:
            return []

        return analysis.get('decisions', [])

    def generate_summary(self, conversation_text: str, max_length: int = 200) -> str:
        """
        Generate concise conversation summary.

        Args:
            conversation_text: Full conversation
            max_length: Maximum summary length in words

        Returns:
            Summary string
        """
        prompt = f"""Summarize this conversation in {max_length} words or less.
Focus on: key decisions, technical approaches, outcomes.

Conversation:
{conversation_text[:8000]}

Summary:"""

        try:
            result = self._call_mkg(prompt, max_tokens=500)
            return result.strip()
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return "Summary unavailable"

    def _build_analysis_prompt(self, conversation_text: str, focus: str) -> str:
        """Build analysis prompt based on focus area."""

        prompts = {
            "decisions": """Analyze this conversation and extract key decisions made.

For each decision, provide:
1. **Decision**: What was decided
2. **Context**: Why this decision was needed
3. **Reasoning**: Technical or strategic rationale
4. **Outcome**: Expected or actual result

Format as JSON array:
[
  {
    "decision": "...",
    "context": "...",
    "reasoning": "...",
    "outcome": "..."
  }
]

Conversation:
""",
            "patterns": """Analyze this conversation for recurring patterns.

Identify:
1. Technical patterns (architecture, tools, approaches)
2. Workflow patterns (debugging, testing, deployment)
3. Decision-making patterns (how choices are made)

Format as JSON object with pattern categories.

Conversation:
""",
            "context": """Extract contextual information from this conversation.

Provide:
1. **Project**: Which project is discussed
2. **Phase**: Development phase (planning, implementation, testing)
3. **Technologies**: Tools and frameworks mentioned
4. **Blockers**: Issues or challenges encountered
5. **Next Steps**: Planned follow-up actions

Format as JSON object.

Conversation:
""",
            "summary": """Provide a comprehensive summary of this conversation.

Include:
1. Main topic or goal
2. Key technical decisions
3. Outcomes or deliverables
4. Follow-up actions needed

Keep summary under 300 words.

Conversation:
"""
        }

        base_prompt = prompts.get(focus, prompts["summary"])

        # Truncate conversation to fit context window
        truncated = conversation_text[:12000]  # ~3K tokens

        return base_prompt + truncated

    def _call_mkg(self, prompt: str, max_tokens: Optional[int] = None) -> str:
        """
        POST to the configured OpenAI-compatible chat/completions endpoint.

        Args:
            prompt: Prompt to send
            max_tokens: Override default max_tokens

        Returns:
            Response text (content of the first choice's message).
        """
        tokens = max_tokens or self.max_tokens

        payload = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': 'You are a knowledge analyst. Respond with valid JSON only.'},
                {'role': 'user', 'content': prompt},
            ],
            'max_tokens': tokens,
            'temperature': 0.3,
        }

        try:
            response = requests.post(
                LLM_ENDPOINT,
                json=payload,
                timeout=LLM_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.Timeout:
            logger.error(f"LLM call timed out after {LLM_TIMEOUT_SECONDS}s at {LLM_ENDPOINT}")
            return json.dumps({'error': {'type': 'llm_timeout', 'endpoint': LLM_ENDPOINT}})
        except requests.RequestException as e:
            logger.error(f"LLM call failed at {LLM_ENDPOINT}: {e}")
            return json.dumps({'error': {'type': 'llm_unreachable', 'detail': str(e)}})

        data = response.json()
        choices = data.get('choices', [])
        if not choices:
            return json.dumps({'error': {'type': 'llm_empty_response', 'detail': 'no choices in response'}})
        return choices[0].get('message', {}).get('content', '').strip()

    def _parse_analysis_result(self, result: str, focus: str) -> Dict[str, Any]:
        """Parse the LLM response into a structured dict."""
        try:
            # Handle JSON responses
            if result.strip().startswith('{') or result.strip().startswith('['):
                data = json.loads(result)

                # Normalize based on focus
                if focus == "decisions":
                    if isinstance(data, list):
                        return {"decisions": data}
                    elif "decisions" in data:
                        return data
                    else:
                        return {"decisions": [data]}
                else:
                    return data if isinstance(data, dict) else {"result": data}
            else:
                # Plain text response
                return {"summary": result, "raw": result}

        except json.JSONDecodeError:
            # Fallback for non-JSON responses
            return {"summary": result, "raw": result}


# Backward-compat alias. The class was previously named after the author's private
# LLM gateway; the new name reflects what it actually is. Old imports like
# `from daemon.mkg_client import MKGClient` will still work via this alias and
# the file location is preserved by git history (git mv).
MKGClient = LocalLLMClient


def test_local_llm_client():
    """Smoke-test the client end-to-end against whatever endpoint is configured."""
    client = LocalLLMClient()

    test_conversation = """
    User: I'm working on Empire's Edge pathfinding. Should I use A* or Dijkstra?

    Assistant: For Empire's Edge, A* is better because:
    1. You have good heuristics (distance to target)
    2. Your maps are large (A* reduces search space)
    3. Performance is critical for real-time strategy

    Decision: Use A* with Manhattan distance heuristic.
    """

    print("Testing MKG conversation analysis...")

    # Test decision extraction
    decisions = client.extract_decisions(test_conversation)
    print(f"✅ Extracted {len(decisions)} decisions")

    # Test summary generation
    summary = client.generate_summary(test_conversation, max_length=50)
    print(f"✅ Generated summary: {summary[:100]}...")

    return True


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    test_local_llm_client()
