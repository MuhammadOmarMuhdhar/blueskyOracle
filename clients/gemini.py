import time
import requests
import io
from google import genai
from google.genai import types

class Client:
    def __init__(self, api_key, model_name="gemini-2.5-flash"):
        self.api_key = api_key
        self.model_name = model_name
        self.client = genai.Client(api_key=self.api_key)

    def generate(self, prompt, delay=6):
        """Generate content without search tools"""
        time.sleep(delay)

        config = types.GenerateContentConfig(
            max_output_tokens=8000
        )

        output = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=config
        )

        # Try different response structures
        if hasattr(output, 'text') and output.text:
            result = output.text
        elif hasattr(output, 'candidates') and output.candidates:
            candidate = output.candidates[0]
            if candidate and hasattr(candidate, 'content') and candidate.content:
                if hasattr(candidate.content, 'parts') and candidate.content.parts:
                    result = candidate.content.parts[0].text
                else:
                    result = f"No parts in content. Candidate: {candidate}"
            else:
                result = f"No content in candidate. Candidate: {candidate}"
        else:
            result = f"No candidates. Output: {output}"
        return result
    
    def process_media(self, media_url: str, prompt: str = "Process this media content.", delay: int = 6):
        """Download and process media from URL using in-memory processing with structured JSON output"""
        time.sleep(delay)
        
        try:
            # Download into memory (no temp files - Render-safe)
            response = requests.get(media_url, timeout=30)
            response.raise_for_status()
            
            # Create BytesIO from response content
            media_data = io.BytesIO(response.content)
            content_type = response.headers.get('content-type', 'application/octet-stream')
            
            # Upload directly from memory to Gemini
            uploaded_file = self.client.files.upload(
                file=media_data,
                config=dict(mime_type=content_type)
            )
            
            # Wait for file processing (especially important for videos)
            max_wait = 60  # seconds
            wait_time = 0
            while uploaded_file.state.name != 'ACTIVE' and wait_time < max_wait:
                time.sleep(5)
                wait_time += 5
                try:
                    uploaded_file = self.client.files.get(name=uploaded_file.name)
                except:
                    break
            
            if uploaded_file.state.name != 'ACTIVE':
                return f"Error: File processing failed or timed out after {max_wait}s"
            
            # Generate content with media and structured JSON schema
            config = types.GenerateContentConfig(
                response_mime_type='application/json',
                response_schema={
                    "type": "object",
                    "properties": {
                        "thinking": {"type": "string"},
                        "request_type": {"type": "string", "enum": ["SUMMARIZE", "DESCRIBE", "READ_TEXT"]},
                        "media_type": {"type": "string", "enum": ["AUDIO", "VIDEO", "IMAGE"]},
                        "response_character_count": {"type": "integer"},
                        "response": {"type": "string"}
                    },
                    "required": ["thinking", "request_type", "media_type", "response_character_count", "response"]
                },
                max_output_tokens=8000
            )
            
            output = self.client.models.generate_content(
                model=self.model_name,
                contents=[uploaded_file, prompt],
                config=config
            )
            
            # Extract result same way as generate method
            if hasattr(output, 'text') and output.text:
                result = output.text
            elif hasattr(output, 'candidates') and output.candidates:
                candidate = output.candidates[0]
                if candidate and hasattr(candidate, 'content') and candidate.content:
                    if hasattr(candidate.content, 'parts') and candidate.content.parts:
                        result = candidate.content.parts[0].text
                    else:
                        result = f"No parts in content. Candidate: {candidate}"
                else:
                    result = f"No content in candidate. Candidate: {candidate}"
            else:
                result = f"No candidates. Output: {output}"
                
            return result
            
        except Exception as e:
            return f"Error processing media: {str(e)}"

