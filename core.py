import os
import json
import time
import asyncio
import logging
import subprocess
from pathlib import Path
from filelock import FileLock
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth
import aiohttp
from typing import List, Dict, Any

# ------------------------- Helper Functions -------------------------
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
    
    # JavaScript function to search for elements comprehensively
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
            
            // XPath selector as fallback
            let path = '';
            let current = element;
            while (current && current.nodeType === Node.ELEMENT_NODE) {{
                let index = 1;
                let sibling = current.previousElementSibling;
                while (sibling) {{
                    if (sibling.tagName === current.tagName) index++;
                    sibling = sibling.previousElementSibling;
                }}
                const tagName = current.tagName.toLowerCase();
                path = `/${{tagName}}[${{index}}]` + path;
                current = current.parentElement;
            }}
            if (path) {{
                selectors.push(`xpath=//${{path.substring(1)}}`);
            }}
            
            // Tag-based selector (lowest priority)
            selectors.push(element.tagName.toLowerCase());
            
            return selectors;
        }}
        
        function normalizeText(text) {{
            if (!text) return '';
            return text.toLowerCase()
                .replace(/[\\s_-]+/g, '')  // Remove spaces, underscores, hyphens
                .replace(/[^a-z0-9]/g, ''); // Keep only alphanumeric
        }}
        

        
        function checkElement(element) {{
            const matches = [];
            const searchNormalized = normalizeText(searchText);
            
            // Check all attributes
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
        
        // Sort by relevance (visible, interactive elements and match quality first)
        results.sort((a, b) => {{
            // Calculate match quality score
            const maxScoreA = Math.max(...a.matches.map(m => m.maxScore || m.score || 50));
            const maxScoreB = Math.max(...b.matches.map(m => m.maxScore || m.score || 50));
            
            // Calculate total relevance score
            const scoreA = (a.isVisible ? 20 : 0) + (a.isInteractive ? 15 : 0) + (a.isClickable ? 10 : 0) + maxScoreA + a.matches.length * 5;
            const scoreB = (b.isVisible ? 20 : 0) + (b.isInteractive ? 15 : 0) + (b.isClickable ? 10 : 0) + maxScoreB + b.matches.length * 5;
            
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
            # Calculate enhanced priority score with fuzzy matching
            priority_score = 0
            
            # Visibility and interaction bonuses
            if result['isVisible']:
                priority_score += 20
            if result['isInteractive']:
                priority_score += 15
            if result['isClickable']:
                priority_score += 10
            
            # Match quality bonus
            match_scores = []
            for match in result['matches']:
                if 'maxScore' in match:
                    match_scores.append(match['maxScore'])
                elif 'score' in match:
                    match_scores.append(match['score'])
                else:
                    match_scores.append(50)  # Default score for old format
            
            if match_scores:
                max_match_score = max(match_scores)
                avg_match_score = sum(match_scores) / len(match_scores)
                priority_score += max_match_score + (avg_match_score * 0.3)
            
            # Number of matches bonus
            priority_score += len(result['matches']) * 5
            
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
                'element_summary': f"{result['tagName']} ({'visible' if result['isVisible'] else 'hidden'}, {'interactive' if result['isInteractive'] else 'static'}) - {len(result['matches'])} matches"
            }
            processed_results.append(processed_result)
        
        return processed_results
        
    except Exception as e:
        print(f"Error in live element search: {{e}}")
        return []

# ------------------------- Logger Setup -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("android_browser")

# ------------------------- Configuration -------------------------
UTILS_FOLDER = "utils"
PORT_MAP_FILE = Path(UTILS_FOLDER) / "device_port_map.json"
PORT_MAP_LOCK = str(PORT_MAP_FILE) + ".lock"
BASE_PORT = 9222
SCREENSHOTS_FOLDER = "screenshots"
DOWNLOADS_FOLDER = "downloads"

# Create necessary directories
os.makedirs(UTILS_FOLDER, exist_ok=True)
os.makedirs(SCREENSHOTS_FOLDER, exist_ok=True)
os.makedirs(DOWNLOADS_FOLDER, exist_ok=True)

# ------------------------- Utility Functions -------------------------

def get_connected_devices():
    """Get list of connected Android devices."""
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, check=True)
        lines = result.stdout.strip().split('\n')[1:]  # Skip header
        devices = []
        for line in lines:
            if line.strip() and 'device' in line:
                device_id = line.split()[0]
                devices.append(device_id)
        return devices
    except subprocess.CalledProcessError:
        logger.error("❌ ADB not found or no devices connected")
        return []

def get_devtools_port(device_id):
    """Assign or retrieve a unique port for a device."""
    with FileLock(PORT_MAP_LOCK):
        port_map = {}
        if PORT_MAP_FILE.exists():
            try:
                port_map = json.loads(PORT_MAP_FILE.read_text())
            except json.JSONDecodeError:
                logger.warning("⚠️ Failed to parse port map file. Starting fresh.")

        if device_id not in port_map:
            assigned_ports = set(port_map.values())
            port = BASE_PORT
            while port in assigned_ports:
                port += 1
            port_map[device_id] = port
            PORT_MAP_FILE.write_text(json.dumps(port_map, indent=2))
        return port_map[device_id]

def run_adb_command(device_id, *args):
    """Run ADB command safely."""
    try:
        cmd = ["adb", "-s", device_id] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.warning(f"ADB command failed: {' '.join(args)} | {e}")
        return None

def setup_chrome_remote_debugging(device_id):
    """Setup Chrome for remote debugging."""
    logger.info(f"[{device_id}] 🔧 Setting up Chrome remote debugging...")
    
    # Enable Chrome remote debugging
    run_adb_command(device_id, "shell", "am", "start", 
                   "-n", "com.android.chrome/com.google.android.apps.chrome.IntentDispatcher",
                   "-a", "android.intent.action.VIEW",
                   "--ez", "enable-remote-debugging", "true")
    
    # Force stop Chrome first
    run_adb_command(device_id, "shell", "am", "force-stop", "com.android.chrome")
    time.sleep(2)

def force_stop_chrome(device_id):
    """Force stop Chrome browser."""
    logger.info(f"[{device_id}] 🚪 Force-stopping Chrome...")
    run_adb_command(device_id, "shell", "am", "force-stop", "com.android.chrome")

def start_chrome_incognito(device_id):
    """Launch Chrome in incognito mode with remote debugging."""
    logger.info(f"[{device_id}] 🌀 Launching Chrome in incognito mode...")
    run_adb_command(device_id, "shell", "am", "start",
                   "-n", "com.android.chrome/com.google.android.apps.chrome.Main",
                   "-d", "chrome://incognito",
                   "--ez", "create_new_tab", "true")

    logger.info(f"[{device_id}] ✅ Chrome launched")

def start_chrome_normal(device_id):
    """Launch Chrome in normal mode with remote debugging."""
    logger.info(f"[{device_id}] 🌀 Launching Chrome in normal mode...")
    run_adb_command(device_id, "shell", "am", "start",
                   "-n", "com.android.chrome/com.google.android.apps.chrome.IntentDispatcher",
                   "-a", "android.intent.action.VIEW",
                   "-d", "about:blank",
                   "--ez", "create_new_tab", "true")
    logger.info(f"[{device_id}] ✅ Chrome launched")

def forward_port(device_id, port):
    """Forward port for DevTools connection."""
    run_adb_command(device_id, "forward", f"tcp:{port}", "localabstract:chrome_devtools_remote")
    logger.info(f"[{device_id}] 🔌 Port {port} forwarded")

async def wait_for_devtools(port, retries=10, delay=2):
    """Wait for DevTools endpoint to be available."""
    url = f"http://localhost:{port}/json/version"
    logger.info(f"⏳ Waiting for DevTools on port {port}...")
    
    async with aiohttp.ClientSession() as session:
        for attempt in range(retries):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        logger.info("✅ DevTools endpoint is ready")
                        return True
            except Exception as e:
                logger.debug(f"DevTools connection attempt {attempt + 1} failed: {e}")
            
            await asyncio.sleep(delay)
    
    logger.error("❌ DevTools endpoint not available")
    return False

def clean_chrome_downloads(device_id):
    """Clean existing downloads from Chrome."""
    logger.info(f"[{device_id}] 🗑️ Cleaning up existing downloads...")
    # Clear Chrome downloads
    run_adb_command(device_id, "shell", "rm", "-f", "/sdcard/Download/*")
    run_adb_command(device_id, "shell", "rm", "-f", "/sdcard/Android/data/com.android.chrome/files/Download/*")

def get_device_info(device_id):
    """Get basic device information."""
    info = {}
    info["device_id"] = device_id
    info["model"] = run_adb_command(device_id, "shell", "getprop", "ro.product.model")
    info["android_version"] = run_adb_command(device_id, "shell", "getprop", "ro.build.version.release")
    info["brand"] = run_adb_command(device_id, "shell", "getprop", "ro.product.brand")
    return info

# ------------------------- Core Browser Controller -------------------------

async def open_website_on_android(device_id, url, incognito=True, wait_time=5, take_screenshot=False, custom_actions=None):
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
    logger.info(f"[{device_id}] 🚀 Opening website: {url}")
    
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
        return {"status": "error", "message": "Failed to connect to Chrome DevTools"}
    
    result = {
        "status": "success", 
        "url": url, 
        "device_id": device_id,
        "incognito": incognito,
        "data": {}
    }

    async with Stealth().use_async(async_playwright()) as p:
        try:
            # Connect to Chrome on Android
            browser = await p.chromium.connect_over_cdp(f"http://localhost:{port}")
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()

            # Navigate to the website
            logger.info(f"[{device_id}] 🌐 Navigating to: {url}")
            await page.goto("https://iherb.com", timeout=30000)
            
            # Wait for the specified time
            await asyncio.sleep(wait_time)

            # page_source = await page.content()

            # with open('page_source.html', 'w', encoding='utf-8') as f:
            #     f.write(page_source)

            print(f"Page source saved to page_source.html")

            # await page.click('.css-7i7ajc')

            search_term = "Search"
            results = await find_elements_with_text_live(page, search_term)

            element = await page.wait_for_selector(".css-7i7ajc", timeout=5000)
            await element.click()

            print(f"\n🔍 LIVE SEARCH RESULTS: Found {len(results)} elements with '{search_term}'")

            print("="*80)
            
            for i, result in enumerate(results[:10]):  # Show first 10 results
                visibility = "👁️ VISIBLE" if result['is_visible'] else "👻 HIDDEN"
                interactivity = "🖱️ INTERACTIVE" if result['is_interactive'] else "📄 STATIC"
                priority = "🎯 HIGH" if result['priority_score'] > 15 else "📍 NORMAL" if result['priority_score'] > 5 else "⬇️ LOW"
                
                print(f"\n{i+1}. {priority} {result['element_summary']}")
                print(f"   Status: {visibility} | {interactivity}")
                print(f"   Position: {result['position']['x']},{result['position']['y']} ({result['position']['width']}x{result['position']['height']})")
                print(f"   Methods: {', '.join(result['interaction_methods']) if result['interaction_methods'] else 'None'}")
                print(f"   Top Selectors: {', '.join(result['suggested_selectors'][:3])}")
                
                # Show match details with scores
                match_details = []
                for match in result['matches'][:3]:  # Show first 3 matches
                    if match['type'] == 'attribute':
                        score = match.get('maxScore', 0)
                        match_type = "name" if match.get('nameMatch') else "value"
                        match_details.append(f"{match['name']} ({match_type}, score: {score})")
                    else:
                        score = match.get('score', 0)
                        match_details.append(f"{match['type']}: {match['value'][:30]}... (score: {score})")
                
                if match_details:
                    print(f"   Matches: {', '.join(match_details)}")
                
                # Show the best match score
                best_score = max([m.get('maxScore', m.get('score', 0)) for m in result['matches']], default=0)
                if best_score > 0:
                    match_quality = "🎯 PERFECT" if best_score >= 100 else "🔥 EXCELLENT" if best_score >= 80 else "✅ GOOD" if best_score >= 60 else "📍 PARTIAL"
                    print(f"   Best Match: {match_quality} (Score: {best_score})")
                
                if result['text_content']:
                    print(f"   Text: {result['text_content'][:100]}...")
            
            # Get page information
            # result["data"]["title"] = await page.title()
            # result["data"]["final_url"] = page.url
            # logger.info(f"[{device_id}] ✅ Page loaded: {result['data']['title']}")
            
            # # Take screenshot if requested
            # if take_screenshot:
            #     screenshot_path = os.path.join(SCREENSHOTS_FOLDER, f"{device_id}_{int(time.time())}.png")
            #     await page.screenshot(path=screenshot_path, full_page=True)
            #     result["data"]["screenshot"] = screenshot_path
            #     logger.info(f"[{device_id}] 📸 Screenshot saved: {screenshot_path}")
            
            # # Get basic page metrics
            # result["data"]["viewport"] = await page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })")
            # result["data"]["scroll_height"] = await page.evaluate("() => document.body.scrollHeight")
            
            await browser.close()

        except Exception as e:
            logger.error(f"[{device_id}] ❌ Failed to open website: {e}")
            result["status"] = "error"
            result["message"] = str(e)
            return result

    logger.info(f"[{device_id}] 🎉 Website opened successfully!")
    return result

async def example_scroll_and_extract(page, device_id):
    """Example custom action: Scroll page and extract text."""
    try:
        # Scroll down multiple times
        scroll_count = 3
        for i in range(scroll_count):
            await page.mouse.wheel(0, 500)
            await asyncio.sleep(1)
        
        # Extract all visible text
        text_content = await page.evaluate("() => document.body.innerText")
        word_count = len(text_content.split())
        
        # Extract all links
        links = await page.query_selector_all("a[href]")
        link_texts = []
        for link in links[:10]:  # First 10 links
            text = await link.inner_text()
            href = await link.get_attribute("href")
            if text and href:
                link_texts.append({"text": text.strip(), "href": href})
        
        return {
            "action": "scroll_and_extract",
            "scrolls_performed": scroll_count,
            "word_count": word_count,
            "links_found": len(link_texts),
            "sample_links": link_texts
        }
    except Exception as e:
        return {"action": "scroll_and_extract", "error": str(e)}

async def main():
    """Main function to run the browser controller."""
    # Check for connected devices
    devices = get_connected_devices()
    if not devices:
        logger.error("❌ No Android devices found. Please:")
        logger.error("   1. Connect your Android device via USB")
        logger.error("   2. Enable USB Debugging in Developer Options")
        logger.error("   3. Run 'adb devices' to verify connection")
        return

    logger.info(f"📱 Found {len(devices)} connected device(s): {devices}")
    
    # Use the first available device
    device_id = devices[0]
    device_info = get_device_info(device_id)
    logger.info(f"🎯 Using device: {device_info}")
    
    # Setup Chrome remote debugging
    setup_chrome_remote_debugging(device_id)
    
    # Replace this URL with any website you want to test
    custom_url = "https://x.com"  # Change this to any URL
    
    result = await open_website_on_android(
        device_id=device_id,
        url=custom_url,
        incognito=True,
        wait_time=8,
        take_screenshot=True,
        custom_actions=example_scroll_and_extract
    )
    

# if __name__ == "__main__":
#     print("🤖 Android Browser Controller Starting...")
#     print("📋 Make sure your Android device is connected and USB debugging is enabled!")
#     print("🌐 This script can open ANY website on your Android device!")
    
#     # Run the main demo
#     asyncio.run(main())
    
    # Or use the simple function:
    # asyncio.run(open_custom_website("https://www.reddit.com", take_screenshot=True))