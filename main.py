import json
import logging
import os
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from clients.gemini import Client as GeminiClient
from clients.bluesky import Client as BlueskyClient

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Main:
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
        
    def fact_check_post(self, post_url: str) -> Dict[str, Any]:
        """
        Fact-check a Bluesky post and return structured JSON response
        """
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

        return reply_text
        
        # # Post reply
        # success = self.bluesky_client.post_reply(original_post_url, reply_text)
        
        # if success:
        #     logger.info(f"Successfully posted fact-check reply to {original_post_url}")
        # else:
        #     logger.error(f"Failed to post reply to {original_post_url}")
            
        return success