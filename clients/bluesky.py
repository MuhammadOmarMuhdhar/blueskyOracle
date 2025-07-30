import re
import requests
import logging
from atproto import Client as AtprotoClient, models
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class Client:
    """Bluesky client for media processing (images, audio, video)"""
    
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
    
    def get_parent_post_with_media(self, url_or_uri: str) -> Optional[Dict[str, Any]]:
        """Get the parent post (one reply up) with media attachments"""
        # Convert URL to URI if needed
        if url_or_uri.startswith("https://"):
            mention_uri = self.url_to_uri(url_or_uri)
            if not mention_uri:
                return None
        else:
            mention_uri = url_or_uri
            
        try:
            from atproto import models
            
            # Get the thread to find parent
            params = models.AppBskyFeedGetPostThread.Params(
                uri=mention_uri,
                depth=0,  # No replies needed
                parentHeight=1  # Just one parent up
            )
            thread = self.client.app.bsky.feed.get_post_thread(params=params)
            
            # Get parent post if it exists
            if hasattr(thread.thread, 'parent') and thread.thread.parent:
                parent = thread.thread.parent
                if hasattr(parent, 'post'):
                    post = parent.post
                    
                    # Skip bot's own posts
                    bot_handle = self.client.me.handle if hasattr(self.client, 'me') else "bskyscribe.bsky.social"
                    if post.author.handle == bot_handle:
                        logger.debug("Skipping bot's own post")
                        return None
                    
                    # Extract media attachments
                    media_items = []
                    if hasattr(post.record, 'embed') and post.record.embed:
                        media_items = self._extract_media_from_embed(post.record.embed, post.author.did)
                    
                    # Return error if no media found
                    if not media_items:
                        return {
                            "error": "No images, videos, or audio found in this post"
                        }
                    
                    return {
                        "uri": post.uri,
                        "author": f"@{post.author.handle}",
                        "text": post.record.text,
                        "created_at": getattr(post.record, 'createdAt', ''),
                        "media": media_items
                    }
            
            # If no parent, return the mention post itself
            params = models.AppBskyFeedGetPosts.Params(uris=[mention_uri])
            response = self.client.app.bsky.feed.get_posts(params=params)
            
            if response.posts:
                post = response.posts[0]
                
                # Extract media attachments
                media_items = []
                if hasattr(post.record, 'embed') and post.record.embed:
                    media_items = self._extract_media_from_embed(post.record.embed, post.author.did)
                
                # Return error if no media found
                if not media_items:
                    return {
                        "error": "No images, videos, or audio found in this post"
                    }
                
                return {
                    "uri": mention_uri,
                    "author": f"@{post.author.handle}",
                    "text": post.record.text,
                    "created_at": getattr(post.record, 'createdAt', ''),
                    "media": media_items
                }
            
            return None
            
        except Exception as e:
            logger.debug(f"Parent post retrieval failed: {e}")
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
    
    def _extract_media_from_embed(self, embed, author_did: str) -> List[Dict[str, Any]]:
        """Extract media URLs and types from post embed"""
        media_items = []
        
        try:
            # Handle record with media (app.bsky.embed.recordWithMedia)
            if hasattr(embed, 'media') and embed.media:
                # Recursively extract media from the media part
                media_items.extend(self._extract_media_from_embed(embed.media, author_did))
            
            # Handle images (app.bsky.embed.images)
            if hasattr(embed, 'images') and embed.images:
                for image in embed.images:
                    if hasattr(image, 'image') and hasattr(image.image, 'ref'):
                        # Convert blob ref to URL
                        blob_url = f"https://bsky.social/xrpc/com.atproto.sync.getBlob?did={author_did}&cid={image.image.ref.link}"
                        media_items.append({
                            'type': 'image',
                            'url': blob_url,
                            'alt_text': getattr(image, 'alt', ''),
                            'mime_type': getattr(image.image, 'mime_type', ''),
                            'size': getattr(image.image, 'size', 0),
                            'ref': image.image.ref.link
                        })
            
            # Handle videos (app.bsky.embed.video)
            if hasattr(embed, 'video') and embed.video:
                video = embed.video
                # Videos are stored as blobs like images
                if hasattr(video, 'ref'):
                    video_url = f"https://bsky.social/xrpc/com.atproto.sync.getBlob?did={author_did}&cid={video.ref.link}"
                    media_items.append({
                        'type': 'video',
                        'url': video_url,
                        'mime_type': getattr(video, 'mime_type', ''),
                        'size': getattr(video, 'size', 0),
                        'ref': video.ref.link
                    })
                # Handle playlist format if it exists
                elif hasattr(video, 'playlist'):
                    media_items.append({
                        'type': 'video',
                        'url': video.playlist,
                        'thumbnail': getattr(video, 'thumbnail', '')
                    })
            
            # Handle external links that might contain media
            if hasattr(embed, 'external') and embed.external:
                external = embed.external
                if hasattr(external, 'uri'):
                    # Check if external link is a media URL
                    url = external.uri
                    if any(url.lower().endswith(ext) for ext in ['.mp4', '.mov', '.avi', '.mp3', '.wav', '.m4a']):
                        media_type = 'video' if any(url.lower().endswith(ext) for ext in ['.mp4', '.mov', '.avi']) else 'audio'
                        media_items.append({
                            'type': media_type,
                            'url': url,
                            'title': getattr(external, 'title', ''),
                            'description': getattr(external, 'description', '')
                        })
                        
        except Exception as e:
            logger.debug(f"Media extraction failed: {e}")
        
        return media_items
    
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

