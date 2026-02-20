"""Agent Genesis MCP Server

A FastMCP server that provides search access to Claude Code conversation history
via the Agent Genesis Phase 2 API.
"""

import logging
from typing import Optional
import requests
from fastmcp import FastMCP

# Import scheduler and indexing tools
from scheduler import SchedulerManager
from indexing_tools import run_manual_indexing

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastMCP server
# MCP 2025-11-25 compliant with explicit version and capabilities
mcp = FastMCP(
    name="Agent Genesis",
    version="1.1.0",
)

# API configuration
API_BASE_URL = "http://localhost:8080"
API_TIMEOUT = 60  # seconds


@mcp.tool
def search_conversations(
    query: str,
    limit: int = 5,
    project: Optional[str] = None
) -> dict:
    """Search Claude Code conversation history using Agent Genesis API.

    Args:
        query: Search query string to find relevant conversations
        limit: Maximum number of results to return (default: 5, max: 50)
        project: Optional project name filter to limit search scope

    Returns:
        Dictionary containing search results with conversation metadata:
        - results: List of matching conversations with scores
        - total_found: Total number of matches
        - query_info: Information about the search query
    """
    try:
        # Validate limit
        if limit < 1 or limit > 50:
            return {
                "error": "Invalid limit",
                "message": "Limit must be between 1 and 50"
            }

        # Build request payload
        payload = {
            "query": query,
            "n_results": limit
        }

        if project:
            payload["project_filter"] = project

        # Call Agent Genesis API
        logger.info(f"Searching for: {query} (limit={limit}, project={project})")
        response = requests.post(
            f"{API_BASE_URL}/search",
            json=payload,
            timeout=API_TIMEOUT
        )

        response.raise_for_status()
        data = response.json()

        # API returns: {"results": [...], "results_count": N, "search_type": "..."}
        results_list = data.get("results", [])
        if not isinstance(results_list, list):
            results_list = []

        # Format results for better readability
        formatted_results = []
        for result in results_list:
            metadata = result.get("metadata", {})

            # Calculate similarity score (1 - distance for better UX)
            distance = result.get("distance", 1.0)
            similarity_score = round(1.0 - distance, 3)

            formatted_results.append({
                "score": similarity_score,
                "project": metadata.get("project", "unknown"),
                "timestamp": metadata.get("timestamp", ""),
                "conversation_id": metadata.get("conversation_id", ""),
                "content_snippet": result.get("document", "")[:200] + "..." if len(result.get("document", "")) > 200 else result.get("document", ""),
                "full_content_length": len(result.get("document", "")),
                "git_branch": metadata.get("git_branch", ""),
                "role": metadata.get("role", "")
            })

        return {
            "success": True,
            "results": formatted_results,
            "total_found": len(formatted_results),
            "query": query,
            "project_filter": project,
            "api_status": "operational"
        }

    except requests.exceptions.ConnectionError:
        logger.error("Failed to connect to Agent Genesis API")
        return {
            "error": "API Connection Failed",
            "message": "Could not connect to Agent Genesis API at localhost:8080. Please ensure the API is running.",
            "troubleshooting": [
                "Check if Agent Genesis Phase 2 API is running (docker-compose up -d)",
                "Verify API is accessible at http://localhost:8080/health",
                "Check Docker container status: docker ps | grep agent-genesis"
            ]
        }

    except requests.exceptions.Timeout:
        logger.error("API request timed out")
        return {
            "error": "API Timeout",
            "message": f"Request to Agent Genesis API timed out after {API_TIMEOUT} seconds"
        }

    except requests.exceptions.HTTPError as e:
        logger.error(f"API returned error: {e}")
        return {
            "error": "API Error",
            "message": f"Agent Genesis API returned error: {str(e)}",
            "status_code": e.response.status_code if e.response else None
        }

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {
            "error": "Unexpected Error",
            "message": str(e)
        }


@mcp.tool
def get_api_stats() -> dict:
    """Get statistics about the indexed conversation corpus.

    Returns:
        Dictionary containing corpus statistics:
        - total_conversations: Number of indexed conversations
        - total_projects: Number of unique projects
        - index_status: Health status of the index
    """
    try:
        response = requests.get(
            f"{API_BASE_URL}/stats",
            timeout=API_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()

        return {
            "success": True,
            "stats": data,
            "api_status": "operational"
        }

    except requests.exceptions.ConnectionError:
        return {
            "error": "API Connection Failed",
            "message": "Could not connect to Agent Genesis API. Please ensure it is running."
        }
    except Exception as e:
        return {
            "error": "Error retrieving stats",
            "message": str(e)
        }


@mcp.tool
def check_api_health() -> dict:
    """Check if the Agent Genesis API is healthy and operational.

    Returns:
        Dictionary with health status and API information
    """
    try:
        response = requests.get(
            f"{API_BASE_URL}/health",
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        return {
            "success": True,
            "status": "healthy",
            "api_response": data,
            "endpoint": API_BASE_URL
        }

    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "status": "unreachable",
            "message": "Cannot connect to Agent Genesis API",
            "endpoint": API_BASE_URL,
            "troubleshooting": [
                "Run: docker-compose up -d",
                "Check: docker ps | grep agent-genesis",
                "Verify: curl http://localhost:8080/health"
            ]
        }
    except Exception as e:
        return {
            "success": False,
            "status": "error",
            "message": str(e),
            "endpoint": API_BASE_URL
        }


@mcp.tool
def manage_scheduler(
    action: str,
    frequency_minutes: Optional[int] = None,
    enabled: Optional[bool] = None
) -> dict:
    """Manage automated conversation indexing scheduler.

    Args:
        action: Action to perform (status|enable|disable|remove|configure)
        frequency_minutes: Indexing interval in minutes (default: 30, min: 5, max: 1440)
        enabled: Whether task should be enabled (used with 'enable' action)

    Returns:
        Dictionary with scheduler status and operation result
    """
    try:
        scheduler = SchedulerManager()

        # Validate action
        valid_actions = ['status', 'enable', 'disable', 'remove', 'configure']
        if action not in valid_actions:
            return {
                "error": "Invalid action",
                "message": f"Action must be one of: {', '.join(valid_actions)}",
                "valid_actions": valid_actions
            }

        # Execute action
        if action == 'status':
            return scheduler.get_status()

        elif action == 'enable':
            return scheduler.enable(frequency_minutes=frequency_minutes)

        elif action == 'disable':
            return scheduler.disable()

        elif action == 'remove':
            return scheduler.remove()

        elif action == 'configure':
            if frequency_minutes is None:
                return {
                    "error": "Missing parameter",
                    "message": "frequency_minutes is required for configure action"
                }
            return scheduler.configure(frequency_minutes=frequency_minutes)

    except Exception as e:
        logger.error(f"Scheduler management error: {e}", exc_info=True)
        return {
            "error": "Scheduler operation failed",
            "message": str(e),
            "action": action
        }


@mcp.tool
def index_conversations(
    full_reindex: bool = False,
    time_range: Optional[str] = None,
    force: bool = False
) -> dict:
    """Manually trigger conversation indexing.

    Args:
        full_reindex: If True, reindex all conversations. If False, only new ones (default: False)
        time_range: Optional time filter - "1h", "24h", "7d", "30d" (default: None for all)
        force: Bypass rate limiting checks (default: False)

    Returns:
        Dictionary with indexing statistics:
        - conversations_processed: Number of conversations indexed
        - tokens_indexed: Approximate token count
        - duration: Time taken in seconds
        - last_indexed_timestamp: ISO timestamp of completion
    """
    try:
        # Validate time_range if provided
        if time_range and time_range not in ['1h', '24h', '7d', '30d']:
            return {
                "error": "Invalid time_range",
                "message": "time_range must be one of: 1h, 24h, 7d, 30d",
                "valid_values": ['1h', '24h', '7d', '30d']
            }

        logger.info(f"Manual indexing requested (full={full_reindex}, time_range={time_range}, force={force})")

        # Run indexing
        result = run_manual_indexing(
            full_reindex=full_reindex,
            time_range=time_range,
            force=force
        )

        return result

    except Exception as e:
        logger.error(f"Manual indexing error: {e}", exc_info=True)
        return {
            "error": "Indexing failed",
            "message": str(e)
        }


# Resource: API endpoint information
@mcp.resource("config://api-endpoints")
def get_api_endpoints() -> str:
    """Provides information about Agent Genesis API endpoints."""
    return f"""# Agent Genesis API Endpoints

Base URL: {API_BASE_URL}

## Available Endpoints:

### POST /search
Search Claude Code conversations
- Body: {{"query": string, "limit": int, "project": string (optional)}}
- Returns: Ranked search results with conversation metadata

### GET /stats
Get corpus statistics
- Returns: Total conversations, projects, index status

### GET /health
Health check endpoint
- Returns: API health status and version info

## Indexed Data:
- 17,538 Claude Code conversations
- Multiple projects indexed
- Real-time search capability
"""


# =============================================================================
# ADDITIONAL RESOURCES - MCP 2025-11-25 Compliance
# =============================================================================

@mcp.resource("agentgenesis://stats")
def get_stats_resource() -> str:
    """Get current corpus statistics as a resource."""
    try:
        response = requests.get(f"{API_BASE_URL}/stats", timeout=API_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        import json
        return json.dumps({
            "type": "statistics",
            "api_status": "operational",
            "stats": data
        }, indent=2)
    except Exception as e:
        import json
        return json.dumps({
            "type": "statistics",
            "api_status": "error",
            "error": str(e)
        }, indent=2)


@mcp.resource("agentgenesis://health")
def get_health_resource() -> str:
    """Get API health status as a resource."""
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=10)
        response.raise_for_status()
        data = response.json()
        import json
        return json.dumps({
            "type": "health",
            "status": "healthy",
            "endpoint": API_BASE_URL,
            "api_response": data
        }, indent=2)
    except Exception as e:
        import json
        return json.dumps({
            "type": "health",
            "status": "unhealthy",
            "endpoint": API_BASE_URL,
            "error": str(e)
        }, indent=2)


# =============================================================================
# PROMPTS - MCP 2025-11-25 Compliance
# =============================================================================

@mcp.prompt()
def search_conversations_workflow(topic: str = "") -> str:
    """Interactive workflow for searching Claude Code conversations."""
    return f"""# Conversation Search Workflow

You are helping search through Claude Code conversation history using Agent Genesis.

## Search Topic
{topic if topic else "[User will provide search topic]"}

## Search Steps

1. **Formulate Query**
   - Identify key terms and concepts
   - Consider project context if relevant
   - Think about related technical terms

2. **Execute Search**
   Use `search_conversations` with:
   ```
   search_conversations(
       query="{topic if topic else 'your search terms'}",
       limit=10,  # Adjust as needed (1-50)
       project=None  # Optional: filter by project name
   )
   ```

3. **Analyze Results**
   - Review similarity scores (higher = more relevant)
   - Check conversation timestamps
   - Read content snippets for context

4. **Refine if Needed**
   - Try different keywords
   - Add project filter
   - Adjust result limit

## Tips for Effective Searches

- Use specific technical terms
- Include error messages or function names
- Try related concepts if first search misses
- Filter by project for focused results

## Example Searches

```python
# General topic search
search_conversations(query="MCP server implementation", limit=10)

# Project-specific search
search_conversations(query="authentication flow", limit=5, project="my-app")

# Error-specific search
search_conversations(query="TypeError undefined is not a function", limit=20)
```
"""


@mcp.prompt()
def trigger_indexing_workflow() -> str:
    """Interactive workflow for managing conversation indexing."""
    return """# Indexing Management Workflow

You are helping manage the Agent Genesis conversation indexing process.

## Available Operations

### 1. Check Status First
Always start by checking API health:
```python
check_api_health()
```

### 2. Manual Indexing

**Index new conversations only (incremental):**
```python
index_conversations(
    full_reindex=False,
    time_range="24h",  # Optional: 1h, 24h, 7d, 30d
    force=False
)
```

**Full reindex (all conversations):**
```python
index_conversations(
    full_reindex=True,
    force=True  # Bypass rate limiting
)
```

### 3. Scheduler Management

**Check scheduler status:**
```python
manage_scheduler(action="status")
```

**Enable automatic indexing:**
```python
manage_scheduler(
    action="enable",
    frequency_minutes=30  # Index every 30 minutes
)
```

**Configure frequency:**
```python
manage_scheduler(
    action="configure",
    frequency_minutes=60  # Change to hourly
)
```

**Disable scheduler:**
```python
manage_scheduler(action="disable")
```

## Recommended Workflow

1. Check API health
2. Check current stats with `get_api_stats()`
3. Decide on indexing approach:
   - Incremental for regular updates
   - Full reindex after major changes
4. Optionally configure scheduler for automation

## Time Range Options

| Value | Description |
|-------|-------------|
| 1h    | Last hour only |
| 24h   | Last 24 hours |
| 7d    | Last week |
| 30d   | Last month |
| None  | All conversations |
"""


def main():
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
