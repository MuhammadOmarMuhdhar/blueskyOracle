import re
import requests
import logging
from atproto import Client as AtprotoClient, models
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class Client:
    """Simple Bluesky client with just the essentials"""
    
    def __init__(self, username: str, password: str):
        self.client = AtprotoClient()
        self.authenticated = False
        try:
            self.client.login(username, password)
            self.authenticated = True
        except Exception as e:
            logger.error(f"Bluesky login failed: {e}")
            raise
    
    def url_to_uri(self, url: str) -> Optional[str]:
        """Convert Bluesky URL to AT URI"""
        match = re.match(r'https://bsky\.app/profile/([^/]+)/post/([^/?#]+)', url)
        if not match:
            logger.debug("URL regex match failed")
            return None
        
        handle, rkey = match.groups()
        
        try:
            response = requests.get(
                f"https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
                params={"handle": handle}
            )
            if response.status_code != 200:
                logger.debug(f"Handle resolution failed: {response.text}")
                return None
            did = response.json()["did"]
            uri = f"at://{did}/app.bsky.feed.post/{rkey}"
            return uri
        except Exception as e:
            logger.debug(f"URL to URI conversion failed: {e}")
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
        
        # Early check: Don't process the bot's own posts
        try:
            from atproto import models
            params = models.AppBskyFeedGetPosts.Params(uris=[uri])
            response = self.client.app.bsky.feed.get_posts(params=params)
            if response.posts:
                post_author = response.posts[0].author.handle
                bot_handle = self.client.me.handle if hasattr(self.client, 'me') else "haqiqa.bsky.social"
                if post_author == bot_handle:
                    logger.debug(f"Skipping bot's own post")
                    return None
        except Exception as e:
            logger.debug(f"Error checking post author: {e}")
            # Continue with normal processing if check fails
        
        try:
            from atproto import models
            params = models.AppBskyFeedGetPostThread.Params(
                uri=uri, 
                depth=6,        # Get replies
                parentHeight=10 # Get parent context
            )
            thread = self.client.app.bsky.feed.get_post_thread(params=params)
            
            conversation = []
            target_post = None
            target_author = None
            mention_request = None
            mention_author = None
            
            # Helper to extract post data
            def extract_post_data(view):
                if hasattr(view, 'post') and hasattr(view.post, 'record'):
                    return {
                        "author": f"@{view.post.author.handle}",
                        "content": view.post.record.text,
                        "timestamp": getattr(view.post.record, 'createdAt', '')
                    }
                return None
            
            # Get current post (the mention request)
            current_post = extract_post_data(thread.thread)
            if current_post:
                mention_request = current_post["content"]
                mention_author = current_post["author"]
                conversation.append({
                    **current_post,
                    "role": "fact_check_request"
                })
            
            # Traverse the thread to find the root post and build conversation context
            def traverse_thread_to_root(current_thread):
                """Traverse thread backwards to find root post and build full context"""
                thread_chain = []
                current = current_thread
                bot_handle = f"@{self.client.me.handle}" if hasattr(self.client, 'me') else "@haqiqa.bsky.social"
                
                # Traverse backwards through the thread
                while current and hasattr(current, 'parent'):
                    parent_post = extract_post_data(current.parent)
                    if parent_post:
                        thread_chain.insert(0, parent_post)  # Insert at beginning to maintain order
                    current = current.parent
                
                # Now analyze the chain to find the original claim and context
                root_post = None
                bot_replies = []
                other_posts = []
                
                for post in thread_chain:
                    if post["author"] == bot_handle:
                        bot_replies.append(post)
                    else:
                        other_posts.append(post)
                
                # The root post is the first non-bot post in the chain
                if other_posts:
                    root_post = other_posts[0]
                
                return root_post, bot_replies, other_posts
            
            # Get thread context
            root_post, bot_replies, other_posts = traverse_thread_to_root(thread.thread)
            
            # Set target post - prefer root post if available
            if root_post:
                target_post = root_post["content"]
                target_author = root_post["author"]
                conversation.insert(0, {
                    **root_post,
                    "role": "original_claim"
                })
                
                # Add other context posts
                for i, post in enumerate(other_posts[1:], 1):  # Skip root post
                    conversation.insert(i, {
                        **post,
                        "role": "discussion"
                    })
                
                # Add bot replies for context
                for bot_reply in bot_replies:
                    conversation.append({
                        **bot_reply,
                        "role": "bot_previous_reply"
                    })
            
            # Fallback: if no root post found, use direct parent
            elif hasattr(thread.thread, 'parent'):
                parent_post = extract_post_data(thread.thread.parent)
                if parent_post:
                    target_post = parent_post["content"]
                    target_author = parent_post["author"]
                    conversation.insert(0, {
                        **parent_post,
                        "role": "original_claim"
                    })
            
            # If no parent, the current post itself might be the target
            if not target_post and current_post:
                target_post = current_post["content"]
                target_author = current_post["author"]
            
            if not target_post:
                return None
            
            # Don't fact-check the bot's own posts
            bot_handle = f"@{self.client.me.handle}" if hasattr(self.client, 'me') else "@haqiqa.bsky.social"
            if target_author == bot_handle:
                return None
            
            # Determine request type and instruction (keep @ symbols for context)
            request_instruction = (mention_request or "").strip()
            request_type = "fact_check"
            if "?" in request_instruction:
                request_type = "question"
            
            # Determine target post type
            target_post_type = "statement"
            if "http" in target_post:
                target_post_type = "article_share"
            elif target_post.startswith("@"):
                target_post_type = "reply"
            
            return {
                "request": {
                    "type": request_type,
                    "requester": mention_author or "@unknown",
                    "instruction": request_instruction
                },
                "target": {
                    "author": target_author or "@unknown",
                    "content": target_post,
                    "post_type": target_post_type
                },
                "context": {
                    "conversation": conversation,
                    "thread_summary": f"Discussion thread with {len(conversation)} posts"
                },
                # Keep legacy format for backward compatibility
                "thread_context": "\n\n".join([f"{p['author']}: {p['content']}" for p in conversation]),
                "replying_to": {
                    "text": target_post,
                    "author": target_author,
                    "uri": uri
                }
            }
            
        except Exception as e:
            logger.debug(f"Thread chain retrieval failed: {e}")
            return None
    
    def get_notifications(self, limit: int = 50) -> list:
        """Get recent notifications (mentions, replies, etc.)"""
        if not self.authenticated:
            return []
        
        try:
            from atproto import models
            params = models.AppBskyNotificationListNotifications.Params(limit=limit)
            response = self.client.app.bsky.notification.list_notifications(params=params)
            return response.notifications
        except Exception as e:
            logger.debug(f"Notifications retrieval failed: {e}")
            return []
    
    def get_post_replies(self, post_url_or_uri: str, limit: int = 100) -> list:
        """Get replies to a specific post"""
        if not self.authenticated:
            return []
        
        if post_url_or_uri.startswith("https://"):
            post_uri = self.url_to_uri(post_url_or_uri)
            if not post_uri:
                return []
        else:
            post_uri = post_url_or_uri
        
        try:
            from atproto import models
            params = models.AppBskyFeedGetPostThread.Params(
                uri=post_uri,
                depth=1,  # Only get direct replies
                parentHeight=0  # Don't get parent context
            )
            response = self.client.app.bsky.feed.get_post_thread(params=params)
            
            replies = []
            if hasattr(response.thread, 'replies') and response.thread.replies:
                for reply in response.thread.replies:
                    if hasattr(reply, 'post') and hasattr(reply.post, 'author'):
                        replies.append({
                            'uri': reply.post.uri,
                            'author_handle': reply.post.author.handle,
                            'author_did': reply.post.author.did,
                            'text': getattr(reply.post.record, 'text', ''),
                            'created_at': getattr(reply.post.record, 'createdAt', '')
                        })
            
            return replies
            
        except Exception as e:
            logger.debug(f"Post replies retrieval failed: {e}")
            return []
    
    def has_bot_already_replied(self, post_url_or_uri: str, bot_handle: str) -> bool:
        """Check if the bot has already replied to this post"""
        try:
            replies = self.get_post_replies(post_url_or_uri)
            logger.debug(f"Found {len(replies)} replies")
            
            # Check if any reply is from the bot
            for reply in replies:
                if reply['author_handle'] == bot_handle:
                    logger.debug(f"Found existing bot reply")
                    return True
            return False
            
        except Exception as e:
            logger.debug(f"Bot reply check failed: {e}")
            # Return False on error to avoid blocking legitimate posts
            return False

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
            from atproto import models
            params = models.AppBskyFeedGetPosts.Params(uris=[parent_uri])
            parent_response = self.client.app.bsky.feed.get_posts(params=params)
            parent_post = parent_response.posts[0]
            parent_ref = models.create_strong_ref(parent_post)
            
            # Check if parent is in a thread
            if hasattr(parent_post.record, 'reply') and parent_post.record.reply:
                root_ref = parent_post.record.reply.root
            else:
                root_ref = parent_ref
            
            # Create reply
            reply_to = models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)
            response = self.client.send_post(text=text, reply_to=reply_to)
            
            # Return the URI of the posted reply
            if hasattr(response, 'uri'):
                return response.uri
            return True  # Fallback for compatibility
            
        except Exception as e:
            logger.error(f"Reply posting failed: {e}")
            return False

