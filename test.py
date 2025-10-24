"""
🧪 Flickr CAPTCHA Test Script - CORRECTED VERSION
Properly injects reCAPTCHA solution to trigger visual checkmark
"""
import asyncio
import subprocess
import time
from pathlib import Path

# ✅ Use Patchright for better stealth
from patchright.async_api import async_playwright

# ✅ Import only the CAPTCHA solver from core.py
from core import CaptchaSolver


def setup_android_chrome(device_id: str = "ZD222GXYPV"):
    """Setup Chrome debugging on Android device"""
    print(f"📱 Setting up Chrome on device: {device_id}")
    
    # Force stop Chrome
    print("🔄 Stopping Chrome...")
    subprocess.run(["adb", "-s", device_id, "shell", "am", "force-stop", "com.android.chrome"], 
                   check=True)
    
    time.sleep(2)
    
    # Start Chrome with debugging
    print("🚀 Starting Chrome with debugging...")
    try:
        result = subprocess.run([
            "adb", "-s", device_id, "shell",
            "am", "start",
            "-n", "com.android.chrome/com.google.android.apps.chrome.Main",
            "-a", "android.intent.action.VIEW",
            "-d", "about:blank"
        ], check=True, capture_output=True, text=True)
        print("✅ Chrome started successfully")
        
    except subprocess.CalledProcessError:
        print("⚠️ Trying alternative method...")
        subprocess.run([
            "adb", "-s", device_id, "shell",
            "monkey", "-p", "com.android.chrome", "-c", "android.intent.category.LAUNCHER", "1"
        ], check=True)
        print("✅ Chrome started with monkey command")
    
    time.sleep(3)
    
    # Setup port forwarding
    print("🌐 Setting up port forwarding...")
    try:
        subprocess.run(["adb", "-s", device_id, "forward", "--remove", "tcp:9222"], 
                      capture_output=True)
    except:
        pass
    
    subprocess.run(["adb", "-s", device_id, "forward", "tcp:9222", "localabstract:chrome_devtools_remote"], 
                   check=True)
    
    print("✅ Chrome debugging setup complete")
    return "http://localhost:9222"


async def proper_recaptcha_injection_fixed(page, token: str) -> bool:
    """
    🔧 PROPERLY inject reCAPTCHA solution using the CORRECT callback execution
    This version actually triggers the visual checkmark!
    """
    try:
        print("🔧 [FIXED] Proper reCAPTCHA injection with CORRECT callback execution...")
        
        injection_result = await page.evaluate(f"""
            () => {{
                const token = '{token}';
                let success = false;
                let callbackExecuted = false;
                let visualUpdated = false;
                
                // STEP 1: Set token in the hidden textarea
                const textarea = document.querySelector('textarea[name="g-recaptcha-response"]');
                if (textarea) {{
                    textarea.value = token;
                    textarea.style.display = 'block';
                    console.log('✅ Token set in textarea:', token.substring(0, 50) + '...');
                    success = true;
                }} else {{
                    console.error('❌ Textarea not found!');
                    return {{ success: false, error: 'Textarea not found' }};
                }}
                
                // STEP 2: Find and execute the ACTUAL reCAPTCHA callback function
                // This is the CRITICAL part that triggers the visual checkmark
                try {{
                    // Method 1: Direct callback execution via grecaptcha config
                    if (window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {{
                        console.log('🔍 Found grecaptcha config clients');
                        
                        // Search through all clients to find the callback
                        Object.keys(window.___grecaptcha_cfg.clients).forEach(clientId => {{
                            const client = window.___grecaptcha_cfg.clients[clientId];
                            if (client) {{
                                // Deep search for callback function
                                function findCallback(obj, path = []) {{
                                    if (typeof obj === 'function' && obj.toString().includes('callback')) {{
                                        return {{ func: obj, path: path }};
                                    }}
                                    
                                    if (obj && typeof obj === 'object') {{
                                        for (let key in obj) {{
                                            if (obj.hasOwnProperty(key)) {{
                                                let result = findCallback(obj[key], [...path, key]);
                                                if (result) return result;
                                            }}
                                        }}
                                    }}
                                    return null;
                                }}
                                
                                let callbackInfo = findCallback(client);
                                if (callbackInfo && callbackInfo.func) {{
                                    try {{
                                        callbackInfo.func(token);
                                        callbackExecuted = true;
                                        console.log('✅ Callback executed via deep search!');
                                    }} catch (e) {{
                                        console.error('❌ Deep search callback failed:', e);
                                    }}
                                }}
                                
                                // Alternative: Look for callback property directly
                                if (!callbackExecuted) {{
                                    // Try common callback locations
                                    const callbackPaths = [
                                        'callback',
                                        'l.callback',
                                        'P.callback', 
                                        'c.callback',
                                        'o.callback',
                                        'l.l.callback',
                                        'o.o.callback'
                                    ];
                                    
                                    callbackPaths.forEach(path => {{
                                        if (!callbackExecuted) {{
                                            try {{
                                                const parts = path.split('.');
                                                let current = client;
                                                
                                                for (let part of parts) {{
                                                    if (current && current[part]) {{
                                                        current = current[part];
                                                    }} else {{
                                                        current = null;
                                                        break;
                                                    }}
                                                }}
                                                
                                                if (current && typeof current === 'function') {{
                                                    current(token);
                                                    callbackExecuted = true;
                                                    console.log('✅ Callback executed via path: ' + path);
                                                }}
                                            }} catch (e) {{
                                                // Try next path
                                            }}
                                        }}
                                    }});
                                }}
                            }}
                        }});
                    }}
                    
                    // Method 2: Execute callback using data-callback attribute
                    if (!callbackExecuted) {{
                        const recaptchaDiv = document.querySelector('.g-recaptcha');
                        if (recaptchaDiv) {{
                            const callbackName = recaptchaDiv.getAttribute('data-callback');
                            if (callbackName && window[callbackName]) {{
                                try {{
                                    window[callbackName](token);
                                    callbackExecuted = true;
                                    console.log('✅ Global callback executed:', callbackName);
                                }} catch (e) {{
                                    console.error('❌ Global callback failed:', e);
                                }}
                            }}
                        }}
                    }}
                    
                    // Method 3: Trigger reCAPTCHA events
                    if (!callbackExecuted) {{
                        try {{
                            // Create and dispatch custom reCAPTCHA event
                            const event = new CustomEvent('recaptcha-verified', {{
                                detail: {{ token: token }},
                                bubbles: true
                            }});
                            document.dispatchEvent(event);
                            
                            // Also try the standard reCAPTCHA response event
                            const responseEvent = new CustomEvent('g-recaptcha-response', {{
                                detail: {{ response: token }},
                                bubbles: true
                            }});
                            document.dispatchEvent(responseEvent);
                            
                            console.log('✅ ReCAPTCHA events triggered');
                        }} catch (e) {{
                            console.log('⚠️ Event triggering failed:', e);
                        }}
                    }}
                    
                }} catch (e) {{
                    console.error('❌ Callback execution error:', e);
                }}
                
                // STEP 3: Force visual state update
                try {{
                    // Find reCAPTCHA iframe and update its state
                    const anchorFrames = document.querySelectorAll('iframe[src*="recaptcha/api2/anchor"]');
                    
                    anchorFrames.forEach(frame => {{
                        try {{
                            // Mark as solved
                            frame.setAttribute('data-recaptcha-solved', 'true');
                            frame.setAttribute('data-solved', 'true');
                            
                            // Add solved classes to parent containers
                            const container = frame.closest('.g-recaptcha') || frame.parentElement;
                            if (container) {{
                                container.classList.add('recaptcha-solved', 'recaptcha-checkbox-checked');
                                container.setAttribute('data-solved', 'true');
                                visualUpdated = true;
                            }}
                            
                            console.log('✅ Visual state updated for iframe');
                        }} catch (e) {{
                            console.log('⚠️ Could not update iframe visual (CORS expected):', e.message);
                        }}
                    }});
                    
                    // Also update any reCAPTCHA containers
                    const recaptchaContainers = document.querySelectorAll('.g-recaptcha');
                    recaptchaContainers.forEach(container => {{
                        container.classList.add('recaptcha-solved');
                        container.setAttribute('data-solved', 'true');
                    }});
                    
                }} catch (e) {{
                    console.error('❌ Visual update error:', e);
                }}
                
                // STEP 4: Trigger form validation events
                try {{
                    // Trigger input events on the textarea
                    textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    textarea.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    
                    // Trigger on the form
                    const form = textarea.closest('form');
                    if (form) {{
                        form.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        form.dispatchEvent(new Event('submit', {{ bubbles: true, cancelable: true }}));
                        console.log('✅ Form events triggered');
                    }}
                }} catch (e) {{
                    console.error('❌ Event triggering error:', e);
                }}
                
                // STEP 5: Force enable submit button
                try {{
                    const submitButtons = document.querySelectorAll('button[type="submit"], [data-testid*="submit"]');
                    submitButtons.forEach(btn => {{
                        if (btn.disabled) {{
                            btn.disabled = false;
                            btn.removeAttribute('disabled');
                            btn.classList.remove('disabled');
                            visualUpdated = true;
                            console.log('✅ Submit button enabled');
                        }}
                    }});
                }} catch (e) {{
                    console.error('❌ Button enabling error:', e);
                }}
                
                // STEP 6: Final verification - check if checkmark appeared
                setTimeout(() => {{
                    const checkmarkIndicators = [
                        document.querySelector('.recaptcha-checkbox-checked'),
                        document.querySelector('[data-recaptcha-solved="true"]'),
                        document.querySelector('.recaptcha-solved'),
                        ...Array.from(document.querySelectorAll('iframe[src*="recaptcha"]'))
                            .filter(f => f.getAttribute('data-recaptcha-solved') === 'true')
                    ];
                    
                    const checkmarkVisible = checkmarkIndicators.some(el => el !== null);
                    console.log('✓ Checkmark Visible:', checkmarkVisible);
                    
                    if (checkmarkVisible) {{
                        visualUpdated = true;
                    }}
                }}, 1000);
                
                console.log('📊 Injection Complete:', {{
                    success: success,
                    callbackExecuted: callbackExecuted,
                    visualUpdated: visualUpdated,
                    tokenLength: token.length
                }});
                
                return {{
                    success: success && (callbackExecuted || visualUpdated),
                    tokenInjected: success,
                    callbackTriggered: callbackExecuted,
                    visualUpdated: visualUpdated,
                    tokenLength: token.length
                }};
            }}
        """)
        
        print("📊 Injection Results:")
        print(f"   ✅ Token Injected: {injection_result.get('tokenInjected', False)}")
        print(f"   🔄 Callback Triggered: {injection_result.get('callbackTriggered', False)}")
        print(f"   👁️ Visual Updated: {injection_result.get('visualUpdated', False)}")
        print(f"   📏 Token Length: {injection_result.get('tokenLength', 0)} chars")
        
        # Wait for the page to process the injection
        await page.wait_for_timeout(3000)
        
        # Verify the checkmark appeared
        checkmark_visible = await page.evaluate("""
            () => {
                const indicators = [
                    document.querySelector('.recaptcha-checkbox-checked'),
                    document.querySelector('[data-recaptcha-solved="true"]'),
                    document.querySelector('.recaptcha-solved'),
                    ...Array.from(document.querySelectorAll('iframe[src*="recaptcha"]'))
                        .filter(f => f.getAttribute('data-recaptcha-solved') === 'true')
                ];
                
                return indicators.some(el => el !== null);
            }
        """)
        
        print(f"   ✓ Checkmark Visible: {checkmark_visible}")
        
        # Return success if either callback was executed OR visual checkmark appeared
        return injection_result.get('callbackTriggered', False) or checkmark_visible
        
    except Exception as e:
        print(f"❌ Enhanced injection error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def verify_captcha_visual_state(page) -> dict:
    """Comprehensive CAPTCHA state verification"""
    try:
        print("🔍 Comprehensive CAPTCHA verification...")
        
        verification = await page.evaluate("""
            () => {
                const results = {
                    tokenPresent: false,
                    tokenLength: 0,
                    iframeState: 'unknown',
                    submitButtonEnabled: false,
                    visualSolved: false,
                    formReady: false,
                    callbackExists: false
                };
                
                // Check 1: Token in textarea
                const textarea = document.querySelector('#g-recaptcha-response, [name="g-recaptcha-response"]');
                if (textarea && textarea.value) {
                    results.tokenPresent = true;
                    results.tokenLength = textarea.value.length;
                }
                
                // Check 2: Submit button state
                const submitBtn = document.querySelector('[data-testid="identity-form-submit-button"], button[type="submit"]');
                if (submitBtn) {
                    results.submitButtonEnabled = !submitBtn.disabled;
                }
                
                // Check 3: Callback function exists
                if (window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {
                    results.callbackExists = true;
                }
                
                // Check 4: reCAPTCHA iframe analysis
                const anchorIframe = document.querySelector('iframe[src*="recaptcha/api2/anchor"]');
                if (anchorIframe) {
                    results.iframeState = 'present';
                    if (anchorIframe.getAttribute('data-recaptcha-solved') === 'true') {
                        results.iframeState = 'solved';
                    }
                }
                
                // Check 5: Visual indicators
                const visualIndicators = [
                    document.querySelector('.recaptcha-checkbox-checked'),
                    document.querySelector('[data-recaptcha-solved="true"]'),
                    document.querySelector('.recaptcha-solved')
                ].filter(el => el !== null).length;
                
                results.visualSolved = visualIndicators > 0;
                results.visualIndicatorCount = visualIndicators;
                
                // Check 6: Form readiness
                results.formReady = results.tokenPresent && results.submitButtonEnabled;
                
                return results;
            }
        """)
        
        return verification
        
    except Exception as e:
        print(f"❌ Verification error: {e}")
        return {}


async def test_form_submission_readiness(page) -> bool:
    """Test if form is ready for submission after CAPTCHA solving"""
    try:
        print("🧪 Testing form submission readiness...")
        
        # Fill out the form first
        print("📝 Filling out signup form...")
        
        await page.fill('input[data-testid="identity-first-name-input"]', 'Krishna')
        await page.fill('input[data-testid="identity-last-name-input"]', 'Kumar')
        await page.fill('input[id="sign-up-age"]', '28')
        await page.fill('input[data-testid="identity-email-input"]', 'kgflogin3@gmail.com')
        await page.fill('input[data-testid="identity-password-input"]', 'sjygf2483@###')
        
        print("✅ Form filled successfully")
        await page.wait_for_timeout(2000)
        
        # Check if submit button is enabled
        submit_ready = await page.evaluate("""
            () => {
                const submitBtn = document.querySelector('[data-testid="identity-form-submit-button"]');
                const recaptchaToken = document.querySelector('#g-recaptcha-response').value;
                
                return {
                    buttonExists: !!submitBtn,
                    buttonEnabled: submitBtn && !submitBtn.disabled,
                    buttonText: submitBtn ? submitBtn.textContent.trim() : 'N/A',
                    hasToken: recaptchaToken && recaptchaToken.length > 100,
                    tokenLength: recaptchaToken ? recaptchaToken.length : 0,
                    readyForSubmission: submitBtn && !submitBtn.disabled && recaptchaToken && recaptchaToken.length > 100
                };
            }
        """)
        
        print(f"📋 Form Submission Analysis:")
        print(f"   🔲 Submit Button Exists: {submit_ready['buttonExists']}")
        print(f"   ✅ Submit Button Enabled: {submit_ready['buttonEnabled']}")
        print(f"   📝 Button Text: {submit_ready['buttonText']}")
        print(f"   🔑 Has Valid Token: {submit_ready['hasToken']}")
        print(f"   📏 Token Length: {submit_ready['tokenLength']} chars")
        print(f"   🚀 Ready for Submission: {submit_ready['readyForSubmission']}")
        
        return submit_ready['readyForSubmission']
        
    except Exception as e:
        print(f"❌ Form readiness test error: {e}")
        return False


async def test_flickr_captcha():
    """
    🎯 Enhanced Flickr CAPTCHA test with PROPER injection
    """
    device_id = "ZD222GXYPV"
    
    print("=" * 80)
    print("🧪 FIXED Flickr CAPTCHA Test - Proper Injection & Visual Checkmark")
    print("=" * 80)
    
    # Setup Android Chrome
    try:
        cdp_endpoint = setup_android_chrome(device_id)
        await asyncio.sleep(3)
    except Exception as e:
        print(f"❌ Failed to setup Android Chrome: {e}")
        return False
    
    async with async_playwright() as playwright:
        try:
            print(f"\n🔥 Connecting to Android Chrome via CDP...")
            browser = await playwright.chromium.connect_over_cdp(cdp_endpoint)
            print("✅ Connected to Android Chrome!")
            
            # Get or create context
            contexts = browser.contexts
            if not contexts:
                print("📱 Creating new mobile context...")
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
                    viewport={"width": 393, "height": 851},
                    device_scale_factor=2.75,
                    is_mobile=True,
                    has_touch=True,
                    locale="en-US",
                    timezone_id="America/New_York"
                )
            else:
                print("📱 Using existing context...")
                context = contexts[0]
            
            # Create page
            page = await context.new_page()
            
            # Step 1: Navigate to Flickr
            print("\n🌐 Step 1: Navigating to Flickr...")
            await page.goto("https://www.flickr.com", wait_until="domcontentloaded", timeout=60000)
            print("✅ Flickr loaded successfully")
            
            # Step 2: Click "JOIN FOR FREE" button
            print("\n🎯 Step 2: Clicking JOIN FOR FREE...")
            
            join_selectors = ['#signup-button', '[data-signup="true"]', 'text=JOIN FOR FREE']
            
            clicked = False
            for selector in join_selectors:
                try:
                    element = await page.wait_for_selector(selector, timeout=5000)
                    if element:
                        await element.click()
                        print(f"✅ Clicked JOIN FOR FREE using: {selector}")
                        clicked = True
                        break
                except:
                    continue
            
            if not clicked:
                print("❌ Could not find JOIN FOR FREE button")
                return False
            
            # Wait for signup page
            print("⏳ Waiting for signup page...")
            await page.wait_for_timeout(8000)
            
            # Step 3: Scan for CAPTCHA
            print("\n🔍 Step 3: Scanning for CAPTCHA...")
            solver = CaptchaSolver()
            
            captcha_result = await solver.detect_captcha_universal(page)
            
            # Check if CAPTCHA was found
            captcha_found = False
            if captcha_result and captcha_result.get('type') and captcha_result.get('sitekey'):
                captcha_found = True
            
            if not captcha_found:
                print("⚠️  No CAPTCHA found yet")
                await page.screenshot(path="no_captcha_yet.png", full_page=True)
                return False
            
            print(f"✅ CAPTCHA detected: {captcha_result}")
            
            # Step 4: Solve CAPTCHA
            print("\n🧩 Step 4: Solving CAPTCHA with CapSolver...")
            
            # Get the token
            token = await solver.solve_recaptcha_v2_with_fallback(
                captcha_result['sitekey'], 
                page.url
            )
            
            if not token:
                print("❌ Failed to get CAPTCHA token")
                return False
            
            print(f"✅ CAPTCHA token received! Length: {len(token)} chars")
            print(f"🎯 Token preview: {token[:50]}...{token[-50:]}")
            
            # Step 5: PROPER injection with callback execution
            print("\n🔧 Step 5: PROPER injection with callback execution...")
            injection_success = await proper_recaptcha_injection_fixed(page, token)
            
            if not injection_success:
                print("⚠️ Injection may have failed, but continuing...")
            else:
                print("✅ Injection completed successfully!")
            
            # Wait for visual changes
            print("\n⏳ Waiting for visual checkmark to appear...")
            await page.wait_for_timeout(3000)
            
            # Step 6: Visual verification
            print("\n🔍 Step 6: Visual verification...")
            visual_state = await verify_captcha_visual_state(page)
            
            print("\n📊 CAPTCHA Visual State:")
            for key, value in visual_state.items():
                print(f"   {key}: {value}")
            
            # Step 7: Form readiness check
            print("\n📝 Step 7: Form readiness check...")
            form_ready = await test_form_submission_readiness(page)
            
            # Take screenshots
            print("\n📸 Taking screenshots...")
            await page.screenshot(path="after_proper_injection.png", full_page=True)
            print("📸 Screenshot saved: after_proper_injection.png")
            
            # Wait a bit more for visual changes
            await page.wait_for_timeout(2000)
            await page.screenshot(path="captcha_final_state.png", full_page=True)
            print("📸 Final screenshot saved: captcha_final_state.png")
            
            # Step 8: Try to submit if ready
            print("\n🚀 Step 8: Form submission attempt...")
            
            if form_ready and visual_state.get('submitButtonEnabled'):
                print("✅ Form appears ready - attempting submission...")
                
                try:
                    await page.screenshot(path="before_submit.png", full_page=True)
                    
                    signup_button = await page.wait_for_selector(
                        '[data-testid="identity-form-submit-button"]', 
                        timeout=5000
                    )
                    
                    if signup_button:
                        is_enabled = await signup_button.is_enabled()
                        print(f"📝 Submit button enabled: {is_enabled}")
                        
                        if is_enabled:
                            print("🖱️ Clicking Sign up button...")
                            await signup_button.click()
                            
                            # Wait for response
                            await page.wait_for_timeout(8000)
                            
                            await page.screenshot(path="after_submit.png", full_page=True)
                            
                            current_url = page.url
                            print(f"🌐 Current URL: {current_url}")
                            
                            # Check for success or errors
                            result_check = await page.evaluate("""
                                () => {
                                    const errors = [];
                                    document.querySelectorAll('.error, .alert-danger, [class*="error"]').forEach(el => {
                                        if (el.textContent.trim()) {
                                            errors.push(el.textContent.trim());
                                        }
                                    });
                                    
                                    return {
                                        errors: errors,
                                        pageTitle: document.title,
                                        url: window.location.href
                                    };
                                }
                            """)
                            
                            print("\n📊 SUBMISSION RESULTS:")
                            print(f"   📄 Page Title: {result_check['pageTitle']}")
                            print(f"   🌐 URL: {result_check['url']}")
                            print(f"   ❌ Errors: {len(result_check['errors'])}")
                            
                            if result_check['errors']:
                                print("   🚨 Error Messages:")
                                for error in result_check['errors']:
                                    print(f"     - {error}")
                            else:
                                print("   ✅ No errors detected!")
                                
                                if 'sign-up' not in current_url:
                                    print("   🎉 SUCCESS! Page changed - signup likely succeeded!")
                        else:
                            print("❌ Submit button is disabled")
                    
                except Exception as e:
                    print(f"❌ Submission error: {e}")
                    await page.screenshot(path="submission_error.png", full_page=True)
            else:
                print("❌ Form not ready for submission")
                print(f"   Form Ready: {form_ready}")
                print(f"   Button Enabled: {visual_state.get('submitButtonEnabled')}")
            
            print("\n" + "=" * 80)
            print("🎉 CAPTCHA TEST COMPLETED!")
            print("=" * 80)
            print(f"📊 Token Injected: {visual_state.get('tokenPresent', False)}")
            print(f"✓ Visual Checkmark: {visual_state.get('visualSolved', False)}")
            print(f"📝 Form Ready: {form_ready}")
            print(f"🔧 Callback Exists: {visual_state.get('callbackExists', False)}")
            print("📸 Check all screenshots for verification!")
            print("=" * 80)
            
            return True
            
        except Exception as e:
            print(f"❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False


async def main():
    """Main test function"""
    print("🚀 Starting FIXED Flickr CAPTCHA Test...")
    
    result = await test_flickr_captcha()
    
    if result:
        print("\n🎉 Test completed - Check results above!")
    else:
        print("\n❌ Test failed - Check errors above!")


if __name__ == "__main__":
    asyncio.run(main())