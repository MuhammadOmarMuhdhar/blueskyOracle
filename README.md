# BskyOracle


A fact-checking bot for Bluesky that analyzes posts and provides accurate, concise fact-checks using AI-powered web search.

## Disclaimer

This bot provides automated fact-checking assistance but should not be considered a definitive source of truth. Users should verify important information through multiple reliable sources.

## File Structure

```
bskyOracle/
├── clients/
│   ├── bluesky.py      # Bluesky AT Protocol client for posts and notifications
│   ├── gemini.py       # Google Gemini AI client with web search
│   └── bigQuery.py     # BigQuery client for analytics logging
├── bots/
│   └── factChecker.py  # Main fact-checking bot logic
├── prompt/
│   └── prompt.txt      # Fact-checking prompt with content analysis
├── daemon.py           # Live monitoring service (Oracle class)
├── render.yaml         # Render deployment configuration
├── Procfile           # Process definition for deployment
├── requirements.txt   # Python dependencies
├── DATAPOLICY.md     # Data collection and privacy policy
└── .env              # Environment variables (API keys, BigQuery config)
```

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
pip install -r requirements.txt
```

## Setup

1. **Get API Keys:**
   - Google Gemini API key from [Google AI Studio](https://makersuite.google.com/app/apikey)
   - Bluesky app password from [Bluesky Settings](https://bsky.app/settings/app-passwords)

2. **Create environment file:**
   ```bash
   cp .env.example .env
   # Add your API keys to .env
   ```

3. **Configure BigQuery (optional):**
   - Create a Google Cloud project
   - Set up BigQuery dataset and service account
   - Add BigQuery credentials to `.env`

4. **Update prompt if needed:**
   - Modify `prompt/prompt.txt` for custom fact-checking behavior

## Usage

### Basic Example

```python
from bots.factChecker import bot

# Initialize the fact checker (reads from .env automatically)
fact_checker = bot()

# Fact-check a specific post
post_url = "https://bsky.app/profile/user/post/123"
result = fact_checker.fact_check_post(post_url)

# Get formatted response for Bluesky
reply_text = fact_checker.format_bluesky_reply(result)
print(reply_text)

# Or do the complete workflow (fact-check + reply)
success = fact_checker.post_fact_check_reply(post_url)
```

### Live Monitoring

```python
from daemon import Oracle

# Start live monitoring for mentions
oracle = Oracle()
oracle.monitor_mentions()  # Runs 24/7 monitoring
```

### Response Format

The bot returns structured JSON responses:

```json
{
    "thinking": "Step-by-step analysis of claims and sources",
    "status": "TRUE|FALSE|MISLEADING|UNVERIFIABLE|NO_CLAIMS",
    "category": "POLITICAL|HEALTH|SCIENCE|NEWS|OPINION|CLIMATE|VACCINE|ELECTION|CONSPIRACY|CELEBRITY|FINANCE|POPCULTURE|TECHNOLOGY|SPORTS|OTHER",
    "response": "Professional response suitable for Bluesky reply",
    "content_analysis": {
        "emotional_tone": "NEUTRAL|ANGRY|FEARFUL|URGENT|SENSATIONAL|...",
        "contains_statistics": true,
        "contains_quotes": false,
        "uses_absolutes": true,
        "creates_urgency": false
    }
}
```

## Prompt Engineering

The bot uses a carefully crafted prompt (`prompt/prompt.txt`) that:

- Instructs the model to think step-by-step
- Defines clear classification criteria (TRUE/FALSE/MISLEADING/UNVERIFIABLE)
- Emphasizes natural source attribution (no numbered citations)
- Analyzes content patterns for misinformation research
- Focuses on substantial errors vs. minor variations
- Maintains professional but conversational tone
- Limits response length for social media (under 250 characters)

## Response Guidelines

- **TRUE**: Core claims are factually accurate (allows minor variations)
- **FALSE**: Claims contain significant factual errors
- **MISLEADING**: Accurate facts presented in a distorted way
- **UNVERIFIABLE**: Claims cannot be confirmed with available sources
- **NO_CLAIMS**: Post contains no specific factual claims to verify

## Features

### Core Functionality
- **AI-Powered Fact-Checking**: Uses Google Gemini with web search for real-time verification
- **Smart Thread Analysis**: Fact-checks the post being replied to, not the mention request
- **Content Pattern Analysis**: Analyzes emotional tone, linguistic patterns, and misinformation markers
- **Privacy-First**: No personal data or post content stored, only anonymized analytics

### Analytics & Research
- **BigQuery Integration**: Logs anonymized fact-checking analytics for misinformation research
- **Content Classification**: Categorizes posts by topic and emotional characteristics
- **Performance Metrics**: Tracks response times, accuracy patterns, and usage statistics
- **Research Ready**: Structured data suitable for academic misinformation studies

### Deployment Ready
- **Live Monitoring**: 24/7 mention detection and automatic responses
- **Render Compatible**: Ready for cloud deployment with provided configuration
- **Robust Error Handling**: Graceful fallbacks and comprehensive logging
- **Environment Driven**: All configuration through environment variables

## How It Works

1. **Mention Detection**: Bot monitors Bluesky for mentions using AT Protocol notifications
2. **Thread Analysis**: When mentioned, retrieves the post being replied to (not the mention itself)
3. **AI Fact-Check**: Sends content to Google Gemini with web search for real-time verification
4. **Response Generation**: Creates concise, professional fact-check response (under 250 chars)
5. **Analytics Logging**: Records anonymized patterns and metrics to BigQuery
6. **Reply Posting**: Automatically posts fact-check response as a reply

## Deployment

### Render (Recommended)

1. Connect your GitHub repository to Render
2. Use the provided `render.yaml` configuration
3. Set environment variables in Render dashboard
4. Deploy as a background worker service


## Contributing

Please feel free to contribute to this project. 

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

