#!/usr/bin/env python3
"""
Quick validation script to check the key improvements:
1. No "Demo@1234" password is hardcoded anywhere
2. Input clearing protection is in place
3. Fill action debugging is comprehensive
4. Login failure detection exists
"""

import os
import re
from pathlib import Path

def check_demo_password():
    """Check if Demo@1234 is hardcoded anywhere."""
    print("🔍 Checking for hardcoded 'Demo@1234' password...")
    
    agent_dir = Path(".")
    found_demo_password = False
    
    for py_file in agent_dir.glob("*.py"):
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if "Demo@1234" in content:
                    print(f"❌ Found 'Demo@1234' in {py_file}")
                    found_demo_password = True
        except Exception as e:
            print(f"⚠️ Could not read {py_file}: {e}")
    
    if not found_demo_password:
        print("✅ No hardcoded 'Demo@1234' found")
    
    return not found_demo_password

def check_input_clearing_protection():
    """Check if input clearing protection is implemented."""
    print("\n🛡️ Checking input clearing protection...")
    
    try:
        with open("main.py", 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check for protection conditions
        protection_checks = [
            "user_input_flow_active",
            "JOBS_IN_INPUT_FLOW",
            "should_clear_inputs",
            "waiting_for_user_input"
        ]
        
        missing_checks = []
        for check in protection_checks:
            if check not in content:
                missing_checks.append(check)
        
        if missing_checks:
            print(f"❌ Missing protection checks: {missing_checks}")
            return False
        else:
            print("✅ Input clearing protection implemented")
            return True
            
    except FileNotFoundError:
        print("❌ main.py not found")
        return False

def check_fill_debugging():
    """Check if fill action debugging is comprehensive."""
    print("\n🔍 Checking fill action debugging...")
    
    try:
        with open("main.py", 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check for debug features
        debug_features = [
            "FILL DEBUG",
            "Original fill_text",
            "user_input_response",
            "Final fill_text"
        ]
        
        missing_features = []
        for feature in debug_features:
            if feature not in content:
                missing_features.append(feature)
        
        if missing_features:
            print(f"❌ Missing debug features: {missing_features}")
            return False
        else:
            print("✅ Comprehensive fill debugging implemented")
            return True
            
    except FileNotFoundError:
        print("❌ main.py not found")
        return False

def check_login_failure_detection():
    """Check if login failure detection is implemented."""
    print("\n🚫 Checking login failure detection...")
    
    try:
        with open("main.py", 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check for login failure features
        failure_features = [
            "detect_login_failure",
            "LOGIN FAILURE DETECTED",
            "login_failure_detected",
            "failure_indicators"
        ]
        
        missing_features = []
        for feature in failure_features:
            if feature not in content:
                missing_features.append(feature)
        
        if missing_features:
            print(f"❌ Missing login failure features: {missing_features}")
            return False
        else:
            print("✅ Login failure detection implemented")
            return True
            
    except FileNotFoundError:
        print("❌ main.py not found")
        return False

def check_llm_prompt_improvements():
    """Check if LLM prompt has login failure handling."""
    print("\n🤖 Checking LLM prompt improvements...")
    
    try:
        with open("llm.py", 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check for login failure handling in prompts
        prompt_features = [
            "LOGIN FAILURE DETECTED",
            "LOGIN FAILURE HANDLING",
            "previous login failed",
            "request new credentials"
        ]
        
        missing_features = []
        for feature in prompt_features:
            if feature not in content:
                missing_features.append(feature)
        
        if missing_features:
            print(f"❌ Missing prompt features: {missing_features}")
            return False
        else:
            print("✅ LLM prompt improvements implemented")
            return True
            
    except FileNotFoundError:
        print("❌ llm.py not found")
        return False

def main():
    """Run all validation checks."""
    print("🔧 Validating Human-in-the-Loop Improvements")
    print("=" * 50)
    
    checks = [
        check_demo_password,
        check_input_clearing_protection,
        check_fill_debugging,
        check_login_failure_detection,
        check_llm_prompt_improvements
    ]
    
    results = []
    for check in checks:
        results.append(check())
    
    print("\n" + "=" * 50)
    print("📊 Validation Summary:")
    
    passed = sum(results)
    total = len(results)
    
    print(f"✅ Passed: {passed}/{total} checks")
    
    if passed == total:
        print("🎉 All improvements validated successfully!")
        print("\n💡 Next steps:")
        print("1. Test with actual website to verify debugging output")
        print("2. Check console logs for comprehensive debug information")
        print("3. Verify input protection works during user input flows")
        print("4. Test login failure detection and retry mechanism")
    else:
        print("❌ Some improvements need attention")
        print("Please review the failed checks above")
    
    return passed == total

if __name__ == "__main__":
    main()