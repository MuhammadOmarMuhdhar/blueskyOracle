import time
from google import genai
from google.genai import types

class Client:
    def __init__(self, api_key, model_name="gemini-2.5-flash"):
        self.api_key = api_key
        self.model_name = model_name
        self.cache = {}
        self.client = genai.Client(api_key=self.api_key)
        self.search_tool = types.Tool(
            google_search=types.GoogleSearch()
        )

    def generate(self, prompt, delay=6):
        if prompt in self.cache:
            return self.cache[prompt]

        time.sleep(delay)  # Rate limit management

        config = types.GenerateContentConfig(
            tools=[self.search_tool],
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
        self.cache[prompt] = result
        return result