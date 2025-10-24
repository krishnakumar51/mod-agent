import re
import json
import base64
from enum import Enum
from pathlib import Path
from typing import List, Union, Tuple, Dict

from config import (
    anthropic_client, groq_client, openai_client,
    ANTHROPIC_MODEL, GROQ_MODEL, OPENAI_MODEL
)

class LLMProvider(str, Enum):
    """Enumeration for the supported LLM providers."""
    ANTHROPIC = "anthropic"
    GROQ = "groq"
    OPENAI = "openai"

# --- PROMPT TEMPLATES ---

REFINER_PROMPT = """
Analyze the user's request and create a concise, actionable instruction for an AI web agent.
Focus on the ultimate goal.

User's Target URL: {url}
User's Query: "{query}"

Based on this, generate a single, clear instruction.
Example: "Find the top 5 smartphones under ₹50,000 on flipkart.com, collecting their name, price, and URL."
Refined Instruction:
"""

AGENT_PROMPT = """
You are an autonomous web agent with memory. Your goal is to achieve the user's objective by navigating and interacting with a web page.
You operate in a step-by-step manner. At each step, analyze the current state of the page (HTML and screenshot), review your past actions, and decide on the single best next action.

**User's Objective:** "{query}"
**Current URL:** {url}

**Recent Action History (Memory):**
{history}

**🎯 CRITICAL WORKFLOW - FOLLOW THIS EXACT ORDER:**

**PRIORITY 0: CAPTCHA CHECK (HIGHEST PRIORITY - Check history first!)**
└─ If history shows "🚨 CAPTCHA DETECTED":
   ├─ Stop everything else
   ├─ Use {{"type": "solve_captcha"}} action IMMEDIATELY
   ├─ Wait for CAPTCHA to be solved
   └─ Then continue with form submission or next step

**PRIORITY 1: CHECK FOR FOUND ELEMENTS**
└─ If your history shows "🎯 ELEMENT SEARCH RESULTS FROM PREVIOUS STEP":
   ├─ Extract the FIRST selector marked as "visible" and "interactive"
   ├─ Use it IMMEDIATELY with click/fill/press action
   ├─ DO NOT search again - selectors are already validated
   └─ Example: See "✅ Ready-to-use: #login-btn" → Use {{"type": "click", "selector": "#login-btn"}}

**PRIORITY 2: POP-UP CHECK**
└─ Examine screenshot for blocking pop-ups:
   ├─ LARGE pop-up with dark/blurred background → Use dismiss_popup_using_text
   ├─ Small corner banner → IGNORE, proceed with main task
   └─ No pop-up → Skip to Priority 3

**PRIORITY 3: PLAN YOUR ACTION**
└─ Before ANY click/fill/press action:
   ├─ Step A: Identify EXACT visible text of target element
   ├─ Step B: Use extract_correct_selector_using_text with that EXACT text
   ├─ Step C: WAIT for next step to receive validated selectors
   └─ Step D: In next step, use the selector from search results

**CRITICAL RULES:**
1. 🤖 **CAPTCHA HANDLING** (NEW!):
   - If you see "🚨 CAPTCHA DETECTED" in history → Use {{"type": "solve_captcha"}} IMMEDIATELY
   - NEVER try to click through CAPTCHAs - always solve them first
   - After solving CAPTCHA, wait one step before clicking submit
   - Example: {{"type": "solve_captcha"}}

2. 🔄 **Two-Step Interaction Pattern**:
   - Step N: Search → {{"type": "extract_correct_selector_using_text", "text": "Button Text"}}
   - Step N+1: Act → {{"type": "click", "selector": "#found-selector"}}
   
3. 🚫 **NEVER Guess Selectors**:
   - ❌ BAD: {{"type": "click", "selector": "button"}}
   - ✅ GOOD: First search, then use found selector
   
4. 🎯 **Use Found Elements Immediately**:
   - If history has search results → Use them, don't search again
   
5. 📝 **User Input - EXACT Values**:
   - History shows "🔑 USER PROVIDED PASSWORD: Abc123!" → Use EXACTLY "Abc123!"
   - NEVER generate fake passwords

**Available Tools (Action JSON format):**
-   {{"type": "solve_captcha"}}: **USE THIS when history shows CAPTCHA detected** - Automatically detects and solves any CAPTCHA type
-   {{"type": "extract_correct_selector_using_text", "text": "Exact button/link text"}}: Search for elements
-   {{"type": "click", "selector": "<css_selector>"}}: Click element
-   {{"type": "fill", "selector": "<css_selector>", "text": "<text>"}}: Fill input field
-   {{"type": "press", "selector": "<css_selector>", "key": "Enter"}}: Press key
-   {{"type": "scroll", "direction": "down"}}: Scroll page
-   {{"type": "extract", "items": [{{"title": "...", "price": "..."}}]}}: Extract data
-   {{"type": "finish", "reason": "<completion_summary>"}}: End task
-   {{"type": "dismiss_popup_using_text", "text": "<dismiss_button_text>"}}: Dismiss popups
-   {{"type": "request_user_input", "input_type": "password", "prompt": "...", "is_sensitive": true}}: Request input

**Response Format:**
You MUST respond with a single, valid JSON object containing "thought" and "action". Do NOT add any other text.

**Example 1 - CAPTCHA Detected (HIGHEST PRIORITY):**
```json
{{
    "thought": "History shows '🚨 CAPTCHA DETECTED: RECAPTCHA' from the previous step. The system detected a reCAPTCHA blocking the signup form. I must solve this CAPTCHA before attempting to click the 'Sign up' button. I'll use the solve_captcha action.",
    "action": {{"type": "solve_captcha"}}
}}
```

**Example 2 - After CAPTCHA Solved:**
```json
{{
    "thought": "Previous step shows 'CAPTCHA solved: recaptcha via capsolver'. The CAPTCHA has been successfully solved. Now I can proceed with clicking the 'Sign up' button. The selector '[data-testid=\"identity-form-submit-button\"]' was found earlier.",
    "action": {{"type": "click", "selector": "[data-testid=\"identity-form-submit-button\"]"}}
}}
```

**Example 3 - Using Found Elements:**
```json
{{
    "thought": "History shows element search found '#search-input' from previous step. It's marked as visible and interactive. I'll use it immediately.",
    "action": {{"type": "fill", "selector": "#search-input", "text": "smartphones"}}
}}
```

**Example 4 - First Interaction (Search Phase):**
```json
{{
    "thought": "I can see a 'Login' button but I don't have its selector yet. I must search first.",
    "action": {{"type": "extract_correct_selector_using_text", "text": "Login"}}
}}
```

**🚨 ABSOLUTE RULES:**
- If history shows CAPTCHA detected → solve_captcha action is MANDATORY
- NEVER click submit/signup buttons when CAPTCHA is visible
- NEVER try to bypass CAPTCHAs by clicking through them
- After solving CAPTCHA, wait one step before submitting form
- NEVER search twice for same element
- ALWAYS use exact text from screenshot in search

**Current Situation:**
{history}

Based on the HTML, screenshot, and history above, provide your thought and action as a JSON object.
"""

def get_refined_prompt(url: str, query: str, provider: LLMProvider) -> Tuple[str, Dict]:
    """Generates a refined, actionable prompt and returns the token usage."""
    prompt = REFINER_PROMPT.format(url=url, query=query)
    response_text, usage = get_llm_response("You are a helpful assistant.", prompt, provider, images=[])
    return response_text.strip(), usage

def build_enhanced_history(state) -> str:
    """
    🧠 Build smart history that prioritizes found elements and reduces redundancy
    """
    history_lines = []
    
    # 1. Add found element context at TOP (highest priority)
    if state.get('found_element_context'):
        ctx = state['found_element_context']
        history_lines.append("=" * 60)
        history_lines.append("🎯 ELEMENT SEARCH RESULTS FROM PREVIOUS STEP")
        history_lines.append("=" * 60)
        history_lines.append(f"Search Text: '{ctx['text']}'")
        history_lines.append(f"Total Matches: {ctx.get('total_matches', 0)}")
        
        if ctx.get('all_elements'):
            visible = [e for e in ctx['all_elements'] if e.get('is_visible')]
            interactive = [e for e in ctx['all_elements'] if e.get('is_interactive')]
            
            history_lines.append(f"Found: {len(visible)} visible, {len(interactive)} interactive elements")
            history_lines.append("")
            history_lines.append("📋 TOP MATCHES (Use these selectors):")
            
            for i, elem in enumerate(ctx['all_elements'][:3], 1):
                vis = "✅ VISIBLE" if elem.get('is_visible') else "❌ HIDDEN"
                inter = "🖱️ INTERACTIVE" if elem.get('is_interactive') else "📄 STATIC"
                
                history_lines.append(f"\n  [{i}] {elem['tag_name']} - {vis}, {inter}")
                
                if elem['suggested_selectors']:
                    best_selector = elem['suggested_selectors'][0]
                    history_lines.append(f"      ✅ Ready-to-use: {best_selector}")
                    history_lines.append(f"      Alternatives: {', '.join(elem['suggested_selectors'][1:3])}")
        
        history_lines.append("=" * 60)
        history_lines.append("")
    
    # 2. Add user input context (if available)
    if state.get('user_input_response'):
        input_type = state.get('user_input_request', {}).get('input_type', 'input')
        is_sensitive = state.get('user_input_request', {}).get('is_sensitive', False)
        
        if is_sensitive:
            # Show actual value for sensitive data so agent uses it
            history_lines.append("🔐 USER PROVIDED SENSITIVE DATA:")
            history_lines.append(f"   Type: {input_type.upper()}")
            history_lines.append(f"   Value: {state['user_input_response']}")
            history_lines.append(f"   ⚠️ USE THIS EXACT VALUE - DO NOT GENERATE FAKE DATA")
        else:
            history_lines.append(f"👤 USER PROVIDED {input_type.upper()}: {state['user_input_response']}")
        
        history_lines.append("")
    
    # 3. Add recent action history (last 10 only)
    if state.get('history'):
        history_lines.append("📜 RECENT ACTIONS:")
        recent = state['history'][-10:]
        for line in recent:
            history_lines.append(f"  {line}")
        history_lines.append("")
    
    # 4. Add failure warnings
    if state.get('failed_actions'):
        history_lines.append("⚠️ FAILED ACTIONS (DO NOT REPEAT):")
        failed_list = sorted(state['failed_actions'].items(), key=lambda x: -x[1])
        for sig, count in failed_list[:5]:
            history_lines.append(f"  ❌ {sig} (failed {count}x)")
        history_lines.append("")
    
    return "\n".join(history_lines)

def get_agent_action(query: str, url: str, html: str, provider: LLMProvider, screenshot_path: Union[Path, None], history: str) -> Tuple[dict, Dict]:
    """Gets the next thought and action from the agent, and returns token usage."""
    screenshot_note = ""
    if not screenshot_path:
        screenshot_note = "\n\n**⚠️ NOTE: Screenshot capture failed - relying on HTML content only.**"
    
    prompt = AGENT_PROMPT.format(query=query, url=url, history=history or "No actions taken yet.") + screenshot_note
    system_prompt = "You are an autonomous web agent. Respond ONLY with a JSON object containing 'thought' and 'action'. No other text."

    try:
        images = [screenshot_path] if screenshot_path else []
        response_text, usage = get_llm_response(system_prompt, prompt, provider, images=images)
        
        if not response_text or not response_text.strip():
            raise ValueError("Empty response from LLM")
        
        action = extract_json_from_response(response_text)
        
        if not isinstance(action, dict):
            raise ValueError("Response is not a dictionary")
        
        if "action" not in action:
            action["action"] = {"type": "finish", "reason": "No action specified"}
        
        return action, usage
        
    except Exception as e:
        print(f"Error in get_agent_action: {e}")
        error_action = {
            "thought": f"Error parsing model response: {str(e)}", 
            "action": {"type": "finish", "reason": f"Parsing failed: {str(e)}"}
        }
        error_usage = {"input_tokens": usage.get("input_tokens", 0) if 'usage' in locals() else 0, "output_tokens": 0} 
        return error_action, error_usage


def get_llm_response(
    system_prompt: str,
    prompt: str,
    provider: LLMProvider,
    images: List[Path]
) -> Tuple[str, Dict]:
    """Gets a response and token usage from the specified LLM provider."""
    usage = {"input_tokens": 0, "output_tokens": 0}
    
    if provider == LLMProvider.ANTHROPIC:
        if not anthropic_client: raise ValueError("Anthropic client not initialized.")
        
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]

        if images:
            last_image_path = images[-1]
            if last_image_path and last_image_path.exists() and last_image_path.stat().st_size > 0:
                try:
                    with open(last_image_path, "rb") as f: 
                        img_data = base64.b64encode(f.read()).decode("utf-8")
                        if img_data:
                            messages[0]["content"].append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_data}})
                except Exception as e:
                    print(f"Warning: Failed to read screenshot {last_image_path}: {e}")

        response = anthropic_client.messages.create(model=ANTHROPIC_MODEL, max_tokens=2048, system=system_prompt, messages=messages)
        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        
        if not response.content or len(response.content) == 0:
            return "", usage
        
        content = response.content[0]
        if hasattr(content, 'text'):
            return content.text or "", usage
        else:
            return str(content), usage

    elif provider == LLMProvider.OPENAI:
        if not openai_client: raise ValueError("OpenAI client not initialized.")
        
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
        for img_path in images:
            if img_path and img_path.exists() and img_path.stat().st_size > 0:
                try:
                    with open(img_path, "rb") as f: 
                        img_data = base64.b64encode(f.read()).decode("utf-8")
                        if img_data:
                            messages[0]["content"].append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_data}"}})
                except Exception as e:
                    print(f"Warning: Failed to read screenshot {img_path}: {e}")
        
        response = openai_client.chat.completions.create(model=OPENAI_MODEL, max_tokens=2048, messages=[{"role": "system", "content": system_prompt}, *messages])
        if response.usage:
            usage = {"input_tokens": response.usage.prompt_tokens, "output_tokens": response.usage.completion_tokens}
        
        content = response.choices[0].message.content
        return content or "", usage

    elif provider == LLMProvider.GROQ:
        if not groq_client: raise ValueError("Groq client not initialized.")
        if images: raise ValueError("The configured Groq model does not support vision.")

        response = groq_client.chat.completions.create(model=GROQ_MODEL, max_tokens=2048, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}])
        if response.usage:
             usage = {"input_tokens": response.usage.prompt_tokens, "output_tokens": response.usage.completion_tokens}
        
        content = response.choices[0].message.content
        return content or "", usage

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


def extract_json_from_response(text: str) -> Union[dict, list]:
    """Robustly extracts a JSON object or array from a string."""
    if not text or not text.strip():
        raise ValueError("Empty response from LLM")
    
    text = text.strip()
    
    # Remove markdown code blocks
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    
    patterns = [
        r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',
        r'\{.*?\}',
        r'\{.*\}',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                parsed = json.loads(match)
                if isinstance(parsed, dict) and ('thought' in parsed or 'action' in parsed):
                    return parsed
            except json.JSONDecodeError:
                continue
    
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            candidate = text[start:end+1]
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    
    if text:
        print(f"DEBUG: Failed to parse JSON from response: {text[:200]}...")
        return {
            "thought": f"Failed to parse response: {text[:100]}...",
            "action": {"type": "finish", "reason": "JSON parsing failed"}
        }
    
    raise ValueError("No valid JSON object found in the response.")