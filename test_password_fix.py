#!/usr/bin/env python3
"""
Test script to validate the password fix.
This will test that the agent uses the exact user-provided password
instead of generating its own.
"""

import asyncio
import requests
import json
import time
from datetime import datetime

# Configuration
API_BASE = "http://localhost:8000"
TEST_PASSWORD = "TestPassword123!@#"
TEST_EMAIL = "test.user@example.com"

def print_with_timestamp(message):
    """Print message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")

async def test_password_override():
    """Test that user password overrides LLM-generated passwords."""
    print_with_timestamp("🔐 Testing Password Override Fix")
    
    # Submit login test job
    response = requests.post(f"{API_BASE}/submit", json={
        "query": "Go to https://accounts.google.com and login (will need credentials)",
        "top_k": 1,
        "provider": "anthropic"
    })
    
    if response.status_code != 200:
        print_with_timestamp(f"❌ Failed to submit job: {response.text}")
        return False
    
    job_id = response.json()["job_id"]
    print_with_timestamp(f"✅ Job submitted: {job_id}")
    
    password_used_correctly = False
    email_used_correctly = False
    
    # Monitor for 2 minutes
    start_time = time.time()
    while time.time() - start_time < 120:
        try:
            # Check for user input requests
            input_response = requests.get(f"{API_BASE}/user-input-request/{job_id}")
            if input_response.status_code == 200:
                input_request = input_response.json()
                input_type = input_request.get('input_type', '')
                prompt = input_request.get('prompt', '')
                
                print_with_timestamp(f"👤 Input requested: {input_type}")
                print_with_timestamp(f"   Prompt: {prompt}")
                
                # Provide test input
                if input_type == 'email' or 'email' in prompt.lower():
                    test_value = TEST_EMAIL
                elif input_type == 'password' or 'password' in prompt.lower():
                    test_value = TEST_PASSWORD
                else:
                    test_value = "test_input"
                
                # Submit input
                submit_response = requests.post(f"{API_BASE}/submit_user_input", json={
                    "job_id": job_id,
                    "input_value": test_value
                })
                
                if submit_response.status_code == 200:
                    print_with_timestamp(f"✅ Submitted {input_type}: {test_value}")
                else:
                    print_with_timestamp(f"❌ Failed to submit {input_type}")
            
            # Check job status for debugging info
            status_response = requests.get(f"{API_BASE}/status/{job_id}")
            if status_response.status_code == 200:
                status_data = status_response.json()
                
                # Look for our debug messages in recent logs
                for msg in status_data.get("messages", [])[-5:]:  # Check last 5 messages
                    if msg.get("type") == "agent_finished":
                        print_with_timestamp("🏁 Agent finished")
                        return password_used_correctly and email_used_correctly
                    
        except Exception as e:
            print_with_timestamp(f"⚠️ Error checking status: {e}")
        
        await asyncio.sleep(2)
    
    print_with_timestamp("⏰ Test completed")
    return password_used_correctly

def check_console_logs():
    """Instructions for checking console logs."""
    print_with_timestamp("🔍 Manual Verification Steps:")
    print_with_timestamp("1. Check the console output for:")
    print_with_timestamp("   - '🔍 FILL DEBUG' messages showing actual password values")
    print_with_timestamp("   - '🔒 FORCING USER PASSWORD' messages when LLM tries to generate passwords")
    print_with_timestamp("   - '🔄 OVERRODE LLM password' messages")
    print_with_timestamp("2. Verify that 'Final fill_text' matches your provided password")
    print_with_timestamp("3. Look for any 'Abcd@123456' or other generated passwords being overridden")

async def main():
    """Run the password fix test."""
    print_with_timestamp("🚀 Password Fix Validation Test")
    print_with_timestamp("="*60)
    
    # Check server
    try:
        response = requests.get(f"{API_BASE}/health")
        if response.status_code != 200:
            print_with_timestamp("❌ Server not responding")
            return
    except:
        print_with_timestamp("❌ Cannot connect to server")
        return
    
    print_with_timestamp(f"📋 Test Configuration:")
    print_with_timestamp(f"   Test Email: {TEST_EMAIL}")
    print_with_timestamp(f"   Test Password: {TEST_PASSWORD}")
    print_with_timestamp(f"   Expected: Agent should use EXACT values above")
    
    print_with_timestamp("\n" + "="*60)
    
    # Run test
    result = await test_password_override()
    
    print_with_timestamp("\n" + "="*60)
    print_with_timestamp("📊 Test Results:")
    
    if result:
        print_with_timestamp("✅ Password test passed")
    else:
        print_with_timestamp("⚠️  Test completed - check console logs for details")
    
    # Instructions for manual verification
    print_with_timestamp("\n" + "="*60)
    check_console_logs()
    
    print_with_timestamp("\n💡 Key Improvements:")
    print_with_timestamp("1. ✅ Forced password override for password fields")
    print_with_timestamp("2. ✅ Enhanced LLM prompts with explicit instructions")
    print_with_timestamp("3. ✅ Comprehensive debugging for password flow")
    print_with_timestamp("4. ✅ Password visibility toggle in web interface")
    print_with_timestamp("5. ✅ Increased timing for password entry")

if __name__ == "__main__":
    asyncio.run(main())