import time
from google import genai

class Client:
    def __init__(self, api_key, model_name="gemini-2.5-flash"):
        self.api_key = api_key
        self.model_name = model_name
        self.cache = {}
        self.client = genai.Client(api_key=self.api_key)
        self.search_tool = {'google_search': {}}
    
    def generate(self, prompt, delay=6):
        if prompt in self.cache:
            return self.cache[prompt]
        
        time.sleep(delay)  # Rate limit management
        
        chat = self.client.chats.create(
            model=self.model_name, 
            config={'tools': [self.search_tool]}
        )
        response = chat.send_message(prompt)
        output = response.candidates[0].content.parts[0].text
        
        self.cache[prompt] = output
        return output