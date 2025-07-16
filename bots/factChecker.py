import json
import logging
import os
import time
import uuid
import pandas as pd
import requests
from datetime import datetime
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from clients.gemini import Client as GeminiClient
from clients.bluesky import Client as BlueskyClient
from clients.bigQuery import Client as BigQueryClient

# Load environment variables
load_dotenv(override=True)

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
        
        # In-memory mapping of post URIs to fact-check IDs for source retrieval
        self.post_to_factcheck_map = {}
        
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
        
    def fact_check_post(self, post_url: str, max_retries: int = 3) -> Dict[str, Any]:
        """
        Fact-check a Bluesky post with URL validation and retry logic
        """
        logger.info(f"Starting fact-check with URL validation (max {max_retries} attempts)")
        
        for attempt in range(max_retries):
            logger.info(f"Fact-check attempt {attempt + 1}/{max_retries}")
            
            try:
                result = self._fact_check_attempt(post_url)
                
                if "error" in result:
                    logger.warning(f"Attempt {attempt + 1} failed with error: {result['error']}")
                    continue
                
                # Validate source URLs with smarter thresholds
                sources = result.get('sources', [])
                invalid_urls = self._validate_source_urls(sources)
                
                # Calculate success rate
                total_sources = len(sources)
                invalid_count = len(invalid_urls)
                valid_count = total_sources - invalid_count
                success_rate = (valid_count / total_sources * 100) if total_sources > 0 else 0
                
                # Accept if no sources (general statements) or ≥50% sources work
                if total_sources == 0 or success_rate >= 50:
                    if total_sources == 0:
                        logger.info(f"Attempt {attempt + 1} successful - no specific sources to validate")
                    else:
                        logger.info(f"Attempt {attempt + 1} successful - {valid_count}/{total_sources} sources validated ({success_rate:.1f}%)")
                    return result
                
                # Only retry if success rate is too low (<50%) and we have sources
                logger.warning(f"Attempt {attempt + 1}: Only {valid_count}/{total_sources} sources valid ({success_rate:.1f}%) - below 50% threshold")
                for url in invalid_urls:
                    logger.warning(f"  Invalid URL: {url}")
                
                # If this is the last attempt, accept anyway to avoid inconclusive results
                if attempt == max_retries - 1:
                    logger.warning(f"Final attempt - accepting result despite low source validation rate")
                    return result
                    
                # Wait a bit before retry
                time.sleep(2)
                
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed with exception: {e}")
                if attempt == max_retries - 1:
                    break
                time.sleep(2)
        
        # All attempts failed - return inconclusive response
        logger.warning("All fact-check attempts failed - returning inconclusive response")
        return self._create_inconclusive_response()
    
    def _fact_check_attempt(self, post_url: str) -> Dict[str, Any]:
        """
        Single fact-check attempt without validation
        """
        start_time = time.time()
        
        # Get thread context and target post
        thread_data = self.bluesky_client.get_thread_chain(post_url)
        if not thread_data:
            return {"error": "Could not retrieve post data"}
            
        # Load prompt template from file
        with open(self.prompt_file, 'r') as f:
            prompt_template = f.read()
            
        # Format prompt with structured thread data
        request_info = thread_data.get("request", {})
        target_info = thread_data.get("target", {})
        context_info = thread_data.get("context", {})
        
        from datetime import datetime
        
        prompt = prompt_template.format(
            current_date=datetime.now().strftime("%Y-%m-%d"),
            request_type=request_info.get("type", "fact_check"),
            requester=request_info.get("requester", "@unknown").replace("@", ""),
            request_instruction=request_info.get("instruction", "fact check this"),
            target_content=target_info.get("content", ""),
            target_author=target_info.get("author", "@unknown").replace("@", ""),
            target_post_type=target_info.get("post_type", "statement"),
            conversation_summary=context_info.get("thread_summary", "No additional context")
        )
        
        # Query Gemini for fact-check
        response = self.gemini_client.generate(prompt)
        
        # Parse JSON response
        try:
            parsed_result = self._parse_json_response(response, "gemini", {})
            
            # Log to BigQuery and get fact-check ID
            fact_check_id = self._log_to_bigquery(post_url, thread_data, parsed_result, start_time)
            
            # Add fact-check ID to result for source retrieval
            parsed_result['fact_check_id'] = fact_check_id
            
            return parsed_result
        except Exception as e:
            logger.error(f"Failed to parse response: {e}")
            return {"error": f"Failed to parse response: {str(e)}", "raw_response": response}
    
    def _parse_json_response(self, json_str_to_parse: str, api_key_snippet: str, doi_mapping: dict) -> dict:
        """
        Robustly parses a JSON string response from the Gemini model with enhanced error handling
        """
        parsed_json = None
        
        try:
            # Try direct JSON parsing first
            parsed_json = json.loads(json_str_to_parse)
            logger.info("Successfully parsed JSON directly.")
            return parsed_json
        except json.JSONDecodeError as e:
            logger.warning(f"Direct JSON parsing failed: {e}")
        
        # Enhanced extraction with cleanup
        try:
            # Clean up common JSON formatting issues
            cleaned_json = self._clean_json_string(json_str_to_parse)
            
            # Extract JSON object
            start_idx = cleaned_json.find('{')
            if start_idx == -1:
                raise ValueError("No JSON object found in response")
            
            # Find matching closing brace
            brace_count = 0
            end_idx = start_idx
            for i, char in enumerate(cleaned_json[start_idx:], start_idx):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break
            
            if brace_count != 0:
                # Fallback to simple method
                end_idx = cleaned_json.rfind('}') + 1
            
            json_str_extracted = cleaned_json[start_idx:end_idx]
            
            # Try parsing the extracted JSON
            parsed_json = json.loads(json_str_extracted)
            logger.info("Successfully parsed JSON after cleanup and extraction.")
            return parsed_json
            
        except (json.JSONDecodeError, ValueError) as e:
            # Final fallback - try to create a minimal valid response
            logger.error(f"All JSON parsing attempts failed: {e}")
            logger.error(f"Raw response (first 500 chars): {json_str_to_parse[:500]}")
            
            # Debug: Log the cleaned JSON as well
            try:
                cleaned_debug = self._clean_json_string(json_str_to_parse)
                logger.error(f"Cleaned JSON (first 500 chars): {cleaned_debug[:500]}")
                
                # Try to find where exactly it fails
                start_idx = cleaned_debug.find('{')
                if start_idx != -1:
                    logger.error(f"JSON starts at position {start_idx}")
                    # Show a bit more context around the failure point
                    if hasattr(e, 'pos'):
                        error_pos = getattr(e, 'pos', 0)
                        logger.error(f"Error at position {error_pos}")
                        context_start = max(0, error_pos - 50)
                        context_end = min(len(cleaned_debug), error_pos + 50)
                        logger.error(f"Context around error: {cleaned_debug[context_start:context_end]}")
            except Exception as debug_e:
                logger.error(f"Debug logging failed: {debug_e}")
            
            # Return a fallback response
            return {
                "thinking": "JSON parsing failed - using fallback response",
                "status": "UNVERIFIABLE", 
                "category": "OTHER",
                "response": "Error processing fact-check response. Please try again.",
                "sources": [],
                "content_analysis": {
                    "emotional_tone": "NEUTRAL",
                    "contains_statistics": False,
                    "contains_quotes": False,
                    "contains_dates": False,
                    "uses_absolutes": False,
                    "creates_urgency": False,
                    "appeals_to_authority": False,
                    "personal_anecdote": False
                }
            }
    
    def _clean_json_string(self, json_str: str) -> str:
        """Clean up common JSON formatting issues"""
        import re
        
        # Remove common prefixes/suffixes that break JSON
        cleaned = json_str.strip()
        
        # Remove markdown code blocks
        if cleaned.startswith('```json'):
            cleaned = cleaned[7:]
        if cleaned.startswith('```'):
            cleaned = cleaned[3:]
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3]
        
        # Remove common text before JSON
        prefixes_to_remove = [
            "Here's the fact-check response:",
            "Here is the response:",
            "Response:",
            "JSON:",
        ]
        
        for prefix in prefixes_to_remove:
            if cleaned.lower().startswith(prefix.lower()):
                cleaned = cleaned[len(prefix):].strip()
        
        # Handle control characters and escape sequences
        # Replace problematic control characters
        cleaned = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', cleaned)
        
        # Fix @ symbols that can break JSON parsing
        # Escape @ symbols that appear unescaped in string values
        cleaned = self._fix_at_symbols(cleaned)
        
        # Remove citation brackets that break JSON
        cleaned = self._remove_citation_brackets(cleaned)
        
        # Fix common JSON escaping issues
        # Handle unescaped quotes within strings (basic fix)
        cleaned = self._fix_unescaped_quotes(cleaned)
        
        # Don't escape newlines - JSON parsing can handle proper line breaks
        # Only escape problematic characters that are actually inside string values
        cleaned = re.sub(r'[\r\t]', ' ', cleaned)  # Replace tabs and carriage returns with spaces
        
        return cleaned.strip()
    
    def _remove_citation_brackets(self, json_str: str) -> str:
        """Remove citation brackets like [1], [2, 3] from JSON string values"""
        import re
        
        try:
            # Remove citation patterns from anywhere in the JSON
            # Patterns like [1], [2, 3], [i], [ii], [a], [b], etc.
            cleaned = re.sub(r'\s*\[\s*[a-zA-Z0-9]+(?:\s*,\s*[a-zA-Z0-9]+)*\s*\]', '', json_str)
            return cleaned
            
        except Exception as e:
            # If anything fails, return original
            return json_str
    
    def _fix_at_symbols(self, json_str: str) -> str:
        """Fix @ symbols that can break JSON parsing by removing them from string values"""
        import re
        
        try:
            # Pattern to find JSON string values and remove @ symbols from them
            def clean_string_value(match):
                field_name = match.group(1)
                value_content = match.group(2)
                # Remove @ symbols from the value content
                cleaned_value = value_content.replace('@', '')
                return f'"{field_name}": "{cleaned_value}"'
            
            # Match JSON field patterns like "field": "value@with@symbols"
            pattern = r'"([^"]+)":\s*"([^"]*@[^"]*)"'
            cleaned = re.sub(pattern, clean_string_value, json_str)
            
            return cleaned
            
        except Exception as e:
            # If anything fails, return original
            return json_str
    
    def _fix_unescaped_quotes(self, json_str: str) -> str:
        """Fix unescaped quotes within JSON string values"""
        import re
        
        try:
            # Simple approach: escape all unescaped quotes except field boundaries
            lines = json_str.split('\n')
            fixed_lines = []
            
            for line in lines:
                # Skip lines that don't have JSON field patterns
                if '": "' not in line:
                    fixed_lines.append(line)
                    continue
                    
                # For lines with JSON fields, be more careful
                # Pattern: find the value part of "field": "value"
                match = re.match(r'^(\s*"[^"]+"\s*:\s*")([^"]*(?:[^\\"]|\\.)*)("\s*,?\s*)$', line)
                if match:
                    prefix = match.group(1)  # "field": "
                    value = match.group(2)   # the value content
                    suffix = match.group(3)  # ",
                    
                    # Escape unescaped quotes in the value
                    fixed_value = re.sub(r'(?<!\\)"', '\\"', value)
                    line = prefix + fixed_value + suffix
                
                fixed_lines.append(line)
            
            return '\n'.join(fixed_lines)
            
        except Exception as e:
            # If anything fails, return original
            return json_str
    
    def _validate_source_urls(self, sources: List[Dict[str, Any]]) -> List[str]:
        """
        Validate all source URLs and return list of invalid ones
        """
        invalid_urls = []
        
        for source in sources:
            url = source.get('url', '')
            if not url:
                continue
                
            if not self._is_valid_url(url):
                invalid_urls.append(url)
        
        return invalid_urls
    
    def _is_valid_url(self, url: str) -> bool:
        """
        Check if a URL is valid and accessible
        """
        try:
            # Basic URL format validation
            if not url.startswith(('http://', 'https://')):
                logger.warning(f"Invalid URL format: {url}")
                return False
            
            # Make HEAD request to check if URL exists
            logger.debug(f"Validating URL: {url}")
            response = requests.head(
                url, 
                timeout=10,
                allow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0 (compatible; FactChecker/1.0)'}
            )
            
            # Check for successful response codes
            if response.status_code in [200, 301, 302, 303]:
                logger.debug(f"URL valid: {url} (status: {response.status_code})")
                return True
            else:
                logger.warning(f"URL returned error status {response.status_code}: {url}")
                return False
                
        except requests.exceptions.Timeout:
            logger.warning(f"URL timeout: {url}")
            return False
        except requests.exceptions.ConnectionError:
            logger.warning(f"URL connection error: {url}")
            return False
        except requests.exceptions.RequestException as e:
            logger.warning(f"URL request failed: {url} - {e}")
            return False
        except Exception as e:
            logger.warning(f"URL validation error: {url} - {e}")
            return False
    
    def _create_inconclusive_response(self) -> Dict[str, Any]:
        """
        Create a fallback response when all attempts fail
        """
        return {
            "thinking": "Multiple attempts to verify sources failed - unable to provide reliable fact-check",
            "status": "UNVERIFIABLE",
            "category": "OTHER",
            "response": "Unable to achieve conclusive results. The claims could not be verified with reliable sources at this time.",
            "sources": [],
            "content_analysis": {
                "emotional_tone": "NEUTRAL",
                "contains_statistics": False,
                "contains_quotes": False,
                "contains_dates": False,
                "uses_absolutes": False,
                "creates_urgency": False,
                "appeals_to_authority": False,
                "personal_anecdote": False
            },
            "fact_check_id": str(uuid.uuid4())
        }
    
    def _log_to_bigquery(self, post_url: str, thread_data: Dict[str, Any], result: Dict[str, Any], start_time: float) -> str:
        """Log fact-check result to BigQuery and return the fact-check ID"""
        fact_check_id = str(uuid.uuid4())
        
        if not self.bq_client:
            return fact_check_id
        
        try:
            post_text = thread_data["replying_to"]["text"]
            now = datetime.now()
            
            # Get content analysis from LLM response or fallback to manual analysis
            content_analysis = result.get('content_analysis', {})
            
            # Use the fact-check ID generated at method start
            
            record = {
                'id': fact_check_id,
                'timestamp': now,
                'thinking': result.get('thinking', ''),
                'status': result.get('status', ''),
                'category': result.get('category', ''),
                'response': result.get('response', ''),
                'response_length': len(result.get('response', '')),
                'sources': json.dumps(result.get('sources', [])),  # Store sources as JSON string
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
            logger.info(f"Successfully logged fact-check to BigQuery with ID: {fact_check_id}")
            
        except Exception as e:
            logger.error(f"Failed to log to BigQuery: {e}")
        
        return fact_check_id
    
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
        # Remove patterns like [1], [2, 3], [i], [ii], [a], [b], etc. with optional preceding space
        response = re.sub(r'\s*\[\s*[a-zA-Z0-9]+(?:\s*,\s*[a-zA-Z0-9]+)*\s*\]', '', response)
        
        # Also clean up the response during JSON processing before it gets here
        if isinstance(fact_check_result.get("response"), str):
            fact_check_result["response"] = re.sub(r'\s*\[\s*[a-zA-Z0-9]+(?:\s*,\s*[a-zA-Z0-9]+)*\s*\]', '', fact_check_result["response"])
        
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
        reply_result = self.bluesky_client.post_reply(original_post_url, reply_text)
        
        if reply_result and reply_result != True:  # Got a URI back
            # Store mapping for source retrieval
            fact_check_id = result.get('fact_check_id')
            if fact_check_id:
                self.post_to_factcheck_map[reply_result] = fact_check_id
                logger.info(f"Stored mapping: {reply_result} -> {fact_check_id}")
            
            logger.info(f"Successfully posted fact-check reply to {original_post_url}")
            return True
        elif reply_result == True:  # Old-style boolean return
            logger.info(f"Successfully posted fact-check reply to {original_post_url}")
            return True
        else:
            logger.error(f"Failed to post reply to {original_post_url}")
            return False
    
    def get_sources_by_id(self, fact_check_id: str) -> list:
        """
        Retrieve sources for a specific fact-check ID from BigQuery
        
        Args:
            fact_check_id: The UUID of the fact-check record
            
        Returns:
            List of source dictionaries, empty list if not found or error
        """
        if not self.bq_client:
            logger.warning("BigQuery not available for source retrieval")
            return []
        
        try:
            dataset_id = os.getenv('BIGQUERY_DATASET_ID', 'dataset')
            table_id = os.getenv('BIGQUERY_TABLE_ID', 'fact-checker')
            project_id = os.getenv('BIGQUERY_PROJECT_ID')
            
            query = f"""
            SELECT sources 
            FROM `{project_id}.{dataset_id}.{table_id}` 
            WHERE id = '{fact_check_id}'
            LIMIT 1
            """
            
            result = self.bq_client.query(query)
            
            if len(result) > 0 and 'sources' in result.columns:
                sources_json = result.iloc[0]['sources']
                if sources_json:
                    try:
                        return json.loads(sources_json)
                    except json.JSONDecodeError:
                        # Try to clean the JSON before parsing
                        cleaned_json = self._clean_json_string(sources_json)
                        try:
                            return json.loads(cleaned_json)
                        except json.JSONDecodeError:
                            logger.warning(f"Could not parse sources JSON: {sources_json[:100]}")
                            return []
            
            return []
            
        except Exception as e:
            logger.error(f"Failed to retrieve sources from BigQuery: {e}")
            return []
    
    def format_sources_response(self, sources: list) -> str:
        """
        Format sources list into a readable Bluesky reply with article names and publishers only
        
        Args:
            sources: List of source dictionaries from BigQuery
            
        Returns:
            Formatted string with sources, or error message
        """
        if not sources:
            return "No sources found for this fact-check."
        
        response_parts = ["Sources for this fact-check:"]
        
        for i, source in enumerate(sources[:5], 1):  # Limit to 5 sources
            title = source.get('title', 'Unknown Article')
            publisher = source.get('publisher', 'Unknown Publisher')
            
            source_line = f"{i}. {title} - {publisher}"
            response_parts.append(source_line)
        
        full_response = "\n\n".join(response_parts)
        
        # Truncate if too long for Bluesky (300 char limit)
        if len(full_response) > 280:
            # Try shorter format
            response_parts = ["Sources:"]
            for i, source in enumerate(sources[:3], 1):
                title = source.get('title', f'Source {i}')
                publisher = source.get('publisher', '')
                if publisher:
                    response_parts.append(f"{i}. {title[:40]}... - {publisher}")
                else:
                    response_parts.append(f"{i}. {title[:50]}...")
            
            full_response = "\n\n".join(response_parts)
            
            # Final truncation if still too long
            if len(full_response) > 280:
                full_response = full_response[:277] + "..."
        
        return full_response