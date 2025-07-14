# BskyOracle

A fact-checking bot for Bluesky that analyzes posts and provides accurate, concise fact-checks using AI-powered web search.

## Features

- **Real-time fact-checking** of Bluesky posts using Google's Gemini AI with web search
- **Thread context analysis** to understand conversation flow
- **Structured responses** with status classification (TRUE/FALSE/MISLEADING/UNVERIFIABLE/NO_CLAIMS)
- **Professional tone** suitable for social media engagement
- **Source attribution** using natural language (e.g., "per the Census", "according to Reuters")
- **Automatic cleanup** of academic citations and formatting

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd bskyOracle
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install required dependencies:
```bash
pip install google-generativeai atproto requests
```

## Setup

1. **Get API Keys:**
   - Google Gemini API key from [Google AI Studio](https://makersuite.google.com/app/apikey)
   - Bluesky app password from [Bluesky Settings](https://bsky.app/settings/app-passwords)

2. **Configure the bot:**
   - Update the prompt in `prompt/prompt.txt` if needed
   - Modify response format or guidelines as required

## Usage

### Basic Example

```python
from fact_checker import FactChecker

# Initialize the fact checker
fact_checker = FactChecker(
    gemini_api_key="your-gemini-api-key",
    bluesky_username="your.handle.bsky.social",
    bluesky_password="your-app-password",
    prompt_file="prompt/prompt.txt"
)

# Fact-check a specific post
post_url = "https://bsky.app/profile/user/post/123"
result = fact_checker.fact_check_post(post_url)

# Get formatted response for Bluesky
reply_text = fact_checker.format_bluesky_reply(result)
print(reply_text)

# Or do the complete workflow (fact-check + reply)
success = fact_checker.post_fact_check_reply(post_url)
```

### Response Format

The bot returns structured JSON responses:

```json
{
    "thinking": "Step-by-step analysis of claims and sources",
    "status": "TRUE|FALSE|MISLEADING|UNVERIFIABLE|NO_CLAIMS",
    "category": "POLITICAL|HEALTH|SCIENCE|NEWS|OPINION|OTHER",
    "response": "Professional response suitable for Bluesky reply"
}
```

## File Structure

```
bskyOracle/
├── clients/
│   ├── bluesky.py      # Bluesky client for reading posts and replying
│   └── gemini.py       # Google Gemini client with web search
├── prompt/
│   └── prompt.txt      # Fact-checking prompt template
├── fact_checker.py     # Main FactChecker class
└── README.md
```

## Key Components

### BlueskyClient (`clients/bluesky.py`)
- Authenticates with Bluesky
- Retrieves post content and thread context
- Posts replies to fact-checked content
- Converts URLs to AT Protocol URIs

### GeminiClient (`clients/gemini.py`)
- Interfaces with Google's Gemini AI
- Enables web search for fact-checking
- Handles rate limiting and caching

### FactChecker (`fact_checker.py`)
- Orchestrates the fact-checking workflow
- Parses JSON responses robustly
- Cleans up citations and formatting
- Provides both raw data and formatted responses

## Prompt Engineering

The bot uses a carefully crafted prompt (`prompt/prompt.txt`) that:

- Instructs the model to think step-by-step
- Defines clear classification criteria
- Emphasizes natural source attribution
- Focuses on substantial errors vs. minor variations
- Maintains professional but conversational tone
- Limits response length for social media

## Response Guidelines

- **TRUE**: Core claims are factually accurate (allows minor variations)
- **FALSE**: Claims contain significant factual errors
- **MISLEADING**: Accurate facts presented in a distorted way
- **UNVERIFIABLE**: Claims cannot be confirmed with available sources
- **NO_CLAIMS**: Post contains no specific factual claims to verify

## Rate Limiting

The Gemini client includes built-in rate limiting (6-second delay by default) to respect API limits. Responses are cached to avoid redundant API calls.

## Error Handling

- Robust JSON parsing with fallback extraction
- Automatic cleanup of academic citations and quotation marks
- Graceful handling of authentication and API failures
- Clear error messages for debugging

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This bot provides automated fact-checking assistance but should not be considered a definitive source of truth. Users should verify important information through multiple reliable sources.