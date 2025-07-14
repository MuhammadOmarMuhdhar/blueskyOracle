import json
import logging
import os
import time
import uuid
import pandas as pd
from datetime import datetime
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from clients.gemini import Client as GeminiClient
from clients.bluesky import Client as BlueskyClient
from clients.bigQuery import Client as BigQueryClient

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class bot:
    """Bluesky fact-checking bot that analyzes posts and returns structured JSON responses"""
    
    def __init__(self, gemini_api_key: str = None, bluesky_username: str = None, bluesky_password: str = None, prompt_file: str = "prompt/prompt.txt"):
        """Initialize fact checker with API credentials (loads from .env if not provided)"""
        # Load from environment variables if not provided
        self.gemini_api_key = gemini_api_key or os.getenv('GEMINI_API_KEY')
        self.bluesky_username = bluesky_username or os.getenv('BLUESKY_USERNAME')
        self.bluesky_password = bluesky_password or os.getenv('BLUESKY_PASSWORD')
        self.prompt_file = prompt_file  
        
        
        # Validate required credentials
        if not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY not found in environment variables or parameters")
        if not self.bluesky_username:
            raise ValueError("BLUESKY_USERNAME not found in environment variables or parameters")
        if not self.bluesky_password:
            raise ValueError("BLUESKY_PASSWORD not found in environment variables or parameters")
        
        # Initialize clients
        self.gemini_client = GeminiClient(api_key=self.gemini_api_key)
        self.bluesky_client = BlueskyClient(username=self.bluesky_username, password=self.bluesky_password)
        self.bq_client = self._init_bigquery_client()
    
    def _init_bigquery_client(self):
        """Initialize BigQuery client if credentials are available"""
        try:
            credentials_json = json.loads(os.getenv('BIGQUERY_CREDENTIALS_JSON'))
            project_id = os.getenv('BIGQUERY_PROJECT_ID')
            return BigQueryClient(credentials_json, project_id)
        except Exception as e:
            logger.warning(f"BigQuery not available: {e}")
            return None
        
    def fact_check_post(self, post_url: str) -> Dict[str, Any]:
        """
        Fact-check a Bluesky post and return structured JSON response
        """
        start_time = time.time()
        
        # Get thread context and target post
        thread_data = self.bluesky_client.get_thread_chain(post_url)
        if not thread_data:
            return {"error": "Could not retrieve post data"}
            
        # Load prompt template from file
        with open(self.prompt_file, 'r') as f:
            prompt_template = f.read()
            
        # Format prompt with thread data
        prompt = prompt_template.format(
            thread_context=thread_data.get("thread_context", ""),
            replying_to_text=thread_data["replying_to"]["text"],
            replying_to_author=thread_data["replying_to"]["author"]
        )
        
        # Query Gemini for fact-check
        response = self.gemini_client.generate(prompt)
        
        # Parse JSON response
        try:
            parsed_result = self._parse_json_response(response, "gemini", {})
            
            # Log to BigQuery
            self._log_to_bigquery(post_url, thread_data, parsed_result, start_time)
            
            return parsed_result
        except Exception as e:
            logger.error(f"Failed to parse response: {e}")
            return {"error": f"Failed to parse response: {str(e)}", "raw_response": response}
    
    def _parse_json_response(self, json_str_to_parse: str, api_key_snippet: str, doi_mapping: dict) -> dict:
        """
        Robustly parses a JSON string response from the Gemini model, handling the array format
        and converting numeric DOI/field keys back to original format.
        Uses simplified JSON extraction approach.
        """
        parsed_json = None
        try:
            # Try direct JSON parsing first
            parsed_json = json.loads(json_str_to_parse)
            logger.info("Successfully parsed JSON directly.")
        except json.JSONDecodeError as e:
            # Use the simpler extraction method from the second version
            start_idx = json_str_to_parse.find('{')
            if json_str_to_parse.find('[') >= 0 and (start_idx == -1 or json_str_to_parse.find('[') < start_idx):
                # Array format detected
                start_idx = json_str_to_parse.find('[')
                end_idx = json_str_to_parse.rfind(']') + 1
            else:
                # Object format
                end_idx = json_str_to_parse.rfind('}') + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str_extracted = json_str_to_parse[start_idx:end_idx]
                try:
                    parsed_json = json.loads(json_str_extracted)
                    logger.info("Successfully parsed JSON after extraction.")
                except json.JSONDecodeError as e_extract:
                    raise ValueError(f"Could not extract valid JSON from response for API key {api_key_snippet}: {e_extract}")
            else:
                raise ValueError(f"Could not find valid JSON object in response for API key {api_key_snippet}")
        
        return parsed_json
    
    def _log_to_bigquery(self, post_url: str, thread_data: Dict[str, Any], result: Dict[str, Any], start_time: float):
        """Log fact-check result to BigQuery"""
        if not self.bq_client:
            return
        
        try:
            post_text = thread_data["replying_to"]["text"]
            now = datetime.now()
            
            # Get content analysis from LLM response or fallback to manual analysis
            content_analysis = result.get('content_analysis', {})
            
            record = {
                'id': str(uuid.uuid4()),
                'timestamp': now,
                'thinking': result.get('thinking', ''),
                'status': result.get('status', ''),
                'category': result.get('category', ''),
                'response': result.get('response', ''),
                'response_length': len(result.get('response', '')),
                'processing_time_ms': int((time.time() - start_time) * 1000),
                'model_version': 'gemini-2.0-flash-v1',
                'day_of_week': now.strftime('%A').upper(),
                'hour_of_day': now.hour,
                'is_weekend': now.weekday() >= 5,
                
                # Content patterns from LLM analysis (with fallbacks)
                'emotional_tone': content_analysis.get('emotional_tone', self._detect_emotional_tone(post_text)),
                'contains_statistics': content_analysis.get('contains_statistics', self._contains_statistics(post_text)),
                'contains_quotes': content_analysis.get('contains_quotes', self._contains_quotes(post_text)),
                'contains_dates': content_analysis.get('contains_dates', self._contains_dates(post_text)),
                'uses_absolutes': content_analysis.get('uses_absolutes', self._uses_absolutes(post_text)),
                'creates_urgency': content_analysis.get('creates_urgency', self._creates_urgency(post_text)),
                'appeals_to_authority': content_analysis.get('appeals_to_authority', self._appeals_to_authority(post_text)),
                'personal_anecdote': content_analysis.get('personal_anecdote', self._personal_anecdote(post_text)),
                
                # Information structure (manual analysis)
                'has_external_links': 'http' in post_text.lower(),
                'has_images': False,  # Could analyze thread_data if needed
                'has_videos': False,  # Could analyze thread_data if needed
                'mention_count': post_text.count('@'),
                'hashtag_count': post_text.count('#'),
                'question_marks_count': post_text.count('?'),
                'exclamation_marks_count': post_text.count('!'),
                'all_caps_words_count': len([word for word in post_text.split() if word.isupper() and len(word) > 1])
            }
            
            # Save to BigQuery
            df = pd.DataFrame([record])
            dataset_id = os.getenv('BIGQUERY_DATASET_ID', 'fact_checks')
            table_id = os.getenv('BIGQUERY_TABLE_ID', 'responses')
            
            self.bq_client.append(df, dataset_id, table_id, create_if_not_exists=True)
            logger.info(f"Successfully logged fact-check to BigQuery")
            
        except Exception as e:
            logger.error(f"Failed to log to BigQuery: {e}")
    
    def _detect_emotional_tone(self, text: str) -> str:
        """Simple emotional tone detection"""
        text_lower = text.lower()
        angry_words = ['outrageous', 'disgusting', 'terrible', 'awful', 'hate', 'angry', 'furious']
        fearful_words = ['dangerous', 'scary', 'terrifying', 'threat', 'warning', 'beware']
        urgent_words = ['urgent', 'breaking', 'immediate', 'now', 'alert', 'emergency']
        sensational_words = ['shocking', 'unbelievable', 'incredible', 'amazing', 'stunning']
        
        if any(word in text_lower for word in angry_words):
            return 'ANGRY'
        elif any(word in text_lower for word in fearful_words):
            return 'FEARFUL'
        elif any(word in text_lower for word in urgent_words):
            return 'URGENT'
        elif any(word in text_lower for word in sensational_words):
            return 'SENSATIONAL'
        else:
            return 'NEUTRAL'
    
    def _contains_statistics(self, text: str) -> bool:
        """Check if text contains statistics/numbers"""
        import re
        # Look for percentages, numbers with units, etc.
        patterns = [r'\d+%', r'\d+\s*(million|billion|thousand)', r'\d+\.\d+', r'\$\d+']
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)
    
    def _contains_quotes(self, text: str) -> bool:
        """Check if text contains quoted speech"""
        return '"' in text or '"' in text or '"' in text or 'said' in text.lower()
    
    def _contains_dates(self, text: str) -> bool:
        """Check if text contains dates"""
        import re
        date_patterns = [
            r'\d{4}',  # Year
            r'\d{1,2}/\d{1,2}/\d{2,4}',  # Date format
            r'(january|february|march|april|may|june|july|august|september|october|november|december)',
            r'(today|yesterday|tomorrow|last week|next week)'
        ]
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in date_patterns)
    
    def _uses_absolutes(self, text: str) -> bool:
        """Check if text uses absolute terms"""
        absolutes = ['always', 'never', 'all', 'none', 'every', 'no one', 'everyone', 'everything', 'nothing']
        text_lower = text.lower()
        return any(absolute in text_lower for absolute in absolutes)
    
    def _creates_urgency(self, text: str) -> bool:
        """Check if text creates urgency"""
        urgent_phrases = ['breaking', 'urgent', 'immediate', 'act now', 'don\'t wait', 'hurry', 'quickly']
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in urgent_phrases)
    
    def _appeals_to_authority(self, text: str) -> bool:
        """Check if text appeals to authority"""
        authority_phrases = ['experts say', 'studies show', 'research proves', 'scientists confirm', 'doctors recommend']
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in authority_phrases)
    
    def _personal_anecdote(self, text: str) -> bool:
        """Check if text contains personal anecdotes"""
        anecdote_phrases = ['i know someone', 'my friend', 'my family', 'happened to me', 'i saw', 'i heard']
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in anecdote_phrases)
    
    def format_bluesky_reply(self, fact_check_result: Dict[str, Any]) -> str:
        """
        Format the JSON fact-check result into a readable Bluesky reply
        
        Args:
            fact_check_result: The parsed JSON result from fact-checking
            
        Returns:
            Formatted string for Bluesky reply
        """
        if "error" in fact_check_result:
            return f"Error: {fact_check_result['error']}"
            
        # Check if status is NO_CLAIMS
        if fact_check_result.get("status") == "NO_CLAIMS":
            return "Thanks for the mention! I didn't find any specific factual claims to verify in this post."
            
        # Use the conversational response from the model and clean up citations
        response = fact_check_result.get("response", "Unable to generate fact-check response")
        
        # Remove all types of numbered citations in brackets including preceding space
        import re
        # Remove patterns like [1], [2, 3], [1, 2], [2, 5], etc. with optional preceding space
        response = re.sub(r'\s*\[\s*\d+(?:\s*,\s*\d+)*\s*\]', '', response)
        
        # Remove quotation marks
        response = response.replace('"', '').replace("'", "")
        
        return response
    
    def post_fact_check_reply(self, original_post_url: str) -> bool:
        """
        Complete workflow: fact-check a post and reply with results
        
        Args:
            original_post_url: URL of the post to fact-check
            
        Returns:
            True if successful, False otherwise
        """
        # Perform fact-check
        result = self.fact_check_post(original_post_url)
        
        # Format reply
        reply_text = self.format_bluesky_reply(result)
        
        # Post reply
        success = self.bluesky_client.post_reply(original_post_url, reply_text)
        
        if success:
            logger.info(f"Successfully posted fact-check reply to {original_post_url}")
        else:
            logger.error(f"Failed to post reply to {original_post_url}")
            
        return success