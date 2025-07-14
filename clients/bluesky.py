import re
import requests
from atproto import Client as AtprotoClient, models
from typing import Optional, Dict, Any


class Client:
    """Simple Bluesky client with just the essentials"""
    
    def __init__(self, username: str, password: str):
        self.client = AtprotoClient()
        self.authenticated = False
        try:
            self.client.login(username, password)
            self.authenticated = True
        except Exception as e:
            print(f"Login failed: {e}")
            raise
    
    def url_to_uri(self, url: str) -> Optional[str]:
        """Convert Bluesky URL to AT URI"""
        match = re.match(r'https://bsky\.app/profile/([^/]+)/post/([^/?#]+)', url)
        if not match:
            print("URL regex match failed")
            return None
        
        handle, rkey = match.groups()
        
        try:
            response = requests.get(
                f"https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
                params={"handle": handle}
            )
            if response.status_code != 200:
                print(f"Handle resolution failed: {response.text}")
                return None
            did = response.json()["did"]
            uri = f"at://{did}/app.bsky.feed.post/{rkey}"
            return uri
        except Exception as e:
            print(f"Exception in url_to_uri: {e}")
            return None
    
    def get_post_text(self, url_or_uri: str) -> Optional[str]:
        """Get the text content of a single post"""
        if url_or_uri.startswith("https://"):
            uri = self.url_to_uri(url_or_uri)
            if not uri:
                return None
        else:
            uri = url_or_uri
        
        try:
            from atproto import models
            params = models.AppBskyFeedGetPosts.Params(uris=[uri])
            response = self.client.app.bsky.feed.get_posts(params=params)
            if response.posts:
                return response.posts[0].record.text
            return None
        except Exception as e:
            return None
    
    def get_thread_chain(self, url_or_uri: str) -> Optional[Dict[str, Any]]:
        """Get thread context and target post info as structured dictionary"""
        if url_or_uri.startswith("https://"):
            uri = self.url_to_uri(url_or_uri)
            if not uri:
                return None
        else:
            uri = url_or_uri
        
        try:
            from atproto import models
            params = models.AppBskyFeedGetPostThread.Params(uri=uri, parent_height=10)
            thread = self.client.app.bsky.feed.get_post_thread(params=params)
            
            posts = []
            target_post = None
            target_author = None
            
            # Collect all posts in thread order
            def collect_posts(view, is_target=False):
                nonlocal target_post, target_author
                if hasattr(view, 'post') and hasattr(view.post, 'record'):
                    author = view.post.author.handle
                    text = view.post.record.text
                    
                    if is_target:
                        target_post = text
                        target_author = author
                    else:
                        posts.append(f"@{author}: {text}")
            
            # Get parent posts first (context)
            if hasattr(thread.thread, 'parent'):
                collect_posts(thread.thread.parent, is_target=False)
            
            # Get the target post (the one we're replying to)
            collect_posts(thread.thread, is_target=True)
            
            if not target_post:
                return None
                
            return {
                "thread_context": "\n\n".join(posts) if posts else "",
                "replying_to": {
                    "text": target_post,
                    "author": target_author
                }
            }
            
        except Exception as e:
            print(f"Exception in get_thread_chain: {e}")
            return None
    
    def post_reply(self, parent_url_or_uri: str, text: str) -> bool:
        """Post a reply to a post"""
        if not self.authenticated:
            return False
        
        if parent_url_or_uri.startswith("https://"):
            parent_uri = self.url_to_uri(parent_url_or_uri)
            if not parent_uri:
                return False
        else:
            parent_uri = parent_url_or_uri
        
        try:
            # Get parent post for reply refs
            parent_response = self.client.app.bsky.feed.get_posts(uris=[parent_uri])
            parent_post = parent_response.posts[0]
            parent_ref = models.create_strong_ref(parent_post)
            
            # Check if parent is in a thread
            if hasattr(parent_post.record, 'reply') and parent_post.record.reply:
                root_ref = parent_post.record.reply.root
            else:
                root_ref = parent_ref
            
            # Create reply
            reply_to = models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)
            self.client.send_post(text=text, reply_to=reply_to)
            return True
            
        except Exception as e:
            print(f"Reply failed: {e}")
            return False

