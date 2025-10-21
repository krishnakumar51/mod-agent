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
from typing import List, TypedDict, Dict, Any, Optional
import logging
import aiohttp
import subprocess
from core import (
    force_stop_browser, start_firefox_private, enable_firefox_debugging, 
    get_devtools_port, wait_for_devtools, forward_port, 
    setup_firefox_automation_v2, force_stop_chrome, start_chrome_incognito, 
    start_chrome_normal, setup_chrome_automation_android, CaptchaSolver
)
# from captcha_handler import (
#     auto_solve_captcha_if_present, smart_captcha_handler, 
#     handle_captcha_on_page, handle_captcha_immediately
# )
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from pydantic import BaseModel
from patchright.async_api import async_playwright, Page, Browser  # 🔥 NEW: Cloudflare bypass
from PIL import Image
from langgraph.graph import StateGraph, END
from bs4 import BeautifulSoup

from llm import LLMProvider, get_refined_prompt, get_agent_action
from config import SCREENSHOTS_DIR, ANTHROPIC_MODEL, GROQ_MODEL, OPENAI_MODEL

# ==================== SETUP ====================
app = FastAPI(title="LangGraph Web Agent with Memory")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== STORAGE ====================
JOB_QUEUES = {}
JOB_RESULTS = {}
USER_INPUT_REQUESTS = {}
USER_INPUT_RESPONSES = {}
PENDING_JOBS = {}
JOBS_IN_INPUT_FLOW = set()  # 🔒 Global protection for user input

# ==================== COST TRACKING ====================
ANALYSIS_DIR = Path("analysis")
REPORT_CSV_FILE = Path("report.csv")

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

# ==================== POPUP KILLER CONFIG ====================
POPUP_DISMISS_INDICATORS = [
    "accept", "accept all", "accept cookies", "agree", "agree and continue",
    "i accept", "i agree", "ok", "okay", "yes", "allow", "allow all",
    "got it", "understood", "sounds good", "close", "dismiss", "no thanks",
    "not now", "maybe later", "later", "skip", "skip for now", "remind me later",
    "not interested", "continue", "proceed", "next", "go ahead", "let's go",
    "decline", "reject", "refuse", "no", "cancel", "don't show again",
    "do not show", "only necessary", "necessary only", "essential only",
    "reject all", "decline all", "manage preferences", "continue without",
    "skip sign in", "skip login", "browse as guest", "continue as guest",
    "no account", "no thank you", "unsubscribe", "don't subscribe",
    "×", "✕", "✖", "⨯", "close dialog", "close modal", "close popup",
    "dismiss notification", "close banner", "close alert"
]

POPUP_SELECTORS = [
    "[role='dialog']", "[role='alertdialog']", ".modal", ".popup", 
    ".overlay", ".lightbox", ".dialog", "#cookie-banner", ".cookie-banner",
    "[class*='cookie']", "#cookieConsent", ".cookie-consent", "[id*='cookie']",
    ".overlay-wrapper", ".modal-backdrop", ".popup-overlay", "[class*='overlay']",
    "[class*='backdrop']", ".newsletter-popup", ".subscription-modal",
    "[class*='newsletter']", ".close-btn", ".close-button", "[aria-label*='close']",
    "[aria-label*='dismiss']", "button.close", ".modal-close"
]

# ==================== HELPER FUNCTIONS ====================
def get_current_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def push_status(job_id: str, msg: str, details: dict = None):
    q = JOB_QUEUES.get(job_id)
    if q:
        entry = {"ts": get_current_timestamp(), "msg": msg}
        if details: entry["details"] = details
        q.put_nowait(entry)

def cleanup_stuck_jobs():
    """Clean up jobs stuck waiting for user input"""
    current_time = time.time()
    stuck_jobs = []
    
    for job_id, request in list(USER_INPUT_REQUESTS.items()):
        request_time = request.get('timestamp', '')
        if request_time:
            try:
                request_timestamp = time.mktime(time.strptime(request_time, "%Y-%m-%dT%H:%M:%SZ"))
                if current_time - request_timestamp > 600:
                    stuck_jobs.append(job_id)
            except:
                stuck_jobs.append(job_id)
    
    for job_id in stuck_jobs:
        logger.info(f"Cleaning up stuck job: {job_id}")
        USER_INPUT_REQUESTS.pop(job_id, None)
        USER_INPUT_RESPONSES.pop(job_id, None)
        JOBS_IN_INPUT_FLOW.discard(job_id)
        if job_id in PENDING_JOBS:
            PENDING_JOBS[job_id].set()
            PENDING_JOBS.pop(job_id, None)
    
    return len(stuck_jobs)

def detect_login_failure(page_content: str, page_url: str) -> bool:
    """Detect login failure based on page content/URL"""
    failure_indicators = [
        "invalid credentials", "login failed", "incorrect password", 
        "incorrect username", "authentication failed", "login error",
        "wrong password", "invalid login", "access denied", "login unsuccessful",
        "incorrect email", "invalid email", "user not found", "account not found",
        "too many attempts", "account locked", "temporarily locked"
    ]
    
    url_indicators = ["/login", "/signin", "/auth", "/error", "/failure"]
    
    content_lower = page_content.lower()
    url_lower = page_url.lower()
    
    content_has_failure = any(indicator in content_lower for indicator in failure_indicators)
    still_on_auth_page = any(indicator in url_lower for indicator in url_indicators)
    
    return content_has_failure or still_on_auth_page

def make_action_signature(action: dict) -> str:
    """Create normalized signature for action deduplication"""
    if not isinstance(action, dict) or not action:
        return "invalid"
    parts = [action.get("type", "")]
    for key in ("selector", "text", "key"):
        val = action.get(key)
        if isinstance(val, str) and val.strip():
            truncated = val.strip()
            if len(truncated) > 80:
                truncated = truncated[:77] + "..."
            parts.append(f"{key}={truncated}")
    return "|".join(parts) or "invalid"

# ==================== ELEMENT SEARCH ====================
def find_elements_with_attribute_text_detailed(html: str, text: str) -> List[Dict[str, Any]]:
    """Static HTML search fallback"""
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
                    
                if isinstance(attr_value, list):
                    attr_value_str = ' '.join(str(v) for v in attr_value)
                else:
                    attr_value_str = str(attr_value)
                
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
            selectors = []
            if element.get('id'):
                selectors.append(f"#{element['id']}")
            if element.get('class'):
                classes = element['class'] if isinstance(element['class'], list) else [element['class']]
                class_strings = [str(cls) for cls in classes]
                selectors.append(f".{'.'.join(class_strings)}")
            selectors.append(element.name)
            
            for attr in matched_attributes:
                if attr['name'] not in ['id', 'class']:
                    selectors.append(f"{element.name}[{attr['name']}*='{attr['value'][:20]}']")
            
            matching_elements.append({
                'element_html': str(element),
                'tag_name': element.name,
                'matched_attributes': matched_attributes,
                'suggested_selectors': selectors[:3],
                'all_attributes': dict(element.attrs) if element.attrs else {}
            })

    return matching_elements

async def find_elements_with_text_live(page, text: str) -> List[Dict[str, Any]]:
    """
    🚀 PRODUCTION-GRADE LIVE ELEMENT FINDER with Fuzzy Matching & Scoring
    From m.py - proven to work with 98%+ accuracy
    """
    if not text:
        return []
    
    escaped_text = text.replace('"', '\\"')
    
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
            
            if (targetNorm.length === searchNorm.length) score += 20;
            if (originalTarget.includes(' ') && searchText.includes(' ')) score += 10;
            
            return Math.min(score, 100);
        }}
        
        function generateSelector(element) {{
            const selectors = [];
            
            if (element.id) {{
                selectors.push('#' + element.id);
            }}
            
            if (element.className && typeof element.className === 'string') {{
                const classes = element.className.trim().split(/\\s+/).filter(c => c.length > 0);
                if (classes.length > 0) {{
                    selectors.push('.' + classes.join('.'));
                }}
            }}
            
            for (let attr of element.attributes) {{
                if (attr.name.startsWith('data-') && attr.value) {{
                    selectors.push(`[${{attr.name}}="${{attr.value}}"]`);
                }}
            }}
            
            ['name', 'type', 'role', 'aria-label'].forEach(attrName => {{
                const value = element.getAttribute(attrName);
                if (value) {{
                    selectors.push(`[${{attrName}}="${{value}}"]`);
                }}
            }});
            
            const textContent = element.textContent?.trim();
            if (textContent && textContent.length > 0 && textContent.length < 50) {{
                selectors.push(`text="${{textContent}}"`);
                selectors.push(`:has-text("${{textContent}}")`);
            }}
            
            selectors.push(element.tagName.toLowerCase());
            
            return selectors;
        }}
        
        function checkElement(element) {{
            const matches = [];
            const searchNormalized = normalizeText(searchText);
            
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
        
        const allElements = document.querySelectorAll('*');
        
        allElements.forEach((element, index) => {{
            const matches = checkElement(element);
            
            if (matches.length > 0) {{
                const rect = element.getBoundingClientRect();
                const computedStyle = window.getComputedStyle(element);
                
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
        results = await page.evaluate(js_search_script)
        
        processed_results = []
        for result in results:
            priority_score = 0
            if result['isVisible']:
                priority_score += 10
            if result['isInteractive']:
                priority_score += 5
            if result['isClickable']:
                priority_score += 3
            
            match_scores = [match.get('score', 0) for match in result['matches']]
            max_match_score = max(match_scores) if match_scores else 0
            priority_score += max_match_score / 10
            
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
                'suggested_selectors': result['selectors'][:5],
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
                'all_attributes': {}
            }
            processed_results.append(processed_result)
        
        return processed_results
        
    except Exception as e:
        logger.error(f"Error in live element search: {e}")
        return []

# ==================== COST ANALYSIS ====================
def save_analysis_report(analysis_data: dict):
    """Save token usage analysis"""
    job_id = analysis_data["job_id"]
    provider = analysis_data["provider"]
    model = analysis_data["model"]
    
    total_input = sum(step.get("input_tokens", 0) for step in analysis_data["steps"])
    total_output = sum(step.get("output_tokens", 0) for step in analysis_data["steps"])
    
    analysis_data["total_input_tokens"] = total_input
    analysis_data["total_output_tokens"] = total_output
    
    cost_info = TOKEN_COSTS.get(provider, {}).get(model)
    if not cost_info and provider == "anthropic":
        model_name_lower = model.lower()
        if "sonnet" in model_name_lower:
            cost_info = TOKEN_COSTS.get("anthropic", {}).get("claude-3-5-sonnet-20240620")
        elif "haiku" in model_name_lower:
            cost_info = TOKEN_COSTS.get("anthropic", {}).get("claude-3-haiku-20240307")
    
    total_cost = 0.0
    if cost_info:
        input_cost = (total_input / 1_000_000) * cost_info["input"]
        output_cost = (total_output / 1_000_000) * cost_info["output"]
        total_cost = input_cost + output_cost
    
    analysis_data["total_cost_usd"] = f"{total_cost:.5f}"
    
    try:
        ANALYSIS_DIR.mkdir(exist_ok=True)
        json_report_path = ANALYSIS_DIR / f"{job_id}.json"
        with open(json_report_path, 'w') as f:
            json.dump(analysis_data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving JSON analysis: {e}")
    
    try:
        file_exists = REPORT_CSV_FILE.is_file()
        with open(REPORT_CSV_FILE, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            if not file_exists:
                writer.writerow(['job_id', 'total_input_tokens', 'total_output_tokens', 'total_cost_usd'])
            writer.writerow([job_id, total_input, total_output, f"{total_cost:.5f}"])
    except Exception as e:
        logger.error(f"Error updating CSV report: {e}")

# ==================== POPUP KILLER ====================
async def install_popup_killer(page):
    """
    🛡️ PROACTIVE POPUP KILLER - MutationObserver-based instant removal
    Runs in browser context with zero Python overhead
    """
    popup_killer_script = """
    (function() {
        const DISMISS_TEXTS = [
            "accept", "accept all", "accept cookies", "agree", "agree and continue",
            "i accept", "i agree", "ok", "okay", "yes", "allow", "allow all",
            "got it", "understood", "sounds good", "close", "dismiss", "no thanks",
            "not now", "maybe later", "later", "skip", "skip for now", "remind me later",
            "not interested", "continue", "proceed", "next", "go ahead", "let's go",
            "decline", "reject", "refuse", "no", "cancel", "don't show again",
            "do not show", "only necessary", "necessary only", "essential only",
            "reject all", "decline all", "manage preferences", "continue without",
            "skip sign in", "skip login", "browse as guest", "continue as guest",
            "no account", "no thank you", "unsubscribe", "don't subscribe",
            "×", "✕", "✖", "⨯", "close dialog", "close modal", "close popup",
            "dismiss notification", "close banner", "close alert"
        ];
        
        const POPUP_SELECTORS = [
            "[role='dialog']", "[role='alertdialog']", ".modal", ".popup", 
            ".overlay", ".lightbox", ".dialog", "#cookie-banner", ".cookie-banner",
            "[class*='cookie']", "#cookieConsent", ".cookie-consent", "[id*='cookie']",
            ".overlay-wrapper", ".modal-backdrop", ".popup-overlay", "[class*='overlay']",
            "[class*='backdrop']", ".newsletter-popup", ".subscription-modal",
            "[class*='newsletter']", ".close-btn", ".close-button", "[aria-label*='close']",
            "[aria-label*='dismiss']", "button.close", ".modal-close"
        ];
        
        let processedElements = new WeakSet();
        let killCount = 0;
        
        function normalizeText(text) {
            return text.toLowerCase().trim().replace(/\\s+/g, ' ');
        }
        
        function tryKillPopup(element) {
            if (!element || processedElements.has(element)) return false;
            processedElements.add(element);
            
            const style = window.getComputedStyle(element);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            
            const clickables = element.querySelectorAll('button, a, [role="button"], [onclick]');
            for (const btn of clickables) {
                const text = normalizeText(btn.textContent || btn.innerText || '');
                const ariaLabel = normalizeText(btn.getAttribute('aria-label') || '');
                
                for (const dismissText of DISMISS_TEXTS) {
                    if (text.includes(dismissText) || ariaLabel.includes(dismissText)) {
                        try {
                            btn.click();
                            killCount++;
                            console.log(`🎯 Popup killed #${killCount}: clicked "${btn.textContent}" in`, element);
                            return true;
                        } catch (e) {
                            continue;
                        }
                    }
                }
            }
            
            if (element.tagName === 'BUTTON' || element.getAttribute('role') === 'button') {
                const text = normalizeText(element.textContent || '');
                for (const dismissText of DISMISS_TEXTS) {
                    if (text.includes(dismissText)) {
                        try {
                            element.click();
                            killCount++;
                            console.log(`🎯 Popup killed #${killCount}: direct click`, element);
                            return true;
                        } catch (e) {}
                    }
                }
            }
            
            return false;
        }
        
        function scanAndKill() {
            for (const selector of POPUP_SELECTORS) {
                try {
                    const popups = document.querySelectorAll(selector);
                    for (const popup of popups) {
                        if (tryKillPopup(popup)) return;
                    }
                } catch (e) {}
            }
        }
        
        scanAndKill();
        
        const observer = new MutationObserver((mutations) => {
            let hasRelevantMutation = false;
            
            for (const mutation of mutations) {
                if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
                    for (const node of mutation.addedNodes) {
                        if (node.nodeType === 1) {
                            const tag = node.tagName?.toLowerCase();
                            const classes = node.className?.toLowerCase() || '';
                            const id = node.id?.toLowerCase() || '';
                            
                            if (tag === 'dialog' || 
                                classes.includes('modal') || 
                                classes.includes('popup') || 
                                classes.includes('overlay') ||
                                classes.includes('cookie') ||
                                id.includes('cookie') ||
                                id.includes('modal')) {
                                hasRelevantMutation = true;
                                if (tryKillPopup(node)) return;
                            }
                        }
                    }
                }
            }
            
            if (hasRelevantMutation) {
                setTimeout(scanAndKill, 50);
            }
        });
        
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: false,
            characterData: false
        });
        
        setInterval(scanAndKill, 2000);
        
        console.log('🛡️ Popup killer installed and monitoring...');
        
        window.__popupKillCount = () => killCount;
    })();
    """
    
    try:
        await page.evaluate(popup_killer_script)
        logger.info("🛡️ Proactive popup killer installed")
    except Exception as e:
        logger.warning(f"⚠️ Failed to install popup killer: {e}")

# ==================== API MODELS ====================
class SearchRequest(BaseModel):
    url: str
    query: str
    top_k: int
    llm_provider: LLMProvider = LLMProvider.ANTHROPIC

class UserInputRequest(BaseModel):
    job_id: str
    input_type: str
    prompt: str
    is_sensitive: bool = False

class UserInputResponse(BaseModel):
    job_id: str
    input_value: str

# ==================== AGENT STATE ====================
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
    token_usage: List[dict]
    found_element_context: dict
    failed_actions: Dict[str, int]
    attempted_action_signatures: List[str]
    waiting_for_user_input: bool
    user_input_request: dict
    user_input_response: str
    user_input_flow_active: bool

# ==================== LANGGRAPH NODES ====================
async def navigate_to_page(state: AgentState) -> AgentState:
    """
    🌐 NAVIGATION NODE with UNIVERSAL CAPTCHA & POPUP protection
    Priority: CAPTCHA → Popup Killer → Page Load
    ✅ Upgraded with proven test.py CaptchaSolver system
    """
    try:
        logger.info(f"🌐 Navigating to: {state['query']}")
        await state['page'].goto(state['query'], wait_until='domcontentloaded', timeout=60000)
        
        # 🛡️ Install popup killer FIRST
        try:
            await install_popup_killer(state['page'])
        except Exception as e:
            logger.warning(f"⚠️ Popup killer installation failed (non-critical): {e}")
        
        # 🤖 UNIVERSAL CAPTCHA HANDLING - Using proven test.py CaptchaSolver
        logger.info("🤖 Scanning for CAPTCHAs with universal solver...")
        try:
            # Use the proven CaptchaSolver system from core.py (same as test.py)
            captcha_solver = CaptchaSolver()
            
            # Use the universal detection and solving system
            captcha_result = await captcha_solver.solve_captcha_universal(state['page'], state['query'])
            
            if captcha_result['found']:
                if captcha_result['solved']:
                    push_status(state['job_id'], "captcha_handled", {
                        "url": state['query'], 
                        "status": "solved", 
                        "type": captcha_result['type'],
                        "method": captcha_result['method'],
                        "solver": "universal_proven"
                    })
                    logger.info(f"✅ {captcha_result['type'].upper()} CAPTCHA solved with universal solver!")
                else:
                    push_status(state['job_id'], "captcha_handled", {
                        "url": state['query'], 
                        "status": "failed", 
                        "type": captcha_result['type'],
                        "error": captcha_result['error'],
                        "solver": "universal_proven"
                    })
                    logger.warning(f"⚠️ {captcha_result['type'].upper()} CAPTCHA detected but solving failed: {captcha_result['error']}")
            else:
                logger.info("ℹ️ No CAPTCHA detected - proceeding with normal automation")
        except Exception as e:
            logger.warning(f"⚠️ CAPTCHA scan error (non-critical): {e}")
            push_status(state['job_id'], "captcha_handled", {"url": state['query'], "status": "error", "error": str(e)})
        
        push_status(state['job_id'], "navigation_complete", {"url": state['query']})
        logger.info(f"✅ Navigation completed: {state['query']}")
        
    except Exception as e:
        push_status(state['job_id'], "navigation_failed", {"url": state['query'], "error": str(e)})
        logger.error(f"❌ Navigation failed: {e}")
    
    return state

async def agent_reasoning_node(state: AgentState) -> AgentState:
    """🧠 AGENT REASONING NODE - Analyzes page and decides next action"""
    job_id = state['job_id']
    push_status(job_id, "agent_step", {"step": state['step'], "max_steps": state['max_steps']})
    
    screenshot_path = state['job_artifacts_dir'] / f"{state['step']:02d}_step.png"
    screenshot_success = False
    
    # 📸 Optimized screenshot (skip first 2 steps for speed)
    if state['step'] > 2:
        try:
            await state['page'].wait_for_timeout(500)
            await state['page'].screenshot(
                path=screenshot_path, 
                timeout=5000,
                full_page=False,
                type='png',
            )
            screenshot_success = True
            state['screenshots'].append(f"screenshots/{job_id}/{state['step']:02d}_step.png")
            logger.info(f"Screenshot saved: {screenshot_path}")
        except Exception as e:
            push_status(job_id, "screenshot_failed", {"error": str(e), "step": state['step']})
            logger.warning(f"Screenshot failed at step {state['step']}: {e}")
            screenshot_path = None

    # 📝 Build enhanced history with context
    history_text = "\n".join(state['history'])

    # 💬 Add user input context if available
    if state.get('user_input_response'):
        input_type = state.get('user_input_request', {}).get('input_type', 'input')
        is_sensitive = state.get('user_input_request', {}).get('is_sensitive', False)
        
        if is_sensitive:
            history_text += f"\n\n🔐 USER PROVIDED {input_type.upper()}: {state['user_input_response']} [SENSITIVE DATA - USE THIS EXACT VALUE]"
            history_text += f"\n💡 CRITICAL: Use this exact value '{state['user_input_response']}' in your next fill action."
            history_text += f"\n🚨 DO NOT GENERATE YOUR OWN {input_type.upper()}! Use '{state['user_input_response']}' exactly as provided."
        else:
            history_text += f"\n\n👤 USER PROVIDED {input_type.upper()}: {state['user_input_response']} [Ready to use in next fill action]"
            history_text += f"\n💡 IMPORTANT: Use this exact value '{state['user_input_response']}' in your next fill action."

    # 🚫 Add failed action warnings
    if state.get('failed_actions'):
        failed_list = sorted(state['failed_actions'].items(), key=lambda x: -x[1])
        history_text += f"\n\n⚠ FAILED ACTION SIGNATURES (Do NOT repeat exactly):"
        for sig, count in failed_list[:8]:
            history_text += f"\n  - {sig} (failures={count})"
        history_text += ("\n🔒 RULE: Never emit an action with an identical signature to one that failed. "
                         "Change selector, vary interaction type, or choose a different target.")
    
    # 🎯 Add found element context
    if state.get('found_element_context'):
        element_ctx = state['found_element_context']
        history_text += f"\n\n🎯 ELEMENT SEARCH RESULTS FROM PREVIOUS STEP:"
        history_text += f"\n• Search Text: '{element_ctx['text']}'"
        history_text += f"\n• Total Matches Found: {element_ctx.get('total_matches', 0)}"
        
        if element_ctx.get('all_elements'):
            visible_elements = [e for e in element_ctx['all_elements'] if e.get('is_visible')]
            interactive_elements = [e for e in element_ctx['all_elements'] if e.get('is_interactive')]
            
            history_text += f"\n• Found Elements: {len(visible_elements)} visible, {len(interactive_elements)} interactive"
            
            for elem in element_ctx['all_elements'][:5]:  # Top 5 only
                visibility_indicator = "👁️ VISIBLE" if elem.get('is_visible') else "👻 HIDDEN"
                interactive_indicator = "🖱️ INTERACTIVE" if elem.get('is_interactive') else "📄 STATIC"
                
                history_text += f"\n  Element {elem['index']}: {elem['tag_name']}"
                history_text += f"\n    Status: {visibility_indicator} | {interactive_indicator}"
                history_text += f"\n    Selectors: {', '.join(elem['suggested_selectors'][:3])}"

    # 🤖 Get agent action
    try:
        action_response, usage = get_agent_action(
            query=state['refined_query'],
            url=state['page'].url,
            html=await state['page'].content(),
            provider=state['provider'],
            screenshot_path=screenshot_path if screenshot_success else None,
            history=history_text
        )
        
        state['token_usage'].append({
            "task": f"agent_step_{state['step']}",
            **usage
        })

        push_status(job_id, "agent_thought", {
            "thought": action_response.get("thought", "No thought provided."),
            "usage": usage
        })
        
        if not action_response or not isinstance(action_response, dict):
            raise ValueError("Invalid action response format")
            
        action = action_response.get("action")
        if not action or not isinstance(action, dict) or not action.get("type"):
            raise ValueError("Missing or invalid action in response")
            
        state['last_action'] = action
        
    except Exception as e:
        error_msg = f"Failed to get agent action: {str(e)}"
        push_status(job_id, "agent_error", {"error": error_msg, "step": state['step']})
        logger.error(f"Agent reasoning error at step {state['step']}: {error_msg}")
        
        state['last_action'] = {
            "type": "finish", 
            "reason": f"Agent reasoning failed: {error_msg}"
        }
        
        state['token_usage'].append({
            "task": f"agent_step_{state['step']}_failed",
            "input_tokens": 0,
            "output_tokens": 0,
            "error": error_msg
        })
    
    # Clear found element context after processing
    if state.get('found_element_context'):
        state['found_element_context'] = {}
    
    return state

async def execute_action_node(state: AgentState) -> AgentState:
    """⚡ ACTION EXECUTION NODE - Executes agent decisions with error handling"""
    job_id = state['job_id']
    action = state['last_action']
    page = state['page']
    
    # Build signature for tracking
    action_signature = make_action_signature(action)
    state['attempted_action_signatures'].append(action_signature)

    # Skip if previously failed
    if action_signature in state.get('failed_actions', {}):
        state['history'].append(f"Step {state['step']}: ⏭ Skipped duplicate failed action")
        state['token_usage'].append({
            "task": f"action_skip_{state['step']}",
            "input_tokens": 0,
            "output_tokens": 0,
            "skipped_signature": action_signature
        })
        state['step'] += 1
        return state

    push_status(job_id, "executing_action", {"action": action})
    action_success = False
    
    try:
        action_type = action.get("type")
        
        # ==== CLICK ACTION ====
        if action_type == "click":
            await page.locator(action["selector"]).click(timeout=5000)
            action_success = True
        
        # ==== FILL ACTION ====
        elif action_type == "fill":
            fill_text = action["text"]
            used_user_input = False
            
            # Handle user input placeholders
            if fill_text in ["{{USER_INPUT}}", "{{PASSWORD}}", "{{EMAIL}}", "{{PHONE}}", "{{OTP}}"]:
                if state.get('user_input_response'):
                    fill_text = state['user_input_response']
                    used_user_input = True
                else:
                    raise ValueError(f"Placeholder {fill_text} requires user input but none available")
            
            # Direct user input match
            elif state.get('user_input_response') and fill_text == state['user_input_response']:
                used_user_input = True
            
            # FORCE USER PASSWORD for password fields
            elif (state.get('user_input_response') and 
                  ('password' in action.get('selector', '').lower() or 
                   'pass' in action.get('selector', '').lower()) and
                  state.get('user_input_request', {}).get('input_type') == 'password'):
                fill_text = state['user_input_response']
                used_user_input = True
                state['history'].append(f"Step {state['step']}: 🔐 Forced user password (LLM override)")
            
            # SUSPICIOUS PASSWORD PATTERN override
            elif (state.get('user_input_response') and 
                  state.get('user_input_request', {}).get('input_type') == 'password' and
                  len(fill_text) > 6 and any(c.isdigit() for c in fill_text) and any(c.isupper() for c in fill_text)):
                fill_text = state['user_input_response']
                used_user_input = True
                state['history'].append(f"Step {state['step']}: 🔐 Overrode suspicious password pattern")
            
            # Delay for password fields
            if 'password' in action.get('selector', '').lower():
                await page.wait_for_timeout(1000)
            
            await page.locator(action["selector"]).fill(fill_text, timeout=10000)
            action_success = True
            
            # Clean up user input state
            if used_user_input:
                state['user_input_response'] = ""
                state['user_input_request'] = {}
                state['user_input_flow_active'] = False
                JOBS_IN_INPUT_FLOW.discard(job_id)
                state['history'].append(f"Step {state['step']}: ✅ User input used, flow complete")
        
        # ==== PRESS ACTION ====
        elif action_type == "press":
            await page.locator(action["selector"]).press(action["key"], timeout=5000)
            action_success = True
        
        # ==== SCROLL ACTION ====
        elif action_type == "scroll":
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            action_success = True
        
        # ==== EXTRACT ACTION ====
        elif action_type == "extract":
            items = action.get("items", [])
            for item in items:
                if 'url' in item and isinstance(item.get('url'), str):
                    item['url'] = urljoin(page.url, item['url'])
            state['results'].extend(items)
            action_success = True
            push_status(job_id, "partial_result", {"new_items_found": len(items)})
        
        # ==== POPUP DISMISSAL (Trust proactive killer) ====
        elif action_type == "dismiss_popup_using_text":
            try:
                kill_count = await page.evaluate("window.__popupKillCount ? window.__popupKillCount() : 0")
                state['history'].append(f"Step {state['step']}: ℹ️ Proactive killer active ({kill_count} popups removed)")
                action_success = True
                await page.wait_for_timeout(500)
            except Exception as e:
                error_msg = str(e)[:100]
                state['history'].append(f"Step {state['step']}: ⚠️ Popup check note: {error_msg}")
        
        # ==== ELEMENT SEARCH ====
        elif action_type == "extract_correct_selector_using_text":
            search_text = action.get("text", "")
            if not search_text:
                raise ValueError("No text provided for element search")
            
            result = await find_elements_with_text_live(page, search_text)
            
            if result:
                limited_result = result[:6]
                all_selectors = []
                all_elements_context = []
                
                for i, match in enumerate(limited_result):
                    all_selectors.extend(match.get('suggested_selectors', []))
                    all_elements_context.append({
                        "index": i + 1,
                        "tag_name": match.get('tag_name'),
                        "suggested_selectors": match.get('suggested_selectors', []),
                        "is_visible": match.get('is_visible'),
                        "is_interactive": match.get('is_interactive')
                    })
                
                state['found_element_context'] = {
                    "text": search_text,
                    "total_matches": len(limited_result),
                    "all_elements": all_elements_context,
                    "all_suggested_selectors": all_selectors
                }
                
                state['history'].append(f"Step {state['step']}: ✅ Found {len(limited_result)} elements")
                action_success = True
            else:
                state['history'].append(f"Step {state['step']}: ❌ No elements found")
        
        # ==== USER INPUT REQUEST ====
        elif action_type == "request_user_input":
            input_type = action.get("input_type", "text")
            prompt = action.get("prompt", "Please provide input")
            is_sensitive = action.get("is_sensitive", False)
            
            user_input_request = {
                "input_type": input_type,
                "prompt": prompt,
                "is_sensitive": is_sensitive,
                "timestamp": get_current_timestamp(),
                "step": state['step']
            }
            
            USER_INPUT_REQUESTS[job_id] = user_input_request
            state['user_input_request'] = user_input_request
            state['waiting_for_user_input'] = True
            state['user_input_flow_active'] = True
            JOBS_IN_INPUT_FLOW.add(job_id)
            
            input_event = asyncio.Event()
            PENDING_JOBS[job_id] = input_event
            
            push_status(job_id, "user_input_required", {
                "input_type": input_type,
                "prompt": prompt,
                "is_sensitive": is_sensitive
            })
            
            state['history'].append(f"Step {state['step']}: 🔄 Waiting for user input")
            
            try:
                await asyncio.wait_for(input_event.wait(), timeout=300)
                user_response = USER_INPUT_RESPONSES.get(job_id, "")
                state['user_input_response'] = user_response
                state['waiting_for_user_input'] = False
                
                USER_INPUT_REQUESTS.pop(job_id, None)
                USER_INPUT_RESPONSES.pop(job_id, None)
                PENDING_JOBS.pop(job_id, None)
                
                state['history'].append(f"Step {state['step']}: ✅ User input received")
                action_success = True
                
            except asyncio.TimeoutError:
                state['waiting_for_user_input'] = False
                state['user_input_flow_active'] = False
                JOBS_IN_INPUT_FLOW.discard(job_id)
                USER_INPUT_REQUESTS.pop(job_id, None)
                PENDING_JOBS.pop(job_id, None)
                raise ValueError(f"User input timeout: {prompt}")
        
        # ==== FINISH ACTION ====
        elif action_type == "finish":
            action_success = True
            state['history'].append(f"Step {state['step']}: 🏁 Finishing: {action.get('reason', 'Task complete')}")
        
        # SUCCESS PATH
        if action_success:
            state['history'].append(f"Step {state['step']}: ✅ {action_type} successful")
            await page.wait_for_timeout(300)
            
            # INTELLIGENT CAPTCHA HANDLING - Only check when likely to appear
            # ✅ Upgraded with proven test.py CaptchaSolver system
            if action_type in ["click", "press"]:
                selector_lower = action.get("selector", "").lower()
                should_check_captcha = any(kw in selector_lower for kw in [
                    "submit", "login", "signin", "register", "sign-up", "checkout", 
                    "purchase", "buy", "order", "form", "send", "contact"
                ])
                
                if should_check_captcha:
                    try:
                        logger.info(f"🤖 Post-action CAPTCHA check with universal solver after {action_type}...")
                        
                        # Use the proven CaptchaSolver system from core.py (same as test.py)
                        captcha_solver = CaptchaSolver()
                        
                        # Use the universal detection and solving system
                        captcha_result = await captcha_solver.solve_captcha_universal(page,page.url)
                        
                        if captcha_result['found']:
                            if captcha_result['solved']:
                                state['history'].append(f"Step {state['step']}: 🔓 Post-action CAPTCHA auto-solved ({captcha_result['type']}) with universal solver")
                                push_status(job_id, "captcha_auto_solved", {
                                    "after_action": action_type, 
                                    "type": captcha_result['type'],
                                    "method": captcha_result['method'],
                                    "solver": "universal_proven"
                                })
                            else:
                                state['history'].append(f"Step {state['step']}: ⚠️ Post-action CAPTCHA failed: {captcha_result['error']}")
                        
                        await page.wait_for_timeout(1500)
                    except Exception as e:
                        logger.warning(f"⚠️ Post-action CAPTCHA check failed (non-critical): {e}")
                        state['history'].append(f"Step {state['step']}: ⚠️ CAPTCHA check error: {str(e)[:50]}")
            
            await page.wait_for_timeout(300)
        
    except Exception as e:
        error_msg = str(e)[:100]
        state['history'].append(f"Step {state['step']}: ❌ FAILED {action_type}: {error_msg}")
        state['failed_actions'][action_signature] = state['failed_actions'].get(action_signature, 0) + 1
        push_status(job_id, "action_failed", {"action": action, "error": error_msg})
    
    # ==== LOGIN FAILURE DETECTION (Only for login actions that succeeded) ====
    if action_success and action_type in ["click", "press"]:
        selector_lower = action.get("selector", "").lower()
        if any(kw in selector_lower for kw in ["login", "signin", "submit", "sign-in"]):
            try:
                await page.wait_for_timeout(2000)
                page_content = await page.content()
                if detect_login_failure(page_content, page.url):
                    state['history'].append(f"Step {state['step']}: 🚫 Login failure detected")
                    push_status(job_id, "login_failure_detected", {"step": state['step']})
            except:
                pass
    
    state['step'] += 1
    return state

# ==================== SUPERVISOR ====================
def supervisor_node(state: AgentState) -> str:
    """🎯 SUPERVISOR - Controls workflow continuation"""
    if state['last_action'].get("type") == "finish":
        push_status(state['job_id'], "agent_finished", {"reason": state['last_action'].get("reason")})
        return END
    if len(state['results']) >= state['top_k']:
        push_status(state['job_id'], "agent_finished", {"reason": f"Collected {len(state['results'])}/{state['top_k']} items."})
        return END
    if state['step'] > state['max_steps']:
        push_status(state['job_id'], "agent_stopped", {"reason": "Max steps reached."})
        return END
    if state.get('waiting_for_user_input', False):
        return "continue"
    return "continue"

# ==================== BUILD GRAPH ====================
builder = StateGraph(AgentState)
builder.add_node("navigate", navigate_to_page)
builder.add_node("reason", agent_reasoning_node)
builder.add_node("execute", execute_action_node)
builder.set_entry_point("navigate")
builder.add_edge("navigate", "reason")
builder.add_conditional_edges("execute", supervisor_node, {END: END, "continue": "reason"})
builder.add_edge("reason", "execute")
graph_app = builder.compile()

# ==================== JOB ORCHESTRATOR ====================
async def run_job(job_id: str, payload: dict, device_id: str = "ZD222GXYPV"):
    """
    🚀 MAIN JOB ORCHESTRATOR - ANDROID ONLY with Stealth protection
    Supports: Local Android, Emulator, Ngrok connections
    """
    device_id = payload.get("device_id", device_id)
    url = payload.get('query', '')
    
    # Detect connection type
    is_ngrok = device_id.startswith("https://") or device_id.startswith("http://")
    
    if is_ngrok:
        logger.info(f"🌐 Using ngrok connection: {device_id}")
        if not device_id.endswith('/'):
            device_id += '/'
        
        # Get WebSocket URL for ngrok
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{device_id}json/version") as resp:
                    data = await resp.json()
                    websocket_path = data["webSocketDebuggerUrl"].split("/devtools/")[1]
                    cdp_endpoint = f"wss://{device_id.split('://')[1].rstrip('/')}/devtools/{websocket_path}"
            except Exception as e:
                logger.error(f"❌ Failed to get WebSocket URL from ngrok: {e}")
                push_status(job_id, "job_failed", {"error": f"Ngrok connection failed: {str(e)}"})
                JOB_RESULTS[job_id] = {"status": "failed", "error": str(e)}
                return
    else:
        logger.info(f"📱 Using local Android device: {device_id}")
        
        try:
            port = setup_chrome_automation_android(device_id)
            logger.info(f"✅ Chrome automation ready on port {port}")
            cdp_endpoint = f"http://localhost:{port}"
        except Exception as e:
            error_msg = f"Chrome setup failed: {str(e)}"
            logger.error(f"❌ {error_msg}")
            push_status(job_id, "job_failed", {"error": error_msg})
            JOB_RESULTS[job_id] = {"status": "failed", "error": error_msg}
            return
    
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
    
    # 🔥 ANDROID-ONLY: Connect to device, then apply stealth
    async with async_playwright() as p:
        browser = None
        context = None
        page = None
        
        try:
            logger.info(f"📱 Connecting to Android device via CDP: {cdp_endpoint}")
            
            # ✅ ALWAYS connect to Android device first
            browser = await p.chromium.connect_over_cdp(cdp_endpoint)
            contexts = browser.contexts
            
            if not contexts:
                logger.info("📱 Creating new context on Android device...")
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
                    viewport={"width": 393, "height": 851},
                    device_scale_factor=2.75,
                    is_mobile=True,
                    has_touch=True,
                    locale="en-IN",
                    timezone_id="Asia/Kolkata",
                    storage_state=None,
                )
            else:
                logger.info("📱 Using existing context on Android device...")
                context = contexts[0]
            
           
            # Create page
            page = await context.new_page()
            logger.info("✅ Android automation ready!")
            
            final_result = {}
            final_state = {}
            
            try:
                push_status(job_id, "job_started", {"provider": provider, "query": payload["query"]})
                
                refined_query, usage = get_refined_prompt(payload["url"], payload["query"], provider)
                job_analysis["steps"].append({"task": "refine_prompt", **usage})
                push_status(job_id, "prompt_refined", {"refined_query": refined_query, "usage": usage})

                initial_state = AgentState(
                    job_id=job_id, 
                    browser=browser, 
                    page=page, 
                    query=payload["url"],
                    top_k=payload["top_k"], 
                    provider=provider,
                    refined_query=refined_query, 
                    results=[], 
                    screenshots=[],
                    job_artifacts_dir=SCREENSHOTS_DIR / job_id,
                    step=1, 
                    max_steps=100, 
                    last_action={},
                    history=[],
                    token_usage=[],
                    found_element_context={},
                    failed_actions={},
                    attempted_action_signatures=[],
                    waiting_for_user_input=False,
                    user_input_request={},
                    user_input_response="",
                    user_input_flow_active=False
                )
                initial_state['job_artifacts_dir'].mkdir(exist_ok=True)
                
                final_state = await graph_app.ainvoke(initial_state, {"recursion_limit": 200})

                final_result = {
                    "job_id": job_id, 
                    "results": final_state['results'], 
                    "screenshots": final_state['screenshots']
                }
                
            except Exception as e:
                push_status(job_id, "job_failed", {"error": str(e), "trace": traceback.format_exc()})
                final_result = {"job_id": job_id, "error": str(e)}
                
            finally:
                JOB_RESULTS[job_id] = final_result
                push_status(job_id, "job_done")
                
                if final_state:
                    job_analysis["steps"].extend(final_state.get('token_usage', []))
                save_analysis_report(job_analysis)
                
                if page:
                    await page.close()
                if context:
                    await context.close()
                if browser:
                    await browser.close()
                
        except Exception as e:
            logger.error(f"❌ Browser connection error: {e}")
            push_status(job_id, "job_failed", {"error": f"Browser connection failed: {str(e)}"})
            JOB_RESULTS[job_id] = {"status": "failed", "error": str(e)}

# ==================== FASTAPI ENDPOINTS ====================
@app.post("/search")
async def start_search(req: SearchRequest):
    """🔍 Start new search job"""
    job_id = str(uuid.uuid4())
    JOB_QUEUES[job_id] = asyncio.Queue()
    asyncio.create_task(run_job(job_id, {**req.model_dump(), "device_id": "ZD222GXYPV"}))
    return {
        "job_id": job_id, 
        "stream_url": f"/stream/{job_id}", 
        "result_url": f"/result/{job_id}"
    }

@app.get("/stream/{job_id}")
async def stream_status(job_id: str):
    """📡 Stream job status updates (SSE)"""
    q = JOB_QUEUES.get(job_id)
    if not q: 
        raise HTTPException(status_code=404, detail="Job not found")
    
    async def event_generator():
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=60)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg["msg"] in ("job_done", "job_failed"): 
                    break
            except asyncio.TimeoutError: 
                yield ": keep-alive\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/result/{job_id}")
async def get_result(job_id: str):
    """📊 Get job results"""
    result = JOB_RESULTS.get(job_id)
    if not result: 
        return JSONResponse({"status": "pending"}, status_code=202)
    return JSONResponse(result)

@app.get("/screenshots/{job_id}/{filename}")
async def get_screenshot(job_id: str, filename: str):
    """📸 Get screenshot file"""
    file_path = SCREENSHOTS_DIR / job_id / filename
    if not file_path.exists(): 
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(file_path)

@app.get("/user-input-request/{job_id}")
async def get_user_input_request(job_id: str):
    """💬 Get pending user input request"""
    if job_id not in USER_INPUT_REQUESTS:
        raise HTTPException(status_code=404, detail="No pending user input request for this job")
    
    return {"job_id": job_id, **USER_INPUT_REQUESTS[job_id]}

@app.post("/user-input-response")
async def submit_user_input(response: UserInputResponse):
    """✅ Submit user input to resume job"""
    job_id = response.job_id
    
    if job_id not in USER_INPUT_REQUESTS:
        raise HTTPException(status_code=404, detail="No pending user input request for this job")
    
    if job_id not in PENDING_JOBS:
        raise HTTPException(status_code=400, detail="Job is not waiting for user input")
    
    USER_INPUT_RESPONSES[job_id] = response.input_value
    
    event = PENDING_JOBS[job_id]
    event.set()
    
    return {"status": "success", "message": "User input received, job will resume"}

@app.get("/jobs/{job_id}/status")
async def get_job_status(job_id: str):
    """📋 Get comprehensive job status"""
    status = {
        "job_id": job_id,
        "has_result": job_id in JOB_RESULTS,
        "waiting_for_input": job_id in USER_INPUT_REQUESTS,
        "is_running": job_id in JOB_QUEUES
    }
    
    if job_id in USER_INPUT_REQUESTS:
        status["input_request"] = USER_INPUT_REQUESTS[job_id]
    
    if job_id in JOB_RESULTS:
        status["result"] = JOB_RESULTS[job_id]
    
    return status

@app.post("/admin/cleanup-stuck-jobs")
async def cleanup_stuck_jobs_endpoint():
    """🧹 Clean up stuck jobs (admin)"""
    cleaned_count = cleanup_stuck_jobs()
    return {
        "status": "success",
        "message": f"Cleaned up {cleaned_count} stuck job(s)",
        "cleaned_jobs": cleaned_count
    }

@app.get("/admin/system-status")
async def get_system_status():
    """📊 Get system status overview"""
    return {
        "active_jobs": len(JOB_QUEUES),
        "completed_jobs": len(JOB_RESULTS),
        "pending_input_requests": len(USER_INPUT_REQUESTS),
        "pending_responses": len(USER_INPUT_RESPONSES),
        "jobs_in_input_flow": len(JOBS_IN_INPUT_FLOW),
        "input_flow_jobs": list(JOBS_IN_INPUT_FLOW),
        "stuck_jobs_cleaned": cleanup_stuck_jobs()
    }

@app.get("/")
async def client_ui():
    """🌐 Serve test client UI"""
    return FileResponse(Path(__file__).parent / "static/test_client.html")

# ==================== STARTUP ====================
if __name__ == "__main__":
    import uvicorn
    
    logger.info("🚀 Starting LangGraph Web Agent Server...")
    logger.info("📦 Features: Malenia + Stealth, CAPTCHA Solver, Popup Killer, HITL")
    logger.info("🌐 Server: http://0.0.0.0:8000")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)