import time
import logging
import os
from datetime import datetime
from bots.factChecker  import bot 

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class Oracle(bot):
    def __init__(self):
        super().__init__()
        self.processed_mentions = set()
        self.bot_handle = self.bluesky_username
        
    def get_recent_mentions(self):
        """Get recent mentions from notifications"""
        try:
            logger.info("Checking for new mentions...")
            
            # Get notifications from Bluesky
            notifications = self.bluesky_client.get_notifications()
            
            # Filter for mentions only
            mentions = []
            for notif in notifications:
                if (hasattr(notif, 'reason') and notif.reason == 'mention' and
                    hasattr(notif, 'uri') and notif.uri):
                    mentions.append(notif.uri)
            
            return mentions
            
        except Exception as e:
            logger.error(f"Error getting mentions: {e}")
            return []
    
    def handle_mention(self, mention_uri):
        """Process a single mention"""
        try:
            logger.info(f"Processing mention: {mention_uri}")
            
            # Get the actual mention text to check if it's a sources request
            mention_text = self.bluesky_client.get_post_text(mention_uri)
            if not mention_text:
                logger.warning(f"Could not retrieve mention text for {mention_uri}")
                return
            
            mention_text = mention_text.lower().strip()
            
            # Check if this is a sources request
            if "sources" in mention_text and len(mention_text.replace("@blueskyoracle.bsky.social", "").strip()) <= 10:
                # Get thread data for sources request
                thread_data = self.bluesky_client.get_thread_chain(mention_uri)
                self.handle_sources_request(mention_uri, thread_data)
            else:
                # Regular fact-check request
                result = self.post_fact_check_reply(mention_uri)
                
                if result:
                    logger.info(f"Successfully replied to {mention_uri}")
                else:
                    logger.warning(f"Failed to reply to {mention_uri}")
                
            # Track processed mentions
            self.processed_mentions.add(mention_uri)
            
        except Exception as e:
            logger.error(f"Error handling mention {mention_uri}: {e}")
    
    def handle_sources_request(self, mention_uri, thread_data):
        """Handle a request for sources from a previous fact-check"""
        try:
            logger.info(f"Processing sources request: {mention_uri}")
            
            # Find the bot's fact-check post in the thread context
            fact_check_id = self.find_fact_check_id_in_thread(mention_uri)
            
            if fact_check_id:
                # Retrieve sources from BigQuery
                sources = self.get_sources_by_id(fact_check_id)
                sources_response = self.format_sources_response(sources)
            else:
                sources_response = "Could not find the original fact-check to retrieve sources. Make sure you're replying to one of my fact-check responses."
            
            success = self.bluesky_client.post_reply(mention_uri, sources_response)
            
            if success:
                logger.info(f"Successfully posted sources response to {mention_uri}")
            else:
                logger.warning(f"Failed to post sources response to {mention_uri}")
                
        except Exception as e:
            logger.error(f"Error handling sources request {mention_uri}: {e}")
    
    def find_fact_check_id_in_thread(self, mention_uri):
        """
        Find the fact-check ID from bot posts in the thread
        """
        try:
            # Get the thread data to find what this mention is replying to
            thread_data = self.bluesky_client.get_thread_chain(mention_uri)
            if not thread_data:
                return None
            
            # Get the thread context to find bot posts
            thread_context = thread_data.get("thread_context", "")
            
            # Also check if we can get the parent post directly
            # This is a simplified approach - look for bot posts in recent mapping
            for post_uri, fact_check_id in self.post_to_factcheck_map.items():
                # Check if this fact-check post is in the current thread
                # This is a basic implementation - could be improved
                if post_uri in thread_context or self.is_post_in_thread(post_uri, mention_uri):
                    logger.info(f"Found fact-check ID {fact_check_id} for post {post_uri}")
                    return fact_check_id
            
            logger.warning("Could not find fact-check ID in thread")
            return None
            
        except Exception as e:
            logger.error(f"Error finding fact-check ID: {e}")
            return None
    
    def is_post_in_thread(self, post_uri, mention_uri):
        """
        Check if a post is in the same thread as a mention
        Simplified implementation
        """
        try:
            # Basic check - extract thread info from URIs
            # This is a simplified approach that could be improved
            mention_parts = mention_uri.split('/')
            post_parts = post_uri.split('/')
            
            # Check if they're from the same author (basic thread detection)
            if len(mention_parts) > 2 and len(post_parts) > 2:
                return mention_parts[2] == post_parts[2]  # Same DID
            
            return False
        except Exception:
            return False
    
    def monitor_loop(self, check_interval=30):
        """Main monitoring loop"""
        logger.info(f"Starting BskyOracle monitor - checking every {check_interval}s")
        
        while True:
            try:
                # Get new mentions
                mentions = self.get_recent_mentions()
                
                # Process unprocessed mentions
                new_mentions = [m for m in mentions if m not in self.processed_mentions]
                
                if new_mentions:
                    logger.info(f"Found {len(new_mentions)} new mentions")
                    for mention in new_mentions:
                        self.handle_mention(mention)
                        time.sleep(2)  # Small delay between responses
                else:
                    logger.info("No new mentions found")
                
                # Wait before next check
                time.sleep(check_interval)
                
            except KeyboardInterrupt:
                logger.info("Monitor stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                time.sleep(check_interval)  # Continue on error

def main():
    """Entry point for the monitor"""
    try:
        # Initialize Oracle daemon
        oracle = Oracle()
        logger.info("BskyOracle daemon initialized successfully")
        
        # Start monitoring
        oracle.monitor_loop(check_interval=30)
        
    except Exception as e:
        logger.error(f"Failed to start monitor: {e}")
        raise

if __name__ == "__main__":
    main()