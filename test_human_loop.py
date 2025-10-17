#!/usr/bin/env python3
"""
Test script for Human-in-the-loop functionality in the Web Agent

This script demonstrates how to test the human-in-the-loop mechanism by:
1. Starting a job that will require user input
2. Monitoring for user input requests
3. Simulating user responses

Usage:
1. Start the main server: python main.py
2. Run this test script in another terminal: python test_human_loop.py
"""

import asyncio
import aiohttp
import json
import time
from typing import Optional

class HumanLoopTester:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def start_job(self, url: str, query: str) -> str:
        """Start a scraping job that might require user input"""
        payload = {
            "url": url,
            "query": query,
            "top_k": 5,
            "llm_provider": "anthropic"
        }
        
        async with self.session.post(f"{self.base_url}/search", json=payload) as response:
            if response.status == 200:
                data = await response.json()
                return data["job_id"]
            else:
                raise Exception(f"Failed to start job: {response.status}")
    
    async def check_user_input_request(self, job_id: str) -> Optional[dict]:
        """Check if there's a pending user input request"""
        try:
            async with self.session.get(f"{self.base_url}/user-input-request/{job_id}") as response:
                if response.status == 200:
                    return await response.json()
                return None
        except Exception:
            return None
    
    async def submit_user_input(self, job_id: str, input_value: str) -> bool:
        """Submit user input response"""
        payload = {
            "job_id": job_id,
            "input_value": input_value
        }
        
        try:
            async with self.session.post(f"{self.base_url}/user-input-response", json=payload) as response:
                return response.status == 200
        except Exception:
            return False
    
    async def get_job_status(self, job_id: str) -> dict:
        """Get comprehensive job status"""
        async with self.session.get(f"{self.base_url}/jobs/{job_id}/status") as response:
            if response.status == 200:
                return await response.json()
            return {"error": f"Status {response.status}"}
    
    async def monitor_job_with_interaction(self, job_id: str, max_wait_time: int = 300):
        """Monitor a job and handle user input requests interactively"""
        print(f"Monitoring job {job_id} for user input requests...")
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            # Check for user input request
            input_request = await self.check_user_input_request(job_id)
            
            if input_request:
                print(f"\n🔔 USER INPUT REQUIRED:")
                print(f"   Type: {input_request.get('input_type', 'text')}")
                print(f"   Prompt: {input_request.get('prompt', 'Please provide input')}")
                print(f"   Sensitive: {input_request.get('is_sensitive', False)}")
                
                # Simulate user response (in real usage, this would be user input)
                user_input = self.get_simulated_user_input(input_request)
                
                if user_input:
                    print(f"   Submitting input: {'[HIDDEN]' if input_request.get('is_sensitive') else user_input}")
                    success = await self.submit_user_input(job_id, user_input)
                    
                    if success:
                        print("   ✅ Input submitted successfully!")
                    else:
                        print("   ❌ Failed to submit input")
                else:
                    print("   ⏭️ Skipping input (simulation)")
            
            # Check job status
            status = await self.get_job_status(job_id)
            
            if status.get("has_result") and not status.get("waiting_for_input"):
                print(f"\n✅ Job {job_id} completed!")
                if "result" in status:
                    result = status["result"]
                    print(f"   Status: {result.get('status', 'unknown')}")
                    if "results" in result:
                        print(f"   Items found: {len(result['results'])}")
                break
            
            await asyncio.sleep(2)  # Check every 2 seconds
        
        else:
            print(f"\n⏰ Timeout reached after {max_wait_time} seconds")
    
    def get_simulated_user_input(self, input_request: dict) -> str:
        """Generate simulated user input based on the request type"""
        input_type = input_request.get('input_type', 'text').lower()
        prompt = input_request.get('prompt', '').lower()
        
        # Simulate different types of input
        if 'username' in prompt or 'user' in prompt:
            return "test_user@example.com"
        elif 'password' in prompt or input_type == 'password':
            return "test_password123"
        elif 'email' in prompt or input_type == 'email':
            return "test@example.com"
        elif 'phone' in prompt or input_type == 'phone':
            return "+1234567890"
        elif 'otp' in prompt or 'verification' in prompt or input_type == 'otp':
            return "123456"
        else:
            return "test_input"

async def test_basic_functionality():
    """Test basic human-in-the-loop functionality"""
    async with HumanLoopTester() as tester:
        print("🚀 Testing Human-in-the-Loop Functionality")
        print("=" * 50)
        
        # Test with a site that commonly requires login
        test_cases = [
            {
                "name": "E-commerce with potential login",
                "url": "https://www.amazon.com",
                "query": "Find the latest iPhone prices and add to cart (may require login)"
            },
            {
                "name": "Social media login test",
                "url": "https://www.linkedin.com",
                "query": "Search for job postings in software engineering"
            }
        ]
        
        for i, test_case in enumerate(test_cases, 1):
            print(f"\n📋 Test Case {i}: {test_case['name']}")
            print(f"   URL: {test_case['url']}")
            print(f"   Query: {test_case['query']}")
            
            try:
                job_id = await tester.start_job(test_case['url'], test_case['query'])
                print(f"   Job ID: {job_id}")
                
                await tester.monitor_job_with_interaction(job_id, max_wait_time=180)
                
            except Exception as e:
                print(f"   ❌ Test failed: {str(e)}")
            
            print("-" * 50)

async def test_direct_input_simulation():
    """Test the input mechanism directly"""
    async with HumanLoopTester() as tester:
        print("\n🧪 Testing Direct Input Simulation")
        print("=" * 50)
        
        # Create a job that we expect to require input
        job_id = await tester.start_job(
            "https://accounts.google.com/signin",
            "Login to Google account with username: test@example.com and password: testpass123"
        )
        
        print(f"Started job: {job_id}")
        print("Waiting for user input request...")
        
        # Monitor for input requests for 60 seconds
        for i in range(30):  # 30 checks, 2 seconds apart = 60 seconds
            await asyncio.sleep(2)
            
            input_request = await tester.check_user_input_request(job_id)
            if input_request:
                print(f"Found input request: {input_request}")
                
                # Submit test input
                success = await tester.submit_user_input(job_id, "test@example.com")
                print(f"Input submission result: {success}")
                break
        else:
            print("No input request found within timeout period")

if __name__ == "__main__":
    print("Human-in-the-Loop Test Suite")
    print("Make sure the main server is running on http://localhost:8000")
    print()
    
    # Run tests
    asyncio.run(test_basic_functionality())
    
    print("\n" + "=" * 50)
    print("Testing complete! Check the server logs for detailed execution flow.")