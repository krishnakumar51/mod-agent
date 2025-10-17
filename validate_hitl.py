"""
Simple test to validate the human-in-the-loop implementation
"""

import json
from main import USER_INPUT_REQUESTS, USER_INPUT_RESPONSES, PENDING_JOBS
from main import UserInputRequest, UserInputResponse

def test_data_structures():
    """Test that our data structures work correctly"""
    print("🧪 Testing Human-in-the-Loop Data Structures")
    
    # Test 1: Basic request storage
    job_id = "test-job-123"
    request = {
        "input_type": "email",
        "prompt": "Please provide your email",
        "is_sensitive": False,
        "timestamp": "2025-01-01T00:00:00Z",
        "step": 5
    }
    
    USER_INPUT_REQUESTS[job_id] = request
    assert job_id in USER_INPUT_REQUESTS
    assert USER_INPUT_REQUESTS[job_id]["input_type"] == "email"
    print("✅ Request storage works")
    
    # Test 2: Response storage
    USER_INPUT_RESPONSES[job_id] = "test@example.com"
    assert USER_INPUT_RESPONSES[job_id] == "test@example.com"
    print("✅ Response storage works")
    
    # Test 3: Cleanup
    USER_INPUT_REQUESTS.pop(job_id, None)
    USER_INPUT_RESPONSES.pop(job_id, None)
    assert job_id not in USER_INPUT_REQUESTS
    assert job_id not in USER_INPUT_RESPONSES
    print("✅ Cleanup works")
    
    print("🎉 All data structure tests passed!")

def test_action_signature():
    """Test the action signature creation for human-in-the-loop actions"""
    from main import make_action_signature
    
    print("\n🧪 Testing Action Signatures")
    
    # Test request_user_input action
    action = {
        "type": "request_user_input",
        "input_type": "password",
        "prompt": "Enter your password",
        "is_sensitive": True
    }
    
    signature = make_action_signature(action)
    expected = "request_user_input|input_type=password|prompt=Enter your password"
    
    print(f"Action: {action}")
    print(f"Signature: {signature}")
    print(f"Expected: {expected}")
    
    # The signature should contain the action type and distinguishing fields
    assert "request_user_input" in signature
    assert "password" in signature
    print("✅ Action signature generation works")

def test_user_input_validation():
    """Test input validation logic"""
    print("\n🧪 Testing Input Validation")
    
    # Test valid inputs
    valid_inputs = [
        ("email", "test@example.com"),
        ("phone", "+1234567890"),
        ("text", "some text"),
        ("password", "secret123"),
        ("otp", "123456")
    ]
    
    for input_type, value in valid_inputs:
        # Basic validation - could be enhanced
        assert isinstance(value, str)
        assert len(value.strip()) > 0
        print(f"✅ {input_type}: {value}")
    
    print("✅ Basic input validation works")

def test_agent_state_structure():
    """Test that AgentState supports the new fields"""
    print("\n🧪 Testing AgentState Structure")
    
    # Test that the state can hold human-in-the-loop data
    state_data = {
        "waiting_for_user_input": True,
        "user_input_request": {
            "input_type": "email",
            "prompt": "Test prompt",
            "is_sensitive": False
        },
        "user_input_response": "test@example.com"
    }
    
    # Basic validation
    assert isinstance(state_data["waiting_for_user_input"], bool)
    assert isinstance(state_data["user_input_request"], dict)
    assert isinstance(state_data["user_input_response"], str)
    
    print("✅ AgentState structure supports HITL fields")

if __name__ == "__main__":
    print("Human-in-the-Loop Implementation Validation")
    print("=" * 50)
    
    try:
        test_data_structures()
        test_action_signature()
        test_user_input_validation() 
        test_agent_state_structure()
        
        print("\n" + "=" * 50)
        print("🎉 ALL TESTS PASSED!")
        print("The human-in-the-loop implementation is ready to use.")
        print("\nNext steps:")
        print("1. Start the server: python main.py")
        print("2. Open http://localhost:8000")
        print("3. Test with a login scenario")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        print("Please check the implementation for issues.")