#!/usr/bin/env python3
"""
Test script to verify the debugging improvements and login failure detection.
This script will help us test:
1. Input clearing protection works correctly
2. Password debugging shows actual values being filled
3. Login failure detection works
4. Retry mechanism for failed logins
"""

import asyncio
import requests
import json
import time
from datetime import datetime

# Configuration
API_BASE = "http://localhost:8000"
TEST_TIMEOUT = 300  # 5 minutes

def print_with_timestamp(message):
    """Print message with timestamp for better debugging."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")

class TestDebugger:
    def __init__(self):
        self.job_id = None
        self.test_results = {}
        
    async def test_login_scenario_with_debugging(self):
        """Test a login scenario to verify our debugging improvements."""
        print_with_timestamp("🧪 Testing Login Scenario with Enhanced Debugging")
        
        # Test query that should trigger login
        query = "Go to https://www.facebook.com and login (will need credentials)"
        
        # Submit the job
        response = requests.post(f"{API_BASE}/submit", json={
            "query": query,
            "top_k": 3,
            "provider": "anthropic"
        })
        
        if response.status_code != 200:
            print_with_timestamp(f"❌ Failed to submit job: {response.text}")
            return False
            
        self.job_id = response.json()["job_id"]
        print_with_timestamp(f"✅ Job submitted: {self.job_id}")
        
        # Monitor the job and handle user input requests
        start_time = time.time()
        user_input_count = 0
        login_failure_detected = False
        
        while time.time() - start_time < TEST_TIMEOUT:
            # Check job status
            status_response = requests.get(f"{API_BASE}/status/{self.job_id}")
            if status_response.status_code != 200:
                continue
                
            status_data = status_response.json()
            
            # Look for specific events in the status messages
            for msg in status_data.get("messages", []):
                msg_type = msg.get("type")
                details = msg.get("details", {})
                
                if msg_type == "user_input_requested":
                    user_input_count += 1
                    input_type = details.get("input_type", "text")
                    prompt = details.get("prompt", "")
                    
                    print_with_timestamp(f"👤 User input requested #{user_input_count}")
                    print_with_timestamp(f"   Type: {input_type}")
                    print_with_timestamp(f"   Prompt: {prompt}")
                    
                    # Simulate user providing input
                    if "email" in prompt.lower() or input_type == "email":
                        test_value = "test.debug@example.com"
                    elif "password" in prompt.lower() or input_type == "password":
                        # Use different password for retry test
                        if "incorrect" in prompt.lower() or "failed" in prompt.lower():
                            test_value = "CorrectPassword123!"
                        else:
                            test_value = "TestPassword123"
                    else:
                        test_value = "test_input"
                    
                    # Submit the input
                    input_response = requests.post(f"{API_BASE}/submit_user_input", json={
                        "job_id": self.job_id,
                        "input_value": test_value
                    })
                    
                    if input_response.status_code == 200:
                        print_with_timestamp(f"✅ Submitted {input_type}: {test_value}")
                    else:
                        print_with_timestamp(f"❌ Failed to submit input: {input_response.text}")
                
                elif msg_type == "login_failure_detected":
                    login_failure_detected = True
                    failure_url = details.get("failure_url", "unknown")
                    step = details.get("step", "unknown")
                    print_with_timestamp(f"🚫 LOGIN FAILURE DETECTED at step {step}")
                    print_with_timestamp(f"   URL: {failure_url}")
                
                elif msg_type == "agent_finished":
                    reason = details.get("reason", "unknown")
                    print_with_timestamp(f"🏁 Agent finished: {reason}")
                    return self.analyze_results(user_input_count, login_failure_detected)
                
                elif msg_type == "agent_error":
                    error = details.get("error", "unknown")
                    print_with_timestamp(f"❌ Agent error: {error}")
                    return False
            
            await asyncio.sleep(2)
        
        print_with_timestamp("⏰ Test timed out")
        return False
    
    def analyze_results(self, user_input_count, login_failure_detected):
        """Analyze the test results."""
        print_with_timestamp("📊 Test Results Analysis:")
        print_with_timestamp(f"   User input requests: {user_input_count}")
        print_with_timestamp(f"   Login failure detected: {login_failure_detected}")
        
        # Check if our improvements are working
        success = True
        
        if user_input_count == 0:
            print_with_timestamp("❌ No user input requests - HITL not working")
            success = False
        else:
            print_with_timestamp("✅ User input requests working")
        
        # For a complete test, we'd want to see a login failure and retry
        if login_failure_detected:
            print_with_timestamp("✅ Login failure detection working")
        else:
            print_with_timestamp("⚠️ No login failure detected (may be normal)")
        
        return success
    
    def test_debugging_output(self):
        """Check console output for our debugging improvements."""
        print_with_timestamp("🔍 Testing Debug Output")
        print_with_timestamp("Check the console output for:")
        print_with_timestamp("  - 🔍 FILL DEBUG messages with actual values")
        print_with_timestamp("  - 🧹 INPUT CLEARING DEBUG messages")
        print_with_timestamp("  - 🤖 LLM RESPONSE DEBUG messages")
        print_with_timestamp("  - Protection mechanism status")
        return True

async def main():
    """Run the debug test suite."""
    print_with_timestamp("🚀 Starting Enhanced Debugging Test Suite")
    
    # Check if the server is running
    try:
        response = requests.get(f"{API_BASE}/health")
        if response.status_code != 200:
            print_with_timestamp("❌ Server not responding. Please start the agent server first.")
            return
    except requests.exceptions.ConnectionError:
        print_with_timestamp("❌ Cannot connect to server. Please start the agent server first.")
        return
    
    debugger = TestDebugger()
    
    # Test debug output
    debugger.test_debugging_output()
    
    # Test login scenario with debugging
    print_with_timestamp("\n" + "="*60)
    login_success = await debugger.test_login_scenario_with_debugging()
    
    # Summary
    print_with_timestamp("\n" + "="*60)
    print_with_timestamp("🎯 Test Summary:")
    if login_success:
        print_with_timestamp("✅ Login debugging test passed")
    else:
        print_with_timestamp("❌ Login debugging test failed")
    
    print_with_timestamp("\n💡 To verify improvements:")
    print_with_timestamp("1. Check console output for detailed debug messages")
    print_with_timestamp("2. Verify input fields are not cleared during user input")
    print_with_timestamp("3. Confirm actual password values are shown in debug logs")
    print_with_timestamp("4. Check login failure detection triggers retry requests")

if __name__ == "__main__":
    asyncio.run(main())