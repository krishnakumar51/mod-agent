# Human-in-the-Loop Web Agent

This enhanced web agent supports **Human-in-the-Loop (HITL)** functionality, allowing the AI agent to pause execution and request user input when needed. This is essential for scenarios like:

- 🔐 Login credentials (username/password)
- 📱 OTP/verification codes
- 📧 Email addresses or phone numbers
- 🧩 CAPTCHA solving
- 🔒 Two-factor authentication

## 🚀 Quick Start

### 1. Start the Server
```bash
cd agent-umi
python main.py
```

The server will start on `http://localhost:8000`

### 2. Access the Web Interface
Open your browser and go to `http://localhost:8000`

You'll see the enhanced interface with a new **"User Input"** tab.

### 3. Test with a Login Scenario
Try these example queries that might trigger user input requests:

**Example 1: E-commerce Login**
- URL: `https://www.amazon.com`
- Query: `Login to my account and search for iPhone 15 Pro Max`

**Example 2: Social Media**
- URL: `https://www.linkedin.com`
- Query: `Login and search for software engineer jobs in San Francisco`

## 🎛️ How It Works

### For the Agent (AI Side)

The agent can now use a new action type: `request_user_input`

```json
{
  "thought": "I see a login form but don't have credentials. I need to ask the user for their username and password.",
  "action": {
    "type": "request_user_input",
    "input_type": "email", 
    "prompt": "Please provide your email address for login",
    "is_sensitive": false
  }
}
```

**Input Types:**
- `text` - General text input
- `password` - Sensitive password (hidden in logs)
- `email` - Email address
- `phone` - Phone number
- `otp` - One-time password/verification code

### For Users (Human Side)

When the agent needs input:

1. **Automatic Notification**: The "User Input" tab will highlight
2. **Input Form**: Shows the agent's request with appropriate input field
3. **Submit**: Enter your input and click "Submit Input"
4. **Resume**: Agent automatically continues with your input

## 🔧 API Endpoints

### Check for Input Requests
```http
GET /user-input-request/{job_id}
```

### Submit User Input
```http
POST /user-input-response
Content-Type: application/json

{
  "job_id": "uuid-here",
  "input_value": "user's input"
}
```

### Get Job Status
```http
GET /jobs/{job_id}/status
```

## 🛠️ Technical Implementation

### State Management
The agent state now includes:
- `waiting_for_user_input`: Boolean flag
- `user_input_request`: Current input request details
- `user_input_response`: User's provided input

### Flow Control
1. Agent encounters input field needing user data
2. Agent calls `request_user_input` action
3. System pauses execution and waits (5-minute timeout)
4. User provides input via web interface
5. System resumes with user's input
6. Agent continues with the provided data

### Error Handling
- **Timeout**: If user doesn't respond within 5 minutes, agent continues without input
- **Invalid Input**: System validates input format based on type
- **Network Errors**: Graceful fallbacks with error messages

## 📱 Using with Fill Actions

After receiving user input, the agent can use it in subsequent actions:

```json
{
  "type": "fill",
  "selector": "#username",
  "text": "{{USER_INPUT}}"
}
```

Special placeholders:
- `{{USER_INPUT}}` - Uses the last user input
- `{{PASSWORD}}` - For password fields
- `{{EMAIL}}` - For email fields
- `{{PHONE}}` - For phone fields
- `{{OTP}}` - For OTP/verification codes

## 🧪 Testing

### Automated Testing
```bash
# Install test dependencies
pip install aiohttp

# Run the test suite (make sure server is running)
python test_human_loop.py
```

### Manual Testing
1. Start the server
2. Open the web interface
3. Start a job that requires login
4. Watch for the "User Input" tab to activate
5. Provide the requested input
6. Observe agent resuming execution

## 🔍 Debugging

### Logs to Watch
- `🔄 WAITING FOR USER INPUT` - Agent paused for input
- `✅ USER INPUT RECEIVED` - Input provided, resuming
- `⏰ USER INPUT TIMEOUT` - User didn't respond in time

### Common Issues

**Agent doesn't ask for input:**
- Ensure the agent recognizes a login scenario
- Check if credentials were provided in the original query

**Input submission fails:**
- Verify the job ID is correct
- Check network connectivity
- Ensure the job is actually waiting for input

**Agent gets stuck:**
- Input requests timeout after 5 minutes
- Agent will attempt to continue without input
- Check the error logs for specific issues

## 🚀 Advanced Usage

### Custom Input Types
You can extend the system with custom input types by modifying:
1. `UserInputRequest` model in `main.py`
2. Input validation in the API endpoints
3. Client-side input field types in `test_client.html`

### Timeout Configuration
Modify the timeout in `execute_action_node`:
```python
await asyncio.wait_for(input_event.wait(), timeout=300)  # 5 minutes
```

### Multiple Input Requests
The agent can make multiple input requests in sequence:
1. Ask for username
2. Fill username field
3. Ask for password
4. Fill password field
5. Ask for OTP
6. Fill OTP field

## 🔐 Security Considerations

- **Sensitive Data**: Passwords and sensitive inputs are marked and hidden in logs
- **Memory Cleanup**: User inputs are cleared after use
- **Timeout Protection**: Prevents indefinite waiting
- **Input Validation**: Basic validation based on input type

## 🤝 Contributing

To extend the human-in-the-loop functionality:

1. **Add new input types** in the `UserInputRequest` model
2. **Enhance validation** in the API endpoints
3. **Improve UI** in the client interface
4. **Add specialized prompts** for specific scenarios

The system is designed to be extensible and can accommodate various user interaction patterns beyond simple text input.