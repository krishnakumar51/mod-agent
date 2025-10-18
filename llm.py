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
Example: "Find the top 5 smartphones under â‚¹50,000 on flipkart.com, collecting their name, price, and URL."
Refined Instruction:
"""

AGENT_PROMPT = """
You are an autonomous web agent with memory. Your goal is to achieve the user's objective by navigating and interacting with a web page.
You operate in a step-by-step manner. At each step, analyze the current state of the page (HTML and screenshot), review your past actions, and decide on the single best next action.

**User's Objective:** "{query}"
**Current URL:** {url}

**Recent Action History (Memory):**
{history}


**Your Task:**
1.  **PRIORITY #1: POP-UP CHECK:** Before anything else, examine the screenshot for pop-ups, cookie banners, login modals, or any other interruptions. If you see one, your ONLY goal for this step is to dismiss it using the `dismiss_popup_using_text` tool. Look for buttons with text like "Accept", "Close", "Continue", "Got it", "Maybe later"..
2.  **Think:** If there are no pop-ups, analyze the situation. Also if there is a popup but that is small and not blocking the process then ignore it and don't try to close it. Only if the popup appears on the main content area and the background is blurred or dark then only consider closing it. Review your history. If your history shows "ðŸŽ¯ COMPREHENSIVE ELEMENT SEARCH RESULTS...", pay close attention to the provided selectors and use them immediately for interaction. If your last action failed, identify why and devise a new strategy. What is your immediate goal?
3.  **Act:** Choose ONE action from the available tools to move closer to the user's objective.

**Available Tools (Action JSON format):**
-   `{{"type": "fill", "selector": "<css_selector>", "text": "<text_to_fill>"}}`: To type in an input field.
-   `{{"type": "click", "selector": "<css_selector>"}}`: To click a button or link.
-   `{{"type": "press", "selector": "<css_selector>", "key": "<key_name>"}}`: To press a key (e.g., "Enter") on an element. **Hint: After filling a search bar, this is often more reliable than clicking a suggestion button.**
-   `{{"type": "scroll", "direction": "down"}}`: To scroll the page and reveal more content.
-   `{{"type": "extract", "items": [{{"title": "...", "price": "...", "url": "...", "snippet": "..."}}]}}`: To extract structured data from the CURRENT VIEW.
-   `{{"type": "finish", "reason": "<summary_of_completion>"}}`: To end the mission when the objective is fully met.
-   `{{"type": "dismiss_popup_using_text", "text": "<text_on_dismiss_button>"}}`: **(HIGH PRIORITY)** Use this first to dismiss any pop-ups or banners by clicking the element with the matching text.
-   `{{"type": "request_user_input", "input_type": "<text|password|otp|email|phone>", "prompt": "<descriptive_prompt_for_user>", "is_sensitive": <true|false>}}`: **Use this when you need user input** like login credentials, OTP codes, phone numbers, etc. The agent will pause and wait for user response.

**Magic Tools (Action JSON format):**
-   `{{"type": "extract_correct_selector_using_text", "text": "Exact text on button, div, span, link, etc."}}`: Use this to find the correct CSS selector for an element by its exact text content. This is useful when you know what you want to click but don't know or unable to find the selector. You should heavily rely on this tool when you think that the element you want to interact with is present on the page but you can't find its selector without using the above available tools.

**Response Format:**
You MUST respond with a single, valid JSON object containing "thought" and "action". Do NOT add any other text, explanations, or markdown.
Example Response for dismissing a pop-up:
```json
{{
    "thought": "The first thing I see is a large cookie consent banner blocking the page. I need to click the 'Accept All' button to continue.",
    "action": {{"type": "dismiss_popup_using_text", "text": "Accept All"}}
}}
Example Response for requesting user input:
```json
{{
    "thought": "I found a login form with username and password fields, but the user hasn't provided credentials in their query. I need to request this information from the user.",
    "action": {{"type": "request_user_input", "input_type": "email", "prompt": "Please provide your email address for login", "is_sensitive": false}}
}}
```

Example Response for using user input:
```json
{{
    "thought": "The user provided their email address: 'user@example.com'. Now I'll fill the username field with their exact input value.",
    "action": {{"type": "fill", "selector": "#username", "text": "user@example.com"}}
}}
```

**CRITICAL: When you see user input in your history like:**
- "ðŸ‘¤ USER PROVIDED EMAIL: user@example.com [Ready to use in next fill action]"
- "ðŸ” USER PROVIDED PASSWORD: [SENSITIVE DATA PROVIDED - Ready to use in next fill action]"

**ðŸš¨ ABSOLUTELY CRITICAL - USER INPUT USAGE RULE:**
When you see user-provided input in your history, you MUST extract and use the EXACT VALUE from your history text. 

**DO NOT GENERATE OR MAKE UP VALUES. ONLY USE WHAT THE USER ACTUALLY PROVIDED.**

**For sensitive data like passwords, look for the pattern in your history:**
- Search for "USER PROVIDED PASSWORD:" in your history
- If you see user input like "user_input_response: 'Pranavsurya@123'" in context
- Use that EXACT value "Pranavsurya@123", do NOT generate "Abcd@123456" or any other password

Example of CORRECT usage when user provided password "MySecret123":
```json
{{
    "thought": "I can see in the context that user_input_response is 'MySecret123'. I must use this exact password value, not generate my own.",
    "action": {{"type": "fill", "selector": "#password", "text": "MySecret123"}}
}}
```

Example of WRONG usage (NEVER DO THIS):
```json
{{
    "thought": "I need to fill a password field",
    "action": {{"type": "fill", "selector": "#password", "text": "Abcd@123456"}}
}}
```

**You MUST use the EXACT VALUE provided by the user, NOT any placeholders. For sensitive data, use the actual value even though it's hidden in the display.**

Example of CORRECT usage after user provides email "john@example.com":
```json
{{
    "thought": "I can see the user provided their email: john@example.com. I'll fill the email field with this exact value.",
    "action": {{"type": "fill", "selector": "#email", "text": "john@example.com"}}
}}
```

Example of WRONG usage (DO NOT DO THIS):
```json
{{
    "action": {{"type": "fill", "selector": "#email", "text": "{{USER_INPUT}}"}}
}}
```

**Current Situation Analysis:**
Based on the provided HTML, screenshot, and your recent history, what is your next thought and action?

**IMPORTANT NOTES:**
- Always use the magic tool `extract_correct_selector_using_text` first to find selectors for elements you want to interact with.
- If your history contains "ðŸŽ¯ IMPORTANT CONTEXT: Element found in previous step!", you MUST use the provided "Ready-to-use Selector" immediately with click, fill, or press action. Do NOT use extract_correct_selector_using_text again for the same element.
- When you see a suggested selector in your history (e.g., "ðŸ’¡ NEXT ACTION SUGGESTION: Use selector '...'"), follow that suggestion immediately.
- If the user wants to get any list of items (products, articles, etc.): then use only the search box on the website to search for the required items. Do not try to navigate using menus, categories, filters or click on any buttons etc. Just use the search box to search for the required items.
- Always try searching in the search box of any websites provided by the user. After searching if you get results then do not try to sort or filter the results. Just extract the required information from the results page or scroll down to load more results and then extract the required information.
- If you are able to extract the information without requiring any login, do not try to login or signup. But if you are not able to extract the information without login, then you can try to login or signup. The login or signup credentials will be provided by the user in the query. Do not try to login with any third party services like google, facebook, etc. Do not scroll down at this moment. If you are not able to find the required information without scrolling down, then you can try login or signup. After logging in or signing up, you can then scroll down to find the required information.
- **HUMAN INPUT SCENARIOS:** Use `request_user_input` when you encounter:
  - Login forms requiring username/password (not provided in query)
  - OTP/verification codes from SMS or email
  - Personal information like phone numbers, addresses
  - **LOGIN FAILURE RECOVERY:** If your history shows "ðŸš« LOGIN FAILURE DETECTED", the previous credentials were incorrect. Request new credentials with a message like "The previous login credentials were incorrect. Please provide the correct username/email and password."

**LOGIN FAILURE HANDLING:**
- If you see "ðŸš« LOGIN FAILURE DETECTED" in your history, this means the previous login attempt failed
- You should immediately request NEW credentials from the user using `request_user_input`
- Use clear prompts like: "The previous login failed. Please provide the correct email address" or "The previous password was incorrect. Please provide the correct password"
- Do NOT reuse credentials that have already failed - always request fresh ones
- After getting new credentials, retry the login process from the beginning
  - CAPTCHA solutions (ask user to solve)
  - Two-factor authentication codes
  - Any other information that only the user can provide
- The most important note is that you have to finish the task at any cost. Do not leave the task unfinished. If you are not able to find the required information, try to find the closest possible information and extract that.
- Do not try to overfetch or extract unnecessary information. Only extract what is required to fulfill the user's objective. If the required information is already extracted, use the finish action to complete the task.
- There is no scroll up action. You can only scroll down. So plan your actions accordingly.
- If any one selector is not working or the element is not found using that selector, then use the magic tool `extract_correct_selector_using_text` to find the correct selector for that element using its exact text content. Do not try to guess or modify the selector by yourself. And do not try to use any other selector from the history if that selector is not working. Always use the magic tool to find the correct selector.
"""

def get_refined_prompt(url: str, query: str, provider: LLMProvider) -> Tuple[str, Dict]:
    """Generates a refined, actionable prompt and returns the token usage."""
    prompt = REFINER_PROMPT.format(url=url, query=query)
    response_text, usage = get_llm_response("You are a helpful assistant.", prompt, provider, images=[])
    return response_text.strip(), usage

def get_agent_action(query: str, url: str, html: str, provider: LLMProvider, screenshot_path: Union[Path, None], history: str) -> Tuple[dict, Dict]:
    """Gets the next thought and action from the agent, and returns token usage."""
    # Add note about screenshot availability
    screenshot_note = ""
    if not screenshot_path:
        screenshot_note = "\n\n**âš ï¸ NOTE: Screenshot capture failed - relying on HTML content only for analysis.**"
    
    prompt = AGENT_PROMPT.format(query=query, url=url, history=history or "No actions taken yet.") + screenshot_note
    system_prompt = "You are an autonomous web agent. Respond ONLY with the JSON object containing your thought and action."

    try:
        images = [screenshot_path] if screenshot_path else []
        response_text, usage = get_llm_response(system_prompt, prompt, provider, images=images)
        
        if not response_text or not response_text.strip():
            raise ValueError("Empty response from LLM")
        
        action = extract_json_from_response(response_text)
        
        # Validate the action structure
        if not isinstance(action, dict):
            raise ValueError("Response is not a dictionary")
        
        if "action" not in action:
            # If no action field, try to construct one from the response
            action["action"] = {"type": "finish", "reason": "No action specified in response"}
        
        return action, usage
        
    except Exception as e:
        print(f"Error in get_agent_action: {e}")
        error_action = {
            "thought": f"Error: Could not parse a valid JSON action from the model's response. {str(e)}", 
            "action": {"type": "finish", "reason": f"Parsing failed: {str(e)}"}
        }
        # Return actual usage if available, otherwise zeros
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
        # for img_path in images:
        #     with open(img_path, "rb") as f: img_data = base64.b64encode(f.read()).decode("utf-8")
        #     messages[0]["content"].append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_data}})

        if images:
            last_image_path = images[-1]
            if last_image_path and last_image_path.exists() and last_image_path.stat().st_size > 0:
                try:
                    with open(last_image_path, "rb") as f: 
                        img_data = base64.b64encode(f.read()).decode("utf-8")
                        # Only add image if we have actual data
                        if img_data:
                            messages[0]["content"].append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_data}})
                except Exception as e:
                    print(f"Warning: Failed to read screenshot {last_image_path}: {e}")
                    # Continue without image

        response = anthropic_client.messages.create(model=ANTHROPIC_MODEL, max_tokens=2048, system=system_prompt, messages=messages)
        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        
        # Handle potential None response content
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
                    # Continue without image
        
        response = openai_client.chat.completions.create(model=OPENAI_MODEL, max_tokens=2048, messages=[{"role": "system", "content": system_prompt}, *messages])
        if response.usage:
            usage = {"input_tokens": response.usage.prompt_tokens, "output_tokens": response.usage.completion_tokens}
        
        # Handle potential None response content
        content = response.choices[0].message.content
        return content or "", usage

    elif provider == LLMProvider.GROQ:
        if not groq_client: raise ValueError("Groq client not initialized.")
        if images: raise ValueError("The configured Groq model does not support vision.")

        response = groq_client.chat.completions.create(model=GROQ_MODEL, max_tokens=2048, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}])
        if response.usage:
             usage = {"input_tokens": response.usage.prompt_tokens, "output_tokens": response.usage.completion_tokens}
        
        # Handle potential None response content
        content = response.choices[0].message.content
        return content or "", usage

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


def extract_json_from_response(text: str) -> Union[dict, list]:
    """Robustly extracts a JSON object or array from a string."""
    if not text or not text.strip():
        raise ValueError("Empty response from LLM")
    
    # Clean the text first
    text = text.strip()
    
    # Try multiple regex patterns to find JSON
    patterns = [
        r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',  # More specific nested JSON
        r'\{.*?\}',  # Non-greedy match
        r'\{.*\}',   # Original greedy match
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                parsed = json.loads(match)
                # Validate that it has the expected structure
                if isinstance(parsed, dict) and ('thought' in parsed or 'action' in parsed):
                    return parsed
            except json.JSONDecodeError:
                continue
    
    # If no valid JSON found, try to extract just the content between first { and last }
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
    
    # Last resort: try to create a basic structure from any text
    if text:
        print(f"DEBUG: Failed to parse JSON from response: {text[:200]}...")
        # Return a basic error structure
        return {
            "thought": f"Failed to parse response: {text[:100]}...",
            "action": {"type": "finish", "reason": "JSON parsing failed"}
        }
    
    raise ValueError("No valid JSON object found in the response.")