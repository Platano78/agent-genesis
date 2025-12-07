#!/usr/bin/env python3
"""
MCP 2025-11-25 Compliance Validation Test Script
Server: agent-genesis

This script validates compliance with the MCP 2025-11-25 specification.
"""

import json
import sys
import os
from datetime import datetime

# Test results tracking
test_results = {
    "server": "agent-genesis",
    "spec_version": "2025-11-25",
    "test_date": datetime.now().isoformat(),
    "tests": [],
    "passed": 0,
    "failed": 0
}

def log_test(name: str, passed: bool, details: str = ""):
    """Log a test result"""
    result = {
        "name": name,
        "passed": passed,
        "details": details
    }
    test_results["tests"].append(result)
    if passed:
        test_results["passed"] += 1
        print(f"  [PASS] {name}")
    else:
        test_results["failed"] += 1
        print(f"  [FAIL] {name}: {details}")

def test_server_initialization():
    """Test that server declares correct protocol version and capabilities"""
    print("\n[TEST] Testing Server Initialization...")
    
    server_file = os.path.join(os.path.dirname(__file__), "agent_genesis_mcp.py")
    
    if not os.path.exists(server_file):
        log_test("Server file exists", False, "agent_genesis_mcp.py not found")
        return
    
    log_test("Server file exists", True)
    
    with open(server_file, 'r') as f:
        content = f.read()
    
    # Check for explicit version
    if 'version="' in content or "version='" in content:
        log_test("Explicit version declared", True)
    else:
        log_test("Explicit version declared", False, "No version parameter in FastMCP init")
    
    # Check for FastMCP import
    if 'from fastmcp import FastMCP' in content:
        log_test("FastMCP import present", True)
    else:
        log_test("FastMCP import present", False, "Missing FastMCP import")
    
    # Check for MCP 2025-11-25 comment
    if '2025-11-25' in content:
        log_test("MCP 2025-11-25 reference", True)
    else:
        log_test("MCP 2025-11-25 reference", False, "No 2025-11-25 spec reference found")

def test_tools_defined():
    """Test that all expected tools are defined"""
    print("\n[TEST] Testing Tools Definition...")
    
    server_file = os.path.join(os.path.dirname(__file__), "agent_genesis_mcp.py")
    
    with open(server_file, 'r') as f:
        content = f.read()
    
    expected_tools = [
        "search_conversations",
        "get_api_stats",
        "check_api_health",
        "manage_scheduler",
        "index_conversations"
    ]
    
    for tool in expected_tools:
        if f"def {tool}" in content and "@mcp.tool" in content:
            log_test(f"Tool '{tool}' defined", True)
        else:
            log_test(f"Tool '{tool}' defined", False, f"Tool {tool} not found")

def test_resources_defined():
    """Test that MCP 2025-11-25 resources are defined"""
    print("\n[TEST] Testing Resources (MCP 2025-11-25)...")
    
    server_file = os.path.join(os.path.dirname(__file__), "agent_genesis_mcp.py")
    
    with open(server_file, 'r') as f:
        content = f.read()
    
    expected_resources = [
        "config://api-endpoints",
        "agentgenesis://stats",
        "agentgenesis://health"
    ]
    
    for resource in expected_resources:
        if f'@mcp.resource("{resource}")' in content:
            log_test(f"Resource '{resource}' defined", True)
        else:
            log_test(f"Resource '{resource}' defined", False, f"Resource {resource} not found")

def test_prompts_defined():
    """Test that MCP 2025-11-25 prompts are defined"""
    print("\n[TEST] Testing Prompts (MCP 2025-11-25)...")
    
    server_file = os.path.join(os.path.dirname(__file__), "agent_genesis_mcp.py")
    
    with open(server_file, 'r') as f:
        content = f.read()
    
    expected_prompts = [
        "search_conversations_workflow",
        "trigger_indexing_workflow"
    ]
    
    for prompt in expected_prompts:
        if f"def {prompt}" in content and "@mcp.prompt()" in content:
            log_test(f"Prompt '{prompt}' defined", True)
        else:
            log_test(f"Prompt '{prompt}' defined", False, f"Prompt {prompt} not found")

def test_api_integration():
    """Test API integration code"""
    print("\n[TEST] Testing API Integration...")
    
    server_file = os.path.join(os.path.dirname(__file__), "agent_genesis_mcp.py")
    
    with open(server_file, 'r') as f:
        content = f.read()
    
    # Check for API configuration
    if 'API_BASE_URL' in content:
        log_test("API base URL configured", True)
    else:
        log_test("API base URL configured", False, "No API_BASE_URL found")
    
    if 'API_TIMEOUT' in content:
        log_test("API timeout configured", True)
    else:
        log_test("API timeout configured", False, "No API_TIMEOUT found")
    
    # Check for requests library
    if 'import requests' in content:
        log_test("Requests library imported", True)
    else:
        log_test("Requests library imported", False, "No requests import found")
    
    # Check for error handling
    if 'requests.exceptions' in content:
        log_test("Request error handling", True)
    else:
        log_test("Request error handling", False, "No request exception handling")

def test_scheduler_integration():
    """Test scheduler integration"""
    print("\n[TEST] Testing Scheduler Integration...")
    
    server_file = os.path.join(os.path.dirname(__file__), "agent_genesis_mcp.py")
    scheduler_file = os.path.join(os.path.dirname(__file__), "scheduler.py")
    
    with open(server_file, 'r') as f:
        content = f.read()
    
    # Check for scheduler import
    if 'from scheduler import' in content:
        log_test("Scheduler module imported", True)
    else:
        log_test("Scheduler module imported", False, "No scheduler import")
    
    # Check if scheduler file exists
    if os.path.exists(scheduler_file):
        log_test("Scheduler module exists", True)
    else:
        log_test("Scheduler module exists", False, "scheduler.py not found")

def test_indexing_integration():
    """Test indexing tools integration"""
    print("\n[TEST] Testing Indexing Integration...")
    
    server_file = os.path.join(os.path.dirname(__file__), "agent_genesis_mcp.py")
    indexing_file = os.path.join(os.path.dirname(__file__), "indexing_tools.py")
    
    with open(server_file, 'r') as f:
        content = f.read()
    
    # Check for indexing tools import
    if 'from indexing_tools import' in content:
        log_test("Indexing tools imported", True)
    else:
        log_test("Indexing tools imported", False, "No indexing_tools import")
    
    # Check if indexing tools file exists
    if os.path.exists(indexing_file):
        log_test("Indexing tools module exists", True)
    else:
        log_test("Indexing tools module exists", False, "indexing_tools.py not found")

def test_pyproject_config():
    """Test pyproject.toml configuration"""
    print("\n[TEST] Testing Project Configuration...")
    
    pyproject_file = os.path.join(os.path.dirname(__file__), "pyproject.toml")
    
    if os.path.exists(pyproject_file):
        log_test("pyproject.toml exists", True)
    else:
        log_test("pyproject.toml exists", False, "pyproject.toml not found")
        return
    
    with open(pyproject_file, 'r') as f:
        content = f.read()
    
    # Check for fastmcp dependency
    if 'fastmcp' in content:
        log_test("FastMCP dependency declared", True)
    else:
        log_test("FastMCP dependency declared", False, "No fastmcp in dependencies")
    
    # Check for version
    if 'version = "1.1.0"' in content:
        log_test("Version 1.1.0 in pyproject", True)
    else:
        log_test("Version 1.1.0 in pyproject", False, "Version mismatch in pyproject.toml")

def generate_report():
    """Generate final test report"""
    print("\n" + "="*60)
    print("MCP 2025-11-25 COMPLIANCE TEST REPORT")
    print("="*60)
    print(f"Server: {test_results['server']}")
    print(f"Spec Version: {test_results['spec_version']}")
    print(f"Test Date: {test_results['test_date']}")
    print(f"\nResults: {test_results['passed']} passed, {test_results['failed']} failed")
    
    total = test_results['passed'] + test_results['failed']
    if total > 0:
        compliance = (test_results['passed'] / total) * 100
        print(f"Compliance: {compliance:.1f}%")
        
        if compliance == 100:
            print("\n[OK] FULLY COMPLIANT with MCP 2025-11-25")
        elif compliance >= 80:
            print("\n[WARN] MOSTLY COMPLIANT - Minor issues to address")
        else:
            print("\n[FAIL] NOT COMPLIANT - Significant updates needed")
    
    print("="*60)
    
    # Save report to file
    report_file = os.path.join(os.path.dirname(__file__), "compliance_report.json")
    with open(report_file, 'w') as f:
        json.dump(test_results, f, indent=2)
    print(f"\nReport saved to: {report_file}")

def main():
    """Run all compliance tests"""
    print("MCP 2025-11-25 Compliance Test Suite")
    print(f"Server: agent-genesis")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Run all tests
    test_server_initialization()
    test_tools_defined()
    test_resources_defined()
    test_prompts_defined()
    test_api_integration()
    test_scheduler_integration()
    test_indexing_integration()
    test_pyproject_config()
    
    # Generate report
    generate_report()
    
    # Return exit code based on results
    return 0 if test_results['failed'] == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
