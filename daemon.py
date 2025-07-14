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
        # Load recently processed mentions from BigQuery on startup
        self.load_recent_processed_mentions()
        
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
            
            # Check if already processed
            if self.is_mention_already_processed(mention_uri):
                logger.info(f"Skipping already processed mention: {mention_uri}")
                return
            
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
        Find the fact-check ID from bot posts in the thread using BigQuery lookup
        """
        try:
            # Get the thread data to find what this mention is replying to
            thread_data = self.bluesky_client.get_thread_chain(mention_uri)
            if not thread_data:
                return None
            
            # Check if this sources request is replying to a bot post
            replying_to_author = thread_data.get("replying_to", {}).get("author", "")
            
            if replying_to_author == self.bluesky_username.replace(".bsky.social", ""):
                # This is replying to the bot - get the most recent fact-check
                logger.info("Sources request is replying to bot post - finding recent fact-check")
                return self.get_most_recent_fact_check_id()
            
            # Fallback: check in-memory mapping (for recent posts)
            for post_uri, fact_check_id in self.post_to_factcheck_map.items():
                if self.is_post_in_thread(post_uri, mention_uri):
                    logger.info(f"Found fact-check ID {fact_check_id} for post {post_uri}")
                    return fact_check_id
            
            # Final fallback: get most recent fact-check from BigQuery
            logger.info("Using fallback: most recent fact-check")
            return self.get_most_recent_fact_check_id()
            
        except Exception as e:
            logger.error(f"Error finding fact-check ID: {e}")
            return None
    
    def get_most_recent_fact_check_id(self):
        """
        Get the most recent fact-check ID from BigQuery
        """
        try:
            if not self.bq_client:
                return None
            
            dataset_id = os.getenv('BIGQUERY_DATASET_ID', 'dataset')
            table_id = os.getenv('BIGQUERY_TABLE_ID', 'fact-checker')
            project_id = os.getenv('BIGQUERY_PROJECT_ID')
            
            query = f"""
            SELECT id 
            FROM `{project_id}.{dataset_id}.{table_id}` 
            WHERE sources IS NOT NULL AND sources != '[]'
            ORDER BY timestamp DESC 
            LIMIT 1
            """
            
            result = self.bq_client.query(query)
            
            if len(result) > 0:
                fact_check_id = result.iloc[0]['id']
                logger.info(f"Found most recent fact-check ID: {fact_check_id}")
                return fact_check_id
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting most recent fact-check ID: {e}")
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
    
    def load_recent_processed_mentions(self):
        """Load recently processed mentions from BigQuery to avoid duplicates"""
        try:
            if not self.bq_client:
                logger.warning("BigQuery not available - using memory-only tracking")
                return
            
            dataset_id = os.getenv('BIGQUERY_DATASET_ID', 'dataset')
            table_id = os.getenv('BIGQUERY_TABLE_ID', 'fact-checker')
            project_id = os.getenv('BIGQUERY_PROJECT_ID')
            
            # Get fact-checks from the last 24 hours to avoid reprocessing
            query = f"""
            SELECT id
            FROM `{project_id}.{dataset_id}.{table_id}` 
            WHERE DATETIME(timestamp) >= DATETIME_SUB(CURRENT_DATETIME(), INTERVAL 24 HOUR)
            ORDER BY timestamp DESC
            LIMIT 100
            """
            
            result = self.bq_client.query(query)
            
            # Add to processed set (using fact-check IDs as proxy for processed mentions)
            for _, row in result.iterrows():
                self.processed_mentions.add(row['id'])
            
            logger.info(f"Loaded {len(result)} recent processed mentions from BigQuery")
            
        except Exception as e:
            logger.error(f"Error loading processed mentions: {e}")
    
    def is_mention_already_processed(self, mention_uri):
        """Check if this mention has already been processed"""
        try:
            # Check in-memory first (fast)
            if mention_uri in self.processed_mentions:
                logger.info(f"Found in memory: {mention_uri}")
                return True
            
            # For the current session, check if this is in recent mentions we've already seen
            # This is a simple but effective approach
            mentions = self.get_recent_mentions()
            if mention_uri in mentions:
                # Check how many times we've seen this mention recently
                recent_mentions = [m for m in mentions if m == mention_uri]
                if len(recent_mentions) > 1:
                    logger.info(f"Seen this mention multiple times recently: {mention_uri}")
                    return True
            
            # Conservative approach: if we've processed many mentions very recently, skip
            if not self.bq_client:
                return False
            
            dataset_id = os.getenv('BIGQUERY_DATASET_ID', 'dataset')
            table_id = os.getenv('BIGQUERY_TABLE_ID', 'fact-checker')
            project_id = os.getenv('BIGQUERY_PROJECT_ID')
            
            # Check for very recent activity (last 10 minutes)
            query = f"""
            SELECT COUNT(*) as count
            FROM `{project_id}.{dataset_id}.{table_id}` 
            WHERE DATETIME(timestamp) >= DATETIME_SUB(CURRENT_DATETIME(), INTERVAL 10 MINUTE)
            AND id IS NOT NULL
            """
            
            result = self.bq_client.query(query)
            very_recent_count = result.iloc[0]['count'] if len(result) > 0 else 0
            
            # If we've processed mentions very recently, be conservative
            if very_recent_count > 3:
                logger.warning(f"Many very recent fact-checks ({very_recent_count}) - being conservative")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking if mention processed: {e}")
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