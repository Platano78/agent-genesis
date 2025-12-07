"""Test script for Agent Genesis MCP Server

This script validates the MCP server functionality by:
1. Testing tool discovery
2. Executing search_conversations with various parameters
3. Verifying API health checks
4. Testing error handling
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path to import the server
sys.path.insert(0, str(Path(__file__).parent))

from fastmcp import Client
from agent_genesis_mcp import mcp


async def test_tool_discovery():
    """Test that all tools are properly registered and discoverable."""
    print("\n" + "="*60)
    print("TEST 1: Tool Discovery")
    print("="*60)

    async with Client(mcp) as client:
        tools = await client.list_tools()

        print(f"\n✓ Found {len(tools)} tools:")
        for tool in tools:
            print(f"  - {tool.name}: {tool.description}")

        expected_tools = {"search_conversations", "get_api_stats", "check_api_health"}
        found_tools = {tool.name for tool in tools}

        if expected_tools == found_tools:
            print("\n✓ All expected tools are registered")
            return True
        else:
            print(f"\n✗ Missing tools: {expected_tools - found_tools}")
            return False


async def test_health_check():
    """Test the API health check tool."""
    print("\n" + "="*60)
    print("TEST 2: API Health Check")
    print("="*60)

    async with Client(mcp) as client:
        result = await client.call_tool("check_api_health", {})

        print(f"\nHealth Check Result:")
        print(f"  Status: {result.data.get('status', 'unknown')}")
        print(f"  Success: {result.data.get('success', False)}")

        if result.data.get('success'):
            print(f"  Endpoint: {result.data.get('endpoint')}")
            print("\n✓ API is healthy and reachable")
            return True
        else:
            print(f"  Message: {result.data.get('message')}")
            print("\n✗ API health check failed")
            if 'troubleshooting' in result.data:
                print("\nTroubleshooting steps:")
                for step in result.data['troubleshooting']:
                    print(f"  - {step}")
            return False


async def test_stats_retrieval():
    """Test the stats retrieval tool."""
    print("\n" + "="*60)
    print("TEST 3: Stats Retrieval")
    print("="*60)

    async with Client(mcp) as client:
        result = await client.call_tool("get_api_stats", {})

        if result.data.get('success'):
            stats = result.data.get('stats', {})
            print(f"\nCorpus Statistics:")
            print(f"  Total Conversations: {stats.get('total_conversations', 'N/A')}")
            print(f"  Total Projects: {stats.get('total_projects', 'N/A')}")
            print("\n✓ Stats retrieved successfully")
            return True
        else:
            print(f"\n✗ Failed to retrieve stats: {result.data.get('message')}")
            return False


async def test_search_basic():
    """Test basic search functionality."""
    print("\n" + "="*60)
    print("TEST 4: Basic Search")
    print("="*60)

    async with Client(mcp) as client:
        # Test with a common query
        result = await client.call_tool(
            "search_conversations",
            {"query": "FastMCP", "limit": 3}
        )

        if result.data.get('success'):
            print(f"\nSearch Query: 'FastMCP'")
            print(f"Total Results: {result.data.get('total_found', 0)}")

            for i, conv in enumerate(result.data.get('results', []), 1):
                print(f"\nResult {i}:")
                print(f"  Score: {conv.get('score')}")
                print(f"  Project: {conv.get('project')}")
                print(f"  Timestamp: {conv.get('timestamp')}")
                print(f"  Snippet: {conv.get('content_snippet', '')[:100]}...")

            print("\n✓ Basic search completed successfully")
            return True
        else:
            print(f"\n✗ Search failed: {result.data.get('message')}")
            return False


async def test_search_with_project_filter():
    """Test search with project filter."""
    print("\n" + "="*60)
    print("TEST 5: Search with Project Filter")
    print("="*60)

    async with Client(mcp) as client:
        # Test with project filter
        result = await client.call_tool(
            "search_conversations",
            {
                "query": "MCP server",
                "limit": 2,
                "project": "agent-genesis"
            }
        )

        if result.data.get('success'):
            print(f"\nSearch Query: 'MCP server' (project: agent-genesis)")
            print(f"Total Results: {result.data.get('total_found', 0)}")
            print(f"Project Filter: {result.data.get('project_filter')}")

            print("\n✓ Project-filtered search completed successfully")
            return True
        else:
            print(f"\n✗ Search with filter failed: {result.data.get('message')}")
            return False


async def test_error_handling():
    """Test error handling with invalid parameters."""
    print("\n" + "="*60)
    print("TEST 6: Error Handling")
    print("="*60)

    async with Client(mcp) as client:
        # Test with invalid limit
        result = await client.call_tool(
            "search_conversations",
            {"query": "test", "limit": 100}  # Max is 50
        )

        if 'error' in result.data:
            print(f"\n✓ Error handling working correctly:")
            print(f"  Error: {result.data.get('error')}")
            print(f"  Message: {result.data.get('message')}")
            return True
        else:
            print("\n✗ Expected error for invalid limit, but got success")
            return False


async def test_resource_access():
    """Test resource access."""
    print("\n" + "="*60)
    print("TEST 7: Resource Access")
    print("="*60)

    async with Client(mcp) as client:
        resources = await client.list_resources()

        print(f"\n✓ Found {len(resources)} resources:")
        for resource in resources:
            print(f"  - {resource.uri}: {resource.name}")

        # Try to read the API endpoints resource
        if resources:
            content = await client.read_resource(resources[0].uri)
            # content is a list of resource contents
            if isinstance(content, list) and len(content) > 0:
                resource_text = content[0].text if hasattr(content[0], 'text') else str(content[0])
                print(f"\nResource Content Preview:")
                print(resource_text[:200] + "..." if len(resource_text) > 200 else resource_text)
                print("\n✓ Resource access working")
                return True
            else:
                print(f"\n✗ Unexpected resource format: {type(content)}")
                return False
        else:
            print("\n✗ No resources found")
            return False


async def run_all_tests():
    """Run all tests and report results."""
    print("\n" + "="*60)
    print("AGENT GENESIS MCP SERVER TEST SUITE")
    print("="*60)

    tests = [
        ("Tool Discovery", test_tool_discovery),
        ("API Health Check", test_health_check),
        ("Stats Retrieval", test_stats_retrieval),
        ("Basic Search", test_search_basic),
        ("Project Filter Search", test_search_with_project_filter),
        ("Error Handling", test_error_handling),
        ("Resource Access", test_resource_access),
    ]

    results = {}
    for test_name, test_func in tests:
        try:
            results[test_name] = await test_func()
        except Exception as e:
            print(f"\n✗ Test '{test_name}' raised exception: {e}")
            results[test_name] = False

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, passed_test in results.items():
        status = "✓ PASS" if passed_test else "✗ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n✓ All tests passed!")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(run_all_tests())
    sys.exit(exit_code)
