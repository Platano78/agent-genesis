"""
MKG (DeepSeek Bridge) Client for Agent Genesis.

Leverages existing MKG infrastructure for:
- Decision extraction from conversations
- Pattern analysis
- Context summarization

Uses local LLM (zero cloud API costs).
"""

import json
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)


class MKGClient:
    """Client for MKG (Mecha King Ghidorah) DeepSeek Bridge."""

    def __init__(self, model: str = "qwen3"):
        """
        Initialize MKG client.

        Args:
            model: MKG model to use (qwen3, deepseek3.1, local, gemini)
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
        Call MKG via subprocess (leverages existing MCP server).

        Args:
            prompt: Prompt to send
            max_tokens: Override default max_tokens

        Returns:
            MKG response text
        """
        tokens = max_tokens or self.max_tokens

        # MKG is available via MCP tools in the parent Claude instance
        # For container execution, we'll use a simple HTTP call to the health endpoint
        # which will be extended to support MKG queries

        # For now, use direct model call if available
        try:
            # Check if running in container vs development
            if Path("/.dockerenv").exists():
                # In container - use placeholder for now
                # TODO: Implement HTTP endpoint for MKG access
                logger.warning("Container MKG access not yet implemented")
                return '{"decisions": [], "summary": "Analysis pending MKG integration"}'
            else:
                # Development mode - could use subprocess to call MKG
                # For Phase 2, we'll implement HTTP endpoint
                return self._mock_analysis(prompt)

        except Exception as e:
            logger.error(f"MKG call failed: {e}")
            raise

    def _mock_analysis(self, prompt: str) -> str:
        """Mock analysis for testing (Phase 2 initial deployment)."""
        return json.dumps({
            "decisions": [
                {
                    "decision": "Example decision from conversation",
                    "context": "Technical requirement identified",
                    "reasoning": "Optimal approach for constraints",
                    "outcome": "Implementation successful"
                }
            ],
            "summary": "Conversation analyzed (mock mode - replace with real MKG)"
        })

    def _parse_analysis_result(self, result: str, focus: str) -> Dict[str, Any]:
        """Parse MKG response into structured format."""
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


def test_mkg_client():
    """Test MKG client functionality."""
    client = MKGClient()

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
    test_mkg_client()
