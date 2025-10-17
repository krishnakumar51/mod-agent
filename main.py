import asyncio
import platform
import re
import uuid
import json
import time
import csv
from pathlib import Path
from urllib.parse import urljoin
import traceback
from typing import List, TypedDict, Dict, Any
import logger
import subprocess
from core import force_stop_chrome, forward_port, get_devtools_port, start_chrome_incognito, start_chrome_normal, wait_for_devtools
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright, Page, Browser
from playwright_stealth import Stealth
from undetected_playwright import Malenia
from PIL import Image
from langgraph.graph import StateGraph, END
from bs4 import BeautifulSoup

from llm import LLMProvider, get_refined_prompt, get_agent_action
from config import SCREENSHOTS_DIR, ANTHROPIC_MODEL, GROQ_MODEL, OPENAI_MODEL

# --- FastAPI App Initialization ---
app = FastAPI(title="LangGraph Web Agent with Memory")

# --- In-Memory Job Storage ---
JOB_QUEUES = {}
JOB_RESULTS = {}

# --- NEW: Token Cost Analysis Configuration ---
ANALYSIS_DIR = Path("analysis")
REPORT_CSV_FILE = Path("report.csv")


# Prices per 1 Million tokens
TOKEN_COSTS = {
    "anthropic": {
        "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
        "claude-3-5-haiku-20241022": {"input": 0.8, "output": 4.0},
        "claude-sonnet-4-5-20250929": {"input": 3.0, "output": 15.0},
        "claude-3-5-sonnet-20240620": {"input": 3.0, "output": 15.0}
    },
    "openai": {
        "gpt-4o": {"input": 5.0, "output": 15.0}
    },
    "groq": {
        "llama3-8b-8192": {"input": 0.05, "output": 0.10}
    }
}

MODEL_MAPPING = {
    LLMProvider.ANTHROPIC: ANTHROPIC_MODEL,
    LLMProvider.GROQ: GROQ_MODEL,
    LLMProvider.OPENAI: OPENAI_MODEL
}

# --- Helper Functions ---
def get_current_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def push_status(job_id: str, msg: str, details: dict = None):
    q = JOB_QUEUES.get(job_id)
    if q:
        entry = {"ts": get_current_timestamp(), "msg": msg}
        if details: entry["details"] = details
        q.put_nowait(entry)

def resize_image_if_needed(image_path: Path, max_dimension: int = 2000):
    try:
        with Image.open(image_path) as img:
            if max(img.size) > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
                img.save(image_path)
    except Exception as e:
        print(f"Warning: Could not resize image {image_path}. Error: {e}")

def find_elements_with_attribute_text_detailed(html: str, text: str) -> List[Dict[str, Any]]:
    
    if not html or not text:
        return []
        
    soup = BeautifulSoup(html, 'html.parser')
    matching_elements = []
    text_lower = text.lower()

    for element in soup.find_all(True):
        if not hasattr(element, 'attrs') or not element.attrs:
            continue
            
        matched_attributes = []
        
        for attr_name, attr_value in element.attrs.items():
            try:
                if attr_value is None:
                    continue
                    
                # Convert list attributes to string
                if isinstance(attr_value, list):
                    attr_value_str = ' '.join(str(v) for v in attr_value)
                else:
                    attr_value_str = str(attr_value)
                
                # Check for matches
                name_match = text_lower in attr_name.lower()
                value_match = text_lower in attr_value_str.lower()
                
                if name_match or value_match:
                    matched_attributes.append({
                        'name': attr_name,
                        'value': attr_value_str,
                        'name_match': name_match,
                        'value_match': value_match
                    })
                    
            except (AttributeError, TypeError):
                continue
        
        if matched_attributes:
            # Generate useful selectors
            selectors = []
            
            # ID selector
            if element.get('id'):
                selectors.append(f"#{element['id']}")
            
            # Class selector
            if element.get('class'):
                classes = element['class'] if isinstance(element['class'], list) else [element['class']]
                # Convert all class values to strings
                class_strings = [str(cls) for cls in classes]
                selectors.append(f".{'.'.join(class_strings)}")
            
            # Tag selector
            selectors.append(element.name)
            
            # Attribute selectors for matched attributes
            for attr in matched_attributes:
                if attr['name'] in ['id', 'class']:
                    continue  # Already handled above
                selectors.append(f"{element.name}[{attr['name']}*='{attr['value'][:20]}']")
            
            matching_elements.append({
                'element_html': str(element),
                'tag_name': element.name,
                'matched_attributes': matched_attributes,
                'suggested_selectors': selectors[:3],  # Top 3 most useful selectors
                'all_attributes': dict(element.attrs) if element.attrs else {}
            })

    return matching_elements

async def find_elements_with_text_live(page, text: str) -> List[Dict[str, Any]]:
    """
    Finds all elements on the LIVE page where any attribute name, value, or text content contains the given text.
    This function works with dynamically rendered elements and conditional content.
    
    Parameters:
        page: Playwright page object
        text (str): The text to search for (case-insensitive).

    Returns:
        List[Dict]: A list of dictionaries containing element info, selectors, and interaction capabilities.
    """
    if not text:
        return []
    
    # Escape the search text for JavaScript
    escaped_text = text.replace('"', '\\"')
    
    # JavaScript function to search for elements comprehensively with fuzzy matching
    js_search_script = f"""
    (function() {{
        const searchText = "{escaped_text}".toLowerCase();
        const results = [];
        
        function normalizeText(text) {{
            if (!text) return '';
            return text.toLowerCase()
                .replace(/[\\s_-]+/g, '')
                .replace(/[^a-z0-9]/g, '');
        }}
        
        function calculateMatchScore(searchNorm, targetNorm, originalTarget) {{
            let score = 0;
            
            if (targetNorm === searchNorm) {{
                score = 100;
            }} else if (targetNorm.startsWith(searchNorm)) {{
                score = 80;
            }} else if (targetNorm.includes(searchNorm)) {{
                score = 60;
            }} else if (targetNorm.endsWith(searchNorm)) {{
                score = 40;
            }} else {{
                return 0;
            }}
            
            if (targetNorm.length === searchNorm.length) {{
                score += 20;
            }}
            
            if (originalTarget.includes(' ') && searchText.includes(' ')) {{
                score += 10;
            }}
            
            return Math.min(score, 100);
        }}
        
        function generateSelector(element) {{
            const selectors = [];
            
            // ID selector (highest priority)
            if (element.id) {{
                selectors.push('#' + element.id);
            }}
            
            // Class selector
            if (element.className && typeof element.className === 'string') {{
                const classes = element.className.trim().split(/\\s+/).filter(c => c.length > 0);
                if (classes.length > 0) {{
                    selectors.push('.' + classes.join('.'));
                }}
            }}
            
            // Data attributes
            for (let attr of element.attributes) {{
                if (attr.name.startsWith('data-') && attr.value) {{
                    selectors.push(`[${{attr.name}}="${{attr.value}}"]`);
                }}
            }}
            
            // Specific attribute selectors
            ['name', 'type', 'role', 'aria-label'].forEach(attrName => {{
                const value = element.getAttribute(attrName);
                if (value) {{
                    selectors.push(`[${{attrName}}="${{value}}"]`);
                }}
            }});
            
            // Text-based selector (for unique text)
            const textContent = element.textContent?.trim();
            if (textContent && textContent.length > 0 && textContent.length < 50) {{
                selectors.push(`text="${{textContent}}"`);
                selectors.push(`:has-text("${{textContent}}")`);
            }}
            
            // Tag-based selector (lowest priority)
            selectors.push(element.tagName.toLowerCase());
            
            return selectors;
        }}
        
        function checkElement(element) {{
            const matches = [];
            const searchNormalized = normalizeText(searchText);
            
            // Check all attributes with fuzzy matching
            for (let attr of element.attributes) {{
                const attrNameNorm = normalizeText(attr.name);
                const attrValueNorm = normalizeText(attr.value);
                
                const nameScore = calculateMatchScore(searchNormalized, attrNameNorm, attr.name);
                const valueScore = calculateMatchScore(searchNormalized, attrValueNorm, attr.value);
                
                if (nameScore > 0 || valueScore > 0) {{
                    matches.push({{
                        type: 'attribute',
                        name: attr.name,
                        value: attr.value,
                        nameMatch: nameScore > 0,
                        valueMatch: valueScore > 0,
                        nameScore: nameScore,
                        valueScore: valueScore,
                        maxScore: Math.max(nameScore, valueScore)
                    }});
                }}
            }}
            
            // Check text content with fuzzy matching
            const textContent = element.textContent?.trim() || '';
            const innerText = element.innerText?.trim() || '';
            
            const textContentNorm = normalizeText(textContent);
            const textContentScore = calculateMatchScore(searchNormalized, textContentNorm, textContent);
            
            if (textContentScore > 0) {{
                matches.push({{
                    type: 'textContent',
                    value: textContent,
                    match: true,
                    score: textContentScore
                }});
            }}
            
            if (innerText !== textContent) {{
                const innerTextNorm = normalizeText(innerText);
                const innerTextScore = calculateMatchScore(searchNormalized, innerTextNorm, innerText);
                
                if (innerTextScore > 0) {{
                    matches.push({{
                        type: 'innerText', 
                        value: innerText,
                        match: true,
                        score: innerTextScore
                    }});
                }}
            }}
            
            // Check placeholder, value, and other common text properties with fuzzy matching
            ['placeholder', 'value', 'title', 'alt', 'aria-label'].forEach(prop => {{
                const value = element[prop] || element.getAttribute(prop);
                if (value) {{
                    const valueNorm = normalizeText(value);
                    const propScore = calculateMatchScore(searchNormalized, valueNorm, value);
                    
                    if (propScore > 0) {{
                        matches.push({{
                            type: 'property',
                            name: prop,
                            value: value,
                            match: true,
                            score: propScore
                        }});
                    }}
                }}
            }});
            
            return matches;
        }}
        
        // Get all elements in the document (including dynamically added ones)
        const allElements = document.querySelectorAll('*');
        
        allElements.forEach((element, index) => {{
            const matches = checkElement(element);
            
            if (matches.length > 0) {{
                const rect = element.getBoundingClientRect();
                const computedStyle = window.getComputedStyle(element);
                
                // Check visibility and interaction capabilities
                const isVisible = (
                    rect.width > 0 && 
                    rect.height > 0 && 
                    computedStyle.visibility !== 'hidden' && 
                    computedStyle.display !== 'none' &&
                    element.offsetParent !== null
                );
                
                const isInteractive = (
                    element.tagName.toLowerCase() in {{'button': 1, 'a': 1, 'input': 1, 'select': 1, 'textarea': 1}} ||
                    element.onclick !== null ||
                    element.getAttribute('onclick') ||
                    element.getAttribute('href') ||
                    computedStyle.cursor === 'pointer' ||
                    element.hasAttribute('tabindex')
                );
                
                const isClickable = (
                    isInteractive ||
                    element.addEventListener ||
                    computedStyle.pointerEvents !== 'none'
                );
                
                results.push({{
                    index: index,
                    tagName: element.tagName.toLowerCase(),
                    matches: matches,
                    selectors: generateSelector(element),
                    isVisible: isVisible,
                    isInteractive: isInteractive,
                    isClickable: isClickable,
                    position: {{
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height)
                    }},
                    styles: {{
                        display: computedStyle.display,
                        visibility: computedStyle.visibility,
                        cursor: computedStyle.cursor,
                        pointerEvents: computedStyle.pointerEvents
                    }},
                    textContent: element.textContent?.trim()?.substring(0, 100) || '',
                    innerHTML: element.innerHTML?.substring(0, 200) || '',
                    outerHTML: element.outerHTML?.substring(0, 300) || ''
                }});
            }}
        }});
        
        // Sort by relevance (visible and interactive elements first, then by match quality)
        results.sort((a, b) => {{
            const maxMatchScoreA = Math.max(...a.matches.map(m => m.score || 0), 0);
            const maxMatchScoreB = Math.max(...b.matches.map(m => m.score || 0), 0);
            
            const scoreA = (a.isVisible ? 10 : 0) + (a.isInteractive ? 5 : 0) + (a.isClickable ? 3 : 0) + (maxMatchScoreA / 10);
            const scoreB = (b.isVisible ? 10 : 0) + (b.isInteractive ? 5 : 0) + (b.isClickable ? 3 : 0) + (maxMatchScoreB / 10);
            return scoreB - scoreA;
        }});
        
        return results;
    }})();
    """
    
    try:
        # Execute the JavaScript and get results
        results = await page.evaluate(js_search_script)
        
        # Process and enhance results
        processed_results = []
        for result in results:
            # Calculate priority score with match scores
            priority_score = 0
            if result['isVisible']:
                priority_score += 10
            if result['isInteractive']:
                priority_score += 5
            if result['isClickable']:
                priority_score += 3
            
            # Add match quality score (scale down from 0-100 to 0-10 range)
            match_scores = [match.get('score', 0) for match in result['matches']]
            max_match_score = max(match_scores) if match_scores else 0
            priority_score += max_match_score / 10  # 100 -> 10, 80 -> 8, 60 -> 6
            
            # Determine interaction capabilities
            interaction_methods = []
            if result['isClickable']:
                interaction_methods.append('click')
            if result['tagName'] in ['input', 'textarea']:
                interaction_methods.append('fill')
                interaction_methods.append('press')
            if result['tagName'] == 'select':
                interaction_methods.append('selectOption')
            
            processed_result = {
                'element_index': result['index'],
                'tag_name': result['tagName'],
                'matches': result['matches'],
                'suggested_selectors': result['selectors'][:5],  # Top 5 selectors
                'is_visible': result['isVisible'],
                'is_interactive': result['isInteractive'],
                'is_clickable': result['isClickable'],
                'position': result['position'],
                'styles': result['styles'],
                'interaction_methods': interaction_methods,
                'text_content': result['textContent'],
                'inner_html': result['innerHTML'],
                'outer_html': result['outerHTML'],
                'priority_score': priority_score,
                'element_summary': f"{result['tagName']} ({'visible' if result['isVisible'] else 'hidden'}, {'interactive' if result['isInteractive'] else 'static'}) - {len(result['matches'])} matches",
                'all_attributes': {}  # Keep compatibility with existing code
            }
            processed_results.append(processed_result)
        
        return processed_results
        
    except Exception as e:
        print(f"Error in live element search: {e}")
        return []

# --- NEW: Cost Analysis Function ---
def save_analysis_report(analysis_data: dict):
    """Calculates final costs, saves a detailed JSON report, and appends to a summary CSV."""
    job_id = analysis_data["job_id"]
    provider = analysis_data["provider"]
    model = analysis_data["model"]
    
    total_input = 0
    total_output = 0
    
    for step in analysis_data["steps"]:
        total_input += step.get("input_tokens", 0)
        total_output += step.get("output_tokens", 0)

    analysis_data["total_input_tokens"] = total_input
    analysis_data["total_output_tokens"] = total_output

    cost_info = TOKEN_COSTS.get(provider, {}).get(model)
    # --- MODIFIED: Add a more robust fallback for different Anthropic model names ---
    if not cost_info and provider == "anthropic":
        model_name_lower = model.lower()
        if "sonnet" in model_name_lower:
            # Default to the latest Sonnet pricing if a specific version isn't matched
            cost_info = TOKEN_COSTS.get("anthropic", {}).get("claude-3.5-sonnet-20240620")
        elif "haiku" in model_name_lower:
            cost_info = TOKEN_COSTS.get("anthropic", {}).get("claude-3-haiku-20240307")


    total_cost = 0.0
    if cost_info:
        input_cost = (total_input / 1_000_000) * cost_info["input"]
        output_cost = (total_output / 1_000_000) * cost_info["output"]
        total_cost = input_cost + output_cost
    
    # Format the cost to a string with 5 decimal places to ensure precision in output files.
    total_cost_usd_str = f"{total_cost:.5f}"
    analysis_data["total_cost_usd"] = total_cost_usd_str

    # 1. Save detailed JSON report in analysis/ directory
    try:
        ANALYSIS_DIR.mkdir(exist_ok=True)
        json_report_path = ANALYSIS_DIR / f"{job_id}.json"
        with open(json_report_path, 'w') as f:
            json.dump(analysis_data, f, indent=2)
    except Exception as e:
        print(f"Error saving JSON analysis report for job {job_id}: {e}")

    # 2. Append summary to report.csv
    try:
        file_exists = REPORT_CSV_FILE.is_file()
        with open(REPORT_CSV_FILE, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            header = ['job_id', 'total_input_tokens', 'total_output_tokens', 'total_cost_usd']
            if not file_exists:
                writer.writerow(header)
            
            row = [job_id, total_input, total_output, total_cost_usd_str]
            writer.writerow(row)
    except Exception as e:
        print(f"Error updating CSV report: {e}")


# --- API Models ---
class SearchRequest(BaseModel):
    url: str
    query: str
    top_k: int
    llm_provider: LLMProvider = LLMProvider.ANTHROPIC

# --- LangGraph Agent State with Memory ---
class AgentState(TypedDict):
    job_id: str
    browser: Browser
    page: Page
    query: str
    top_k: int
    provider: LLMProvider
    refined_query: str
    results: List[dict]
    screenshots: List[str]
    job_artifacts_dir: Path
    step: int
    max_steps: int
    last_action: dict
    history: List[str] 
    token_usage: List[dict] # NEW: To store token usage per step
    found_element_context: dict # NEW: To store context about found elements
    failed_actions: Dict[str, int] # NEW: signature -> failure count
    attempted_action_signatures: List[str] # NEW: chronological list of attempted signatures

# --- NEW: Stable action signature builder ---
def make_action_signature(action: dict) -> str:
    """Create a normalized signature for an agent action to detect repeats.

    Includes the action type plus distinguishing fields if present.
    Falls back to 'invalid' if the structure is unexpected.
    """
    if not isinstance(action, dict) or not action:
        return "invalid"
    parts = [action.get("type", "")]
    for key in ("selector", "text", "key"):
        val = action.get(key)
        if isinstance(val, str) and val.strip():
            # Truncate very long values to keep signature compact
            truncated = val.strip()
            if len(truncated) > 80:
                truncated = truncated[:77] + "..."
            parts.append(f"{key}={truncated}")
    return "|".join(parts) or "invalid"

# --- LangGraph Nodes ---
async def navigate_to_page(state: AgentState) -> AgentState:
    try:
        await state['page'].goto(state['query'], wait_until='domcontentloaded', timeout=60000)
        push_status(state['job_id'], "navigation_complete", {"url": state['query']})
    except Exception as e:
        push_status(state['job_id'], "navigation_failed", {"url": state['query'], "error": str(e)})
        print(f"Navigation failed: {e}")
        # Still continue with the process even if navigation partially fails
    
    try:
        inputs = await state['page'].query_selector_all('input')
        for inp in inputs:
            try:
                if await inp.is_enabled() and await inp.is_visible():
                    await inp.fill("")
            except Exception as e:
                print(f"Failed to clear input field: {e}")
    except Exception as e:
        print(f"Failed to clear input fields: {e}")

    return state

async def agent_reasoning_node(state: AgentState) -> AgentState:
    job_id = state['job_id']
    push_status(job_id, "agent_step", {"step": state['step'], "max_steps": state['max_steps']})
    
    screenshot_path = state['job_artifacts_dir'] / f"{state['step']:02d}_step.png"
    screenshot_success = False
    
    try:
        # Multiple attempts to ensure page is ready for screenshot
        try:
            # First try: wait for network to be idle (basic loading complete)
            await state['page'].wait_for_load_state('networkidle', timeout=3000)
        except:
            try:
                # Second try: wait for DOM content to be loaded
                await state['page'].wait_for_load_state('domcontentloaded', timeout=2000)
            except:
                # Third try: just wait a bit for any pending operations
                await asyncio.sleep(1)
        
        # Take screenshot with reasonable timeout
        await state['page'].screenshot(path=screenshot_path, timeout=20000, full_page=False)  # 20 second timeout, not full page
        resize_image_if_needed(screenshot_path)
        screenshot_success = True
        state['screenshots'].append(f"screenshots/{job_id}/{state['step']:02d}_step.png")
        print(f"Screenshot saved: {screenshot_path}")
    except Exception as e:
        # If screenshot fails, do not create placeholder file to avoid empty image errors
        push_status(job_id, "screenshot_failed", {"error": str(e), "step": state['step']})
        print(f"Screenshot failed at step {state['step']}: {e}")
        # Don't add to screenshots list if it failed - this prevents sending empty images to API
        screenshot_path = None

    # Enhanced history formatting with element context
    history_text = "\n".join(state['history'])

    # --- NEW: Inject anti-repeat guidance if we have failed actions ---
    if state.get('failed_actions'):
        failed_list = sorted(state['failed_actions'].items(), key=lambda x: -x[1])
        history_text += "\n\n⚠ FAILED ACTION SIGNATURES (Do NOT repeat exactly):"
        for sig, count in failed_list[:8]:  # show top 8 failures
            history_text += f"\n  - {sig} (failures={count})"
        history_text += ("\n🔒 RULE: Never emit an action with an identical signature to one that failed. "
                         "Change selector, vary interaction type, or choose a different target. "
                         "Consider scrolling, waiting, broader search, or finishing if stuck.")
        if len(failed_list) > 8:
            history_text += f"\n  ... {len(failed_list) - 8} more failed signatures tracked"
    
    # Add found element context to the history if available
    if state.get('found_element_context'):
        element_ctx = state['found_element_context']
        history_text += f"\n\n🎯 ELEMENT SEARCH RESULTS FROM PREVIOUS STEP:"
        history_text += f"\n• Search Text: '{element_ctx['text']}'"
        history_text += f"\n• Total Matches Found: {element_ctx.get('total_matches', 0)}"
        
        # Add information about the top 5 found elements with their selectors
        if element_ctx.get('all_elements'):
            visible_elements = [e for e in element_ctx['all_elements'] if e.get('is_visible')]
            interactive_elements = [e for e in element_ctx['all_elements'] if e.get('is_interactive')]
            
            history_text += f"\n• Found Elements: {len(visible_elements)} visible, {len(interactive_elements)} interactive out of {len(element_ctx['all_elements'])} total"
            
            for elem in element_ctx['all_elements']:
                visibility_indicator = "👁️ VISIBLE" if elem.get('is_visible') else "👻 HIDDEN"
                interactive_indicator = "🖱️ INTERACTIVE" if elem.get('is_interactive') else "📄 STATIC"
                
                history_text += f"\n  Element {elem['index']}: {elem['tag_name']}"
                history_text += f"\n    Status: {visibility_indicator} | {interactive_indicator}"
                history_text += f"\n    Selectors: {', '.join(elem['suggested_selectors'])}"
        
        # Add all available selectors
        if element_ctx.get('all_suggested_selectors'):
            history_text += f"\n• Available Selectors: {', '.join(element_ctx['all_suggested_selectors'])}"
        
        # Debug output
        print(f"🤖 ELEMENT CONTEXT FOR AGENT:")
        print(f"   Search Text: '{element_ctx['text']}'")
        print(f"   Total Matches: {element_ctx.get('total_matches', 0)}")
        print(f"   Available Selectors: {len(element_ctx.get('all_suggested_selectors', []))}")
    
    # MODIFIED: Capture token usage from agent action with error handling
    try:
        # Only pass screenshot if it was successfully taken
        images_to_send = [screenshot_path] if screenshot_path and screenshot_success else []
        
        action_response, usage = get_agent_action(
            query=state['refined_query'],
            url= state['page'].url,
            html= await state['page'].content(),
            provider=state['provider'],
            screenshot_path=screenshot_path if screenshot_success else None,
            history=history_text
        )
        
        # NEW: Store usage for this step
        state['token_usage'].append({
            "task": f"agent_step_{state['step']}",
            **usage
        })

        push_status(job_id, "agent_thought", {
            "thought": action_response.get("thought", "No thought provided."),
            "usage": usage
        })
        
        # Validate that we have a proper action
        if not action_response or not isinstance(action_response, dict):
            raise ValueError("Invalid action response format")
            
        action = action_response.get("action")
        if not action or not isinstance(action, dict) or not action.get("type"):
            raise ValueError("Missing or invalid action in response")
            
        state['last_action'] = action
        
    except Exception as e:
        # Handle LLM parsing errors gracefully
        error_msg = f"Failed to get agent action: {str(e)}"
        push_status(job_id, "agent_error", {"error": error_msg, "step": state['step']})
        print(f"Agent reasoning error at step {state['step']}: {error_msg}")
        
        # Provide a default action to continue or finish
        state['last_action'] = {
            "type": "finish", 
            "reason": f"Agent reasoning failed: {error_msg}"
        }
        
        # Still record some usage info if available
        state['token_usage'].append({
            "task": f"agent_step_{state['step']}_failed",
            "input_tokens": 0,
            "output_tokens": 0,
            "error": error_msg
        })
    
    # Clear found element context after agent has processed it
    if state.get('found_element_context'):
        state['found_element_context'] = {}
    
    return state

async def execute_action_node(state: AgentState) -> AgentState:

    """
    Open any website on Android device using Chrome.
    
    Args:
        device_id (str): Android device ID
        url (str): Website URL to open
        incognito (bool): Use incognito mode (default: True)
        wait_time (int): Time to wait after opening URL (seconds)
        take_screenshot (bool): Take screenshot after loading
        custom_actions (function): Optional custom function to perform actions on the page
    
    Returns:
        dict: Result containing status, page info, and any extracted data
    """

    job_id = state['job_id']
    action = state['last_action']
    page = state['page']
    
    # --- NEW: Build signature & skip if previously failed ---
    action_signature = make_action_signature(action)
    state['attempted_action_signatures'].append(action_signature)

    if action_signature in state.get('failed_actions', {}):
        state['history'].append(
            f"Step {state['step']}: ⏭ Skipped previously failed action `{action_signature}`. Choosing alternative."
        )
        push_status(job_id, "duplicate_action_skipped", {"signature": action_signature})
        # Record token usage placeholder for skipped action (keeps timeline consistent)
        state['token_usage'].append({
            "task": f"action_skip_{state['step']}",
            "input_tokens": 0,
            "output_tokens": 0,
            "skipped_signature": action_signature
        })
        state['step'] += 1
        return state

    push_status(job_id, "executing_action", {"action": action, "signature": action_signature})
    
    try:
        action_type = action.get("type")
        if action_type == "click":
            await page.locator(action["selector"]).click(timeout=2000)
        elif action_type == "fill":
            await page.locator(action["selector"]).fill(action["text"], timeout=7000)
        elif action_type == "press":
            await page.locator(action["selector"]).press(action["key"],timeout=2000)
        elif action_type == "scroll":
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
        elif action_type == "extract":
            items = action.get("items", [])
            for item in items:
                if 'url' in item and isinstance(item.get('url'), str):
                    item['url'] = urljoin(page.url, item['url'])
            state['results'].extend(items)
            push_status(job_id, "partial_result", {"new_items_found": len(items), "total_items": len(state['results'])})
        elif action_type == "dismiss_popup_using_text":
            search_text = action.get("text", "")
            if not search_text: raise ValueError("No text provided for dismiss_popup_using_text action")
            elements = await find_elements_with_text_live(page, search_text)
            target_element = next((el for el in elements if el.get('is_visible') and el.get('is_clickable')), None)
            
            if target_element and target_element.get('suggested_selectors'):
                selector_to_try = target_element['suggested_selectors'][0]
                await page.locator(selector_to_try).click(timeout=5000)
                state['history'].append(f"Step {state['step']}: ✅ Dismissed pop-up by clicking element with text '{search_text}' using selector '{selector_to_try}'")
            else:
                raise ValueError(f"Could not find a clickable element with text '{search_text}' to dismiss pop-up.")
        
        
        elif action_type == "extract_correct_selector_using_text":
            search_text = action.get("text", "")
            if not search_text:
                state['history'].append(f"Step {state['step']}: ❌ FAILED - No text provided for element search")
                raise ValueError("No text provided for extract_correct_selector_using_text action")
            
            # Use live search to find elements in the current DOM (including dynamic content)
            result = await find_elements_with_text_live(page, search_text)

            if result:
                # Store only the TOP 10 matched elements context for the agent to use
                all_elements_context = []
                all_selectors = []
                
                # Limit to first 10 results only
                limited_result = result[:10]
                
                for i, match in enumerate(limited_result):
                    suggested_selectors = match.get('suggested_selectors', [])
                    tag_name = match.get('tag_name', 'unknown')
                    is_visible = match.get('is_visible', False)
                    is_interactive = match.get('is_interactive', False)
                    is_clickable = match.get('is_clickable', False)
                    
                    # Collect selectors from the 10 matches only
                    all_selectors.extend(suggested_selectors)
                    
                    # Create simplified context for each element
                    element_context = {
                        "index": i + 1,
                        "tag_name": tag_name,
                        "suggested_selectors": suggested_selectors,
                        "is_visible": is_visible,
                        "is_interactive": is_interactive,
                        "is_clickable": is_clickable
                    }
                    all_elements_context.append(element_context)
                
                # Store simplified element context for the agent
                state['found_element_context'] = {
                    "text": search_text,
                    "total_matches": len(limited_result),
                    "all_elements": all_elements_context,
                    "all_suggested_selectors": all_selectors,
                    "summary": f"Found {len(limited_result)} elements containing '{search_text}'"
                }
                
                # Create simple history entry with top 10 elements and their selectors only
                history_details = []
                for i, elem_ctx in enumerate(all_elements_context):
                    visibility = "visible" if elem_ctx['is_visible'] else "hidden"
                    interactivity = "interactive" if elem_ctx['is_interactive'] else "static"
                    selectors_str = ", ".join(elem_ctx['suggested_selectors'])
                    history_details.append(f"  {i+1}. {elem_ctx['tag_name']} ({visibility}, {interactivity})")
                    history_details.append(f"     Selectors: [{selectors_str}]")
                
                history_entry = f"Step {state['step']}: ✅ FOUND {len(limited_result)} TARGET ELEMENTS! Text: '{search_text}'"
                history_entry += "\n" + "\n".join(history_details)
                
                state['history'].append(history_entry)

                # Simplified debug output
                visible_count = sum(1 for elem in all_elements_context if elem.get('is_visible', False))
                interactive_count = sum(1 for elem in all_elements_context if elem.get('is_interactive', False))
                
                print(f"🔍 LIVE ELEMENT SEARCH DEBUG:")
                print(f"   Search Text: '{search_text}'")
                print(f"   Total Matches (Limited to 10): {len(limited_result)}")
                print(f"   Visible Elements: {visible_count}")
                print(f"   Interactive Elements: {interactive_count}")
                print(f"   Total Selectors: {len(all_selectors)}")
                print(f"   Elements:")
                for i, elem in enumerate(all_elements_context):
                    visibility_icon = "👁️" if elem.get('is_visible') else "👻"
                    interactive_icon = "🖱️" if elem.get('is_interactive') else "📄"
                    
                    print(f"     {visibility_icon}{interactive_icon} {i+1}. {elem['tag_name']}")
                    print(f"        Selectors: {elem['suggested_selectors'][:2]}")
                print(f"   🤖 Agent Context: {len(all_elements_context)} elements with their selectors")
            else:
                # No elements found
                state['history'].append(f"Step {state['step']}: ❌ NO ELEMENTS FOUND! Text: '{search_text}' - No elements contain this text in their attributes")
                print(f"🔍 ELEMENT SEARCH DEBUG: No elements found containing '{search_text}'")

        elif action_type == "close_popup":
            soup = BeautifulSoup(await page.content(), 'html.parser')

            elements = soup.find_all(class_=re.compile(r'overlay'))
            for el in elements:
                classname = el.get('class')
                print(classname)
                if classname:
                    for cls in classname:
                        try:
                            await page.evaluate(f"document.querySelector('.{cls}')?.click()")
                        except Exception as e:
                            print(f"Failed to click on .{cls}: {e}")

            await asyncio.sleep(5)

            inputs = await page.query_selector_all('input')

            for inp in inputs:
                try:
                    if inp.is_enabled() and inp.is_visible():
                        await inp.fill("", timeout=0, force=True)
                except Exception as e:
                    print(f"Failed to clear input fields: {e}")

            raise ValueError(f"No parent element found for text '{action.get('text')}'.")
        else:
            raise ValueError(f"No element found with text '{action.get('text')}'.")
        await page.wait_for_timeout(2000)
        state['history'].append(f"Step {state['step']}: ✅ Executed `{action_signature}` successfully.")

    except Exception as e:
        error_message = str(e).splitlines()[0]
        push_status(job_id, "action_failed", {"action": action, "error": error_message, "signature": action_signature})
        state['history'].append(f"Step {state['step']}: ❌ FAILED `{action_signature}` error='{error_message}' (will avoid repeating).")
        # Record failure
        state['failed_actions'][action_signature] = state['failed_actions'].get(action_signature, 0) + 1
        
    state['step'] += 1
    state['history'] = state['history']
    return state

# --- LangGraph Supervisor Logic ---
def supervisor_node(state: AgentState) -> str:
    if state['last_action'].get("type") == "finish":
        push_status(state['job_id'], "agent_finished", {"reason": state['last_action'].get("reason")})
        return END
    if len(state['results']) >= state['top_k']:
        push_status(state['job_id'], "agent_finished", {"reason": f"Collected {len(state['results'])}/{state['top_k']} items."})
        return END
    if state['step'] > state['max_steps']:
        push_status(state['job_id'], "agent_stopped", {"reason": "Max steps reached."})
        return END
    return "continue"

# --- Build the Graph ---
builder = StateGraph(AgentState)
builder.add_node("navigate", navigate_to_page)
builder.add_node("reason", agent_reasoning_node)
builder.add_node("execute", execute_action_node)
builder.set_entry_point("navigate")
builder.add_edge("navigate", "reason")
builder.add_conditional_edges("execute", supervisor_node, {END: END, "continue": "reason"})
builder.add_edge("reason", "execute")
graph_app = builder.compile()

# --- The Core Job Orchestrator ---
async def run_job(job_id: str, payload: dict, device_id: str = "ZD222GXYPV", ):

    device_id = payload.get("device_id", device_id)
    url = payload.get('query', '')
    incognito = True

    # logger.info(f"[{device_id}] 🚀 Opening website: {url}")
    
    # Setup
    port = get_devtools_port(device_id)
    
    # Launch Chrome
    force_stop_chrome(device_id)
    await asyncio.sleep(2)
    
    if incognito:
        start_chrome_incognito(device_id)
    else:
        start_chrome_normal(device_id)
    
    await asyncio.sleep(3)
    forward_port(device_id, port)
    await asyncio.sleep(2)
    
    # Wait for DevTools
    if not await wait_for_devtools(port):
        print(f"[{device_id}] Error: DevTools not available on port {port}")
        push_status(job_id, "job_failed", {"error": "DevTools not available"})
        JOB_RESULTS[job_id] = {"status": "failed", "error": "DevTools not available"}
        return
    
    
    result = {
        "status": "success", 
        "url": url, 
        "device_id": device_id,
        "incognito": incognito,
        "data": {}
    }

    provider = payload["llm_provider"]
    job_analysis = {
        "job_id": job_id,
        "timestamp": get_current_timestamp(),
        "provider": provider,
        "model": MODEL_MAPPING.get(provider, "unknown"),
        "query": payload["query"],
        "url": payload["url"],
        "steps": []
    }
    
    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{port}")
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()
        print("hello")
        final_result = {}
        final_state = {}
        try:
            push_status(job_id, "job_started", {"provider": provider, "query": payload["query"]})
            
            # MODIFIED: Capture entoken usage from prompt refinement
            refined_query, usage = get_refined_prompt(payload["url"], payload["query"], provider)
            job_analysis["steps"].append({"task": "refine_prompt", **usage})
            push_status(job_id, "prompt_refined", {"refined_query": refined_query, "usage": usage})

            initial_state = AgentState(
                job_id=job_id, browser=browser, page=page, query=payload["url"],
                top_k=payload["top_k"], provider=provider,
                refined_query=refined_query, results=[], screenshots=[],
                job_artifacts_dir=SCREENSHOTS_DIR / job_id,
                step=1, max_steps=100, last_action={},
                history=[],
                token_usage=[], # Initialize empty token usage list
                found_element_context={}, # Initialize empty element context
                failed_actions={}, # NEW: track failed action signatures
                attempted_action_signatures=[] # NEW: chronological list
            )
            initial_state['job_artifacts_dir'].mkdir(exist_ok=True)
            
            # graph_app.get_graph().draw_png()
            final_state = await graph_app.ainvoke(initial_state, {"recursion_limit": 200})

            final_result = {"job_id": job_id, "results": final_state['results'], "screenshots": final_state['screenshots']}
        except Exception as e:
            push_status(job_id, "job_failed", {"error": str(e), "trace": traceback.format_exc()})
            final_result["error"] = str(e)
        finally:
            JOB_RESULTS[job_id] = final_result
            push_status(job_id, "job_done")
            
            # NEW: Aggregate and save analysis report
            if final_state:
                job_analysis["steps"].extend(final_state.get('token_usage', []))
            save_analysis_report(job_analysis)
            
            await page.close()
            await browser.close()

# --- FastAPI Endpoints ---
@app.post("/search")
async def start_search(req: SearchRequest):
    job_id = str(uuid.uuid4())
    JOB_QUEUES[job_id] = asyncio.Queue()
    # loop = asyncio.get_event_loop()
    # loop.run_in_executor(None, run_job, job_id, req.dict())
    asyncio.create_task(run_job(job_id, {**req.model_dump(), "device_id": "10.147.65.232:5555"}))
    return {"job_id": job_id, "stream_url": f"/stream/{job_id}", "result_url": f"/result/{job_id}"}

@app.get("/stream/{job_id}")
async def stream_status(job_id: str):
    q = JOB_QUEUES.get(job_id)
    if not q: raise HTTPException(status_code=404, detail="Job not found")
    async def event_generator():
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=60)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg["msg"] in ("job_done", "job_failed"): break
            except asyncio.TimeoutError: yield ": keep-alive\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/result/{job_id}")
async def get_result(job_id: str):
    result = JOB_RESULTS.get(job_id)
    if not result: return JSONResponse({"status": "pending"}, status_code=202)
    return JSONResponse(result)

@app.get("/screenshots/{job_id}/{filename}")
async def get_screenshot(job_id: str, filename: str):
    file_path = SCREENSHOTS_DIR / job_id / filename
    if not file_path.exists(): raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(file_path)

@app.get("/")
async def client_ui():
    return FileResponse(Path(__file__).parent / "static/test_client.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)