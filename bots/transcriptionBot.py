import json
import logging
import os
import time
import uuid
import requests
from datetime import datetime
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from clients.gemini import Client as GeminiClient
from clients.bluesky import Client as BlueskyClient

# Load environment variables
load_dotenv(override=True)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MediaProcessingBot:
    """Bluesky media processing bot that summarizes audio/video and describes/reads images from posts"""
    
    def __init__(self, gemini_api_key: str = None, bluesky_username: str = None, bluesky_password: str = None, prompt_file: str = "prompt/prompt.txt"):
        """Initialize media processing bot with API credentials (loads from .env if not provided)"""
        # Load from environment variables if not provided
        self.gemini_api_key = gemini_api_key or os.getenv('GEMINI_API_KEY')
        self.bluesky_username = bluesky_username or os.getenv('BLUESKY_USERNAME')
        self.bluesky_password = bluesky_password or os.getenv('BLUESKY_PASSWORD')
        self.prompt_file = prompt_file  
        
        # In-memory mapping of post URIs to transcription IDs for analytics
        self.post_to_transcription_map = {}
        
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
    
    def extract_language_from_mention(self, mention_text: str) -> str:
        """Extract requested language from mention text - PROXIMITY-BASED detection only"""
        if not mention_text:
            return "English"
        
        import re
        
        # Language mappings
        language_map = {
            # Full names
            'spanish': 'Spanish', 'español': 'Spanish',
            'french': 'French', 'français': 'French', 
            'german': 'German', 'deutsch': 'German',
            'chinese': 'Chinese', '中文': 'Chinese',
            'japanese': 'Japanese', '日本語': 'Japanese',
            'portuguese': 'Portuguese', 'português': 'Portuguese',
            'italian': 'Italian', 'italiano': 'Italian',
            'korean': 'Korean', '한국어': 'Korean',
            'arabic': 'Arabic', 'العربية': 'Arabic',
            
            # ISO codes
            'es': 'Spanish', 'fr': 'French', 'de': 'German',
            'zh': 'Chinese', 'ja': 'Japanese', 'pt': 'Portuguese', 
            'it': 'Italian', 'ko': 'Korean', 'ar': 'Arabic'
        }
        
        text_lower = mention_text.lower().strip()
        
        # 1. HIGHEST PRIORITY: Explicit structured syntax (anywhere in text)
        explicit_patterns = [
            r'lang(?:uage)?[:\s]+([a-z]{2,})',  # lang:es, language: spanish
            r'\[([a-z]{2,})\]',                 # [spanish]
            r'\{([a-z]{2,})\}',                 # {es}
        ]
        
        for pattern in explicit_patterns:
            match = re.search(pattern, text_lower)
            if match:
                lang_key = match.group(1).lower()
                if lang_key in language_map:
                    return language_map[lang_key]
        
        # 2. PROXIMITY-BASED DETECTION: Language must be within 1-2 words of @mention
        # Find bot mention position
        bot_mention_patterns = [
            r'@bskyscribe\.bsky\.social',
            r'@bskyscribe',
            r'@bot'
        ]
        
        mention_positions = []
        for pattern in bot_mention_patterns:
            for match in re.finditer(pattern, text_lower):
                mention_positions.append(match.start())
        
        if not mention_positions:
            # No bot mention found, default to English
            return "English"
        
        # Extract words and their positions
        words_with_positions = []
        for match in re.finditer(r'\b\w+\b', text_lower):
            words_with_positions.append((match.group(), match.start(), match.end()))
        
        # For each mention, check words within strict proximity window
        for mention_pos in mention_positions:
            # Find the word index closest to the mention
            mention_word_index = None
            min_distance = float('inf')
            
            for i, (word, start_pos, end_pos) in enumerate(words_with_positions):
                # Calculate distance from mention to word
                word_center = (start_pos + end_pos) / 2
                distance = abs(word_center - mention_pos)
                if distance < min_distance:
                    min_distance = distance
                    mention_word_index = i
            
            if mention_word_index is None:
                continue
            
            # Check ONLY 1-2 words AFTER the mention (safer, more natural)
            proximity_range = 2
            start_idx = mention_word_index + 1  # Start after mention
            end_idx = min(len(words_with_positions), mention_word_index + proximity_range + 1)
            
            nearby_words = []
            for i in range(start_idx, end_idx):
                nearby_words.append(words_with_positions[i][0])
            
            # Check if any nearby words are language keywords
            for word in nearby_words:
                if word in language_map:
                    # Additional validation for short ISO codes
                    if len(word) <= 3:
                        # For ISO codes, ensure it's not part of common English words
                        if word in ['it', 'is', 'in', 'to', 'be', 'we', 'he', 'me', 'no', 'so', 'go', 'do']:
                            continue
                    return language_map[word]
        
        # 3. NATURAL LANGUAGE PATTERNS (with proximity)
        natural_patterns = [
            r'in\s+([a-z]{2,})(?:\s|$|[.,!?])',    # "in spanish"
            r'to\s+([a-z]{2,})(?:\s|$|[.,!?])',    # "to french"
            r'as\s+([a-z]{2,})(?:\s|$|[.,!?])',    # "as german"
        ]
        
        for mention_pos in mention_positions:
            # Check natural patterns within proximity of mentions
            for pattern in natural_patterns:
                for match in re.finditer(pattern, text_lower):
                    if abs(match.start() - mention_pos) <= 20:  # Strict proximity
                        lang_key = match.group(1).lower()
                        if lang_key in language_map:
                            return language_map[lang_key]
        
        # Default: No language detected near bot mentions
        return "English"
    
    def transcribe_post(self, post_url: str, language: str = "English", max_retries: int = 3) -> Dict[str, Any]:
        """
        Transcribe media content from a Bluesky post
        """
        logger.info(f"Starting transcription in {language} (max {max_retries} attempts)")
        
        for attempt in range(max_retries):
            logger.debug(f"Transcription attempt {attempt + 1}/{max_retries}")
            
            try:
                result = self._transcription_attempt(post_url, language)
                
                if "error" in result:
                    logger.warning(f"Attempt {attempt + 1} failed with error: {result['error']}")
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    else:
                        return result  # Return error after final attempt
                
                # Success - return result
                logger.info(f"Transcription successful on attempt {attempt + 1}")
                return result
                
            except Exception as e:
                logger.error(f"Transcription attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                else:
                    return {"error": f"Transcription failed after {max_retries} attempts: {str(e)}"}
    
    def _transcription_attempt(self, post_url: str, language: str = "English") -> Dict[str, Any]:
        """
        Single transcription attempt
        """
        start_time = time.time()
        
        # Get parent post with media
        post_data = self.bluesky_client.get_parent_post_with_media(post_url)
        if not post_data:
            return {"error": "Could not retrieve post data"}
        
        # Check for error (no media found)
        if "error" in post_data:
            return post_data  # Return the error directly
        
        # Check if media is present
        media_items = post_data.get("media", [])
        if not media_items:
            return {"error": "No media found in post"}
        
        # Load prompt template from file
        with open(self.prompt_file, 'r') as f:
            prompt_template = f.read()
        
        # Format prompt with language
        formatted_prompt = prompt_template.format(language=language)
        
        # Process first media item (for now)
        media_item = media_items[0]
        media_url = media_item["url"]
        
        # Call Gemini media processing with structured output
        gemini_response = self.gemini_client.process_media(media_url, formatted_prompt)
        
        # Parse JSON response
        try:
            result = json.loads(gemini_response)
            logger.info(f"Transcription completed in {time.time() - start_time:.2f}s")
            return result
            
        except json.JSONDecodeError as e:
            return {"error": f"Failed to parse JSON response: {str(e)}"}
    
    def _fact_check_attempt_with_retry(self, post_url: str, retry_configs: dict, error_log: dict) -> Dict[str, Any]:
        """
        Enhanced fact-check attempt with error-specific handling
        """
        return self._fact_check_attempt(post_url)
    
    def _classify_error(self, error_message: str) -> str:
        """
        Classify error type for appropriate retry strategy
        """
        error_lower = error_message.lower()
        
        if 'could not retrieve post data' in error_lower or 'thread' in error_lower:
            return 'post_retrieval'
        elif 'json' in error_lower or 'parse' in error_lower or 'decode' in error_lower:
            return 'json_parsing'
        elif 'network' in error_lower or 'connection' in error_lower or 'timeout' in error_lower:
            return 'network_error'
        elif 'rate limit' in error_lower or 'too many requests' in error_lower:
            return 'rate_limit'
        else:
            return 'unknown'
    
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
        
        # Log raw response for debugging
        logger.info(f"Response length: {len(response)} characters")
        
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
            logger.debug("JSON parsed directly")
            return parsed_json
        except json.JSONDecodeError as e:
            logger.debug(f"Direct JSON parsing failed: {e}")
        
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
            logger.debug("JSON parsed after cleanup")
            return parsed_json
            
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"JSON parsing failed: {e}")
            logger.debug(f"Raw response (first 500 chars): {json_str_to_parse[:500]}")
            
            # Debug logging for troubleshooting
            if hasattr(e, 'pos'):
                error_pos = getattr(e, 'pos', 0)
                logger.debug(f"Parse error at position {error_pos}")
                context = json_str_to_parse[max(0, error_pos-50):error_pos+50]
                logger.debug(f"Error context: ...{context}...")
            
            # Try manual parsing for common patterns
            try:
                manual_result = self._manual_json_extraction(json_str_to_parse)
                if manual_result:
                    logger.info("Successfully extracted JSON manually")
                    return manual_result
            except Exception as manual_e:
                logger.debug(f"Manual extraction also failed: {manual_e}")
            
            # Final fallback - try to create a minimal valid response
            return {
                "thinking": "JSON parsing failed - using fallback response",
                "substantially_accurate": False,
                "category": "OTHER",
                "response_character_count": 50,
                "response": "Error processing fact-check response. Please try again.",
                "sources": []
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
        
        # Fix common JSON formatting issues
        cleaned = cleaned.strip()
        
        # Fix missing commas before closing braces/brackets
        cleaned = re.sub(r'"\s*\n\s*}', '"\n}', cleaned)
        cleaned = re.sub(r'"\s*\n\s*]', '"\n]', cleaned)
        
        # Fix trailing commas
        cleaned = re.sub(r',(\s*[}\]])', r'\1', cleaned)
        
        # Fix missing quotes around keys
        cleaned = re.sub(r'(\w+):', r'"\1":', cleaned)
        
        # Fix single quotes to double quotes
        cleaned = re.sub(r"'([^']*)'", r'"\1"', cleaned)
        
        # Fix escaped characters that break JSON
        cleaned = cleaned.replace('\\"', '"').replace("\\'", "'")
        
        # Remove any non-printable characters
        cleaned = ''.join(char for char in cleaned if ord(char) >= 32 or char in '\n\r\t')
        
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
                logger.debug(f"Invalid URL format: {url}")
                return False
            
            # Make HEAD request to check if URL exists
            response = requests.head(
                url, 
                timeout=10,
                allow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0 (compatible; FactChecker/1.0)'}
            )
            
            # Check for successful response codes
            if response.status_code in [200, 301, 302, 303]:
                logger.debug(f"URL valid: {url}")
                return True
            else:
                logger.warning(f"URL returned error status {response.status_code}: {url}")
                return False
                
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            logger.debug(f"URL unreachable: {url}")
            return False
        except requests.exceptions.RequestException as e:
            logger.debug(f"URL validation failed: {url} - {e}")
            return False
        except Exception as e:
            logger.debug(f"URL validation error: {url} - {e}")
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
            
            # Handle status field with error metadata
            status_value = result.get('status', '')
            if 'status_with_errors' in result:
                # Store error metadata in status field as JSON
                status_value = json.dumps(result['status_with_errors'])
            
            record = {
                'id': fact_check_id,
                'timestamp': now,
                'thinking': result.get('thinking', ''),
                'status': status_value,
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
            logger.debug(f"Logged fact-check to BigQuery: {fact_check_id}")
            
        except Exception as e:
            logger.error(f"BigQuery logging failed: {e}")
        
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
    
    def format_transcription_reply(self, transcription_result: Dict[str, Any]) -> str:
        """
        Format the JSON transcription result into a readable Bluesky reply
        
        Args:
            transcription_result: The parsed JSON result from transcription
            
        Returns:
            Formatted string for Bluesky reply
        """
        if "error" in transcription_result:
            return f"{transcription_result['error']}"
            
        # Use the response from the structured JSON output
        response = transcription_result.get("response", "Unable to process media content")
        
        # Clean up any unwanted characters but keep the response natural
        response = response.strip()
        
        return response
    
    def post_transcription_reply(self, original_post_url: str, mention_text: str = "") -> bool:
        """
        Complete workflow: transcribe media from a post and reply with results
        
        Args:
            original_post_url: URL of the post to transcribe
            mention_text: Text of the mention (for language detection)
            
        Returns:
            True if successful, False otherwise
        """
        # Extract language from mention
        language = self.extract_language_from_mention(mention_text)
        logger.info(f"Processing transcription request in {language}")
        
        # Perform transcription with language
        result = self.transcribe_post(original_post_url, language)
        
        # Format reply
        reply_text = self.format_transcription_reply(result)
        
        # Post reply
        reply_result = self.bluesky_client.post_reply(original_post_url, reply_text)
        
        if reply_result:
            logger.info(f"Posted transcription reply in {language}")
            return True
        else:
            logger.error(f"Failed to post reply")
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
            logger.debug("BigQuery not available for source retrieval")
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
                            logger.debug(f"Could not parse sources JSON: {sources_json[:50]}")
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
        if len(full_response) > 300:
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
    
    def _manual_json_extraction(self, response_text: str) -> dict:
        """Manual extraction for common JSON patterns when parsing fails"""
        import re
        
        # Try to extract key fields manually
        result = {}
        
        # Extract thinking field
        thinking_match = re.search(r'"thinking":\s*"([^"]+)"', response_text, re.DOTALL)
        if thinking_match:
            result["thinking"] = thinking_match.group(1)
        
        # Extract substantially_accurate
        accurate_match = re.search(r'"substantially_accurate":\s*(true|false)', response_text)
        if accurate_match:
            result["substantially_accurate"] = accurate_match.group(1) == "true"
        
        # Extract category
        category_match = re.search(r'"category":\s*"([^"]+)"', response_text)
        if category_match:
            result["category"] = category_match.group(1)
        
        # Extract response
        response_match = re.search(r'"response":\s*"([^"]+)"', response_text, re.DOTALL)
        if response_match:
            result["response"] = response_match.group(1)
            result["response_character_count"] = len(result["response"])
        
        # Extract sources array (simplified)
        sources_match = re.search(r'"sources":\s*\[([^\]]+)\]', response_text, re.DOTALL)
        if sources_match:
            try:
                # Try to parse individual source objects
                sources_content = sources_match.group(1)
                sources = []
                
                # Find individual source objects
                source_objects = re.findall(r'\{([^}]+)\}', sources_content)
                for source_obj in source_objects:
                    source = {}
                    title_match = re.search(r'"title":\s*"([^"]+)"', source_obj)
                    if title_match:
                        source["title"] = title_match.group(1)
                    
                    publisher_match = re.search(r'"publisher":\s*"([^"]+)"', source_obj)
                    if publisher_match:
                        source["publisher"] = publisher_match.group(1)
                    
                    relevance_match = re.search(r'"relevance":\s*"([^"]+)"', source_obj)
                    if relevance_match:
                        source["relevance"] = relevance_match.group(1)
                    
                    if source:
                        sources.append(source)
                
                result["sources"] = sources
            except:
                result["sources"] = []
        else:
            result["sources"] = []
        
        # Only return if we extracted at least the essential fields
        if "response" in result and "substantially_accurate" in result:
            return result
        
        return None
    
    def _reduce_response_length(self, original_response: str) -> str:
        """
        Use length reduction prompt to compress a response while preserving all key information
        """
        logger.debug(f"Attempting to reduce response length from {len(original_response)} chars")
        
        # Load length reduction prompt template
        length_reduction_prompt_file = "prompt/length_reduction.txt"
        try:
            with open(length_reduction_prompt_file, 'r') as f:
                prompt_template = f.read()
        except FileNotFoundError:
            logger.error(f"Length reduction prompt file not found: {length_reduction_prompt_file}")
            # Fallback to simple truncation
            return original_response[:285] + "..." if len(original_response) > 285 else original_response
        
        # Format prompt with original response
        prompt = prompt_template.format(original_response=original_response)
        
        # Query Gemini for length reduction
        try:
            reduced_response = self.gemini_client.generate(prompt)
            
            # Clean up the response (remove any extra whitespace/formatting)
            reduced_response = reduced_response.strip()
            
            logger.debug(f"Response reduced to {len(reduced_response)} chars")
            
            # Verify it's actually shorter and within limit
            if len(reduced_response) <= 285 and len(reduced_response) < len(original_response):
                return reduced_response
            else:
                logger.warning(f"Length reduction failed: {len(reduced_response)} chars (target: 285)")
                # Fallback to truncation
                return original_response[:285] + "..." if len(original_response) > 285 else original_response
                
        except Exception as e:
            logger.error(f"Error during length reduction: {e}")
            # Fallback to simple truncation
            return original_response[:285] + "..." if len(original_response) > 285 else original_response