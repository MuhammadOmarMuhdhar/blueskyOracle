import time
import logging
import os
from datetime import datetime
import pandas as pd
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
        self.processing_timestamps = {}  # Track when mentions were processed
        self.bot_handle = self.bluesky_username
        self.last_processed_timestamp = None
        # Initialize timestamp-based duplicate prevention
        self.init_timestamp_tracking()
        
    def get_recent_mentions(self):
        """Get recent mentions from notifications, filtered by timestamp"""
        try:
            logger.info("Checking for new mentions...")
            
            # Get notifications from Bluesky
            notifications = self.bluesky_client.get_notifications()
            
            # Filter for mentions only and by timestamp
            mentions = []
            new_latest_timestamp = self.last_processed_timestamp
            
            for notif in notifications:
                if (hasattr(notif, 'reason') and notif.reason == 'mention' and
                    hasattr(notif, 'uri') and notif.uri):
                    
                    # Check if timestamp is available for filtering
                    if hasattr(notif, 'indexedAt') and notif.indexedAt:
                        # Parse notification timestamp
                        notif_timestamp = pd.to_datetime(notif.indexedAt, utc=True)
                        
                        # Only process mentions newer than last processed timestamp
                        if notif_timestamp > self.last_processed_timestamp:
                            mentions.append(notif.uri)
                            
                            # Track the newest timestamp we've seen
                            if notif_timestamp > new_latest_timestamp:
                                new_latest_timestamp = notif_timestamp
                    else:
                        # No timestamp available - process it (fail-safe)
                        mentions.append(notif.uri)
                        logger.info(f"Processing mention without timestamp: {notif.uri}")
            
            # Update our tracking timestamp if we found newer mentions
            if new_latest_timestamp > self.last_processed_timestamp:
                logger.info(f"Found {len(mentions)} new mentions since {self.last_processed_timestamp}")
                self.last_processed_timestamp = new_latest_timestamp
                self.update_timestamp_in_bigquery()
            
            return mentions
            
        except Exception as e:
            logger.error(f"Error getting mentions: {e}")
            return []
    
    def handle_mention(self, mention_uri):
        """Process a single mention"""
        try:
            logger.info(f"Processing mention: {mention_uri}")
            
            # With timestamp-based filtering, mentions should already be new
            # But keep a simple in-memory check for the current session
            if mention_uri in self.processed_mentions:
                logger.info(f"Skipping already processed mention in current session: {mention_uri}")
                return
            
            # Get the actual mention text to check if it's a sources request
            mention_text = self.bluesky_client.get_post_text(mention_uri)
            if not mention_text:
                logger.warning(f"Could not retrieve mention text for {mention_uri}")
                return
            
            mention_text = mention_text.lower().strip()
            
            # Check if this is a sources request
            clean_text = mention_text.replace("@haqiqa.bsky.social", "").strip()
            if "sources" in clean_text and len(clean_text) <= 15:
                logger.info(f"Detected sources request: {mention_uri}")
                self.handle_sources_request(mention_uri)
            else:
                # Regular fact-check request - check if we already replied to this mention
                if self.robust_duplicate_check(mention_uri):
                    logger.info(f"Duplicate detected for mention {mention_uri}, skipping")
                    return
                
                # Proceed with fact-check
                result = self.post_fact_check_reply(mention_uri)
                
                if result:
                    logger.info(f"Successfully replied to {mention_uri}")
                else:
                    logger.warning(f"Failed to reply to {mention_uri}")
            
        except Exception as e:
            logger.error(f"Error handling mention {mention_uri}: {e}")
    
    def robust_duplicate_check(self, mention_uri):
        """Robust duplicate prevention with conservative bias and multiple fallbacks"""
        import requests
        import time
        from datetime import datetime, timedelta
        
        try:
            # Layer 1: Fast in-memory check
            if mention_uri in self.processed_mentions:
                logger.info(f"Duplicate found in memory: {mention_uri}")
                return True
            
            # Layer 2: API check with retry and conservative error handling
            for attempt in range(2):  # 2 attempts max
                try:
                    if self.bluesky_client.has_bot_already_replied(mention_uri, self.bluesky_username):
                        logger.info(f"Duplicate found via API: {mention_uri}")
                        return True
                    # API succeeded and returned False - proceed to next layer
                    logger.debug(f"API check passed (attempt {attempt + 1}): {mention_uri}")
                    break
                    
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    logger.warning(f"API error attempt {attempt + 1} for {mention_uri}: {e}")
                    if attempt == 1:  # Last attempt
                        logger.warning(f"API check failed - CONSERVATIVELY assuming duplicate: {mention_uri}")
                        return True  # Conservative: assume already replied
                    time.sleep(2)  # Brief wait before retry
                    
                except Exception as e:
                    logger.error(f"Unexpected error in duplicate check for {mention_uri}: {e}")
                    logger.warning(f"CONSERVATIVELY assuming duplicate due to error: {mention_uri}")
                    return True  # Conservative: assume already replied
            
            # Layer 3: Recent processing cooldown (simple time-based protection)
            if self.is_recently_processed(mention_uri, cooldown_minutes=5):
                logger.info(f"Mention in cooldown period: {mention_uri}")
                return True
            
            # All checks passed - mark as processed and proceed
            self.processed_mentions.add(mention_uri)
            self.processing_timestamps[mention_uri] = datetime.now()
            logger.info(f"No duplicate found - proceeding with: {mention_uri}")
            return False
            
        except Exception as e:
            logger.error(f"Critical error in robust_duplicate_check for {mention_uri}: {e}")
            # Ultimate fallback - assume duplicate to prevent processing
            return True
    
    def is_recently_processed(self, mention_uri, cooldown_minutes=5):
        """Simple time-based cooldown check"""
        try:
            if mention_uri in self.processing_timestamps:
                time_diff = (datetime.now() - self.processing_timestamps[mention_uri]).total_seconds() / 60
                if time_diff < cooldown_minutes:
                    logger.debug(f"Mention {mention_uri} processed {time_diff:.1f} minutes ago (within {cooldown_minutes}min cooldown)")
                    return True
                else:
                    # Clean up old timestamp
                    del self.processing_timestamps[mention_uri]
            return False
        except Exception as e:
            logger.error(f"Error in cooldown check for {mention_uri}: {e}")
            return False
    
    def handle_sources_request(self, mention_uri):
        """Handle a request for sources from a previous fact-check"""
        try:
            logger.info(f"Processing sources request: {mention_uri}")
            
            # Check for duplicate sources request (prevent infinite loops)
            if self.robust_duplicate_check(mention_uri):
                logger.info(f"Duplicate sources request detected, skipping: {mention_uri}")
                return
            
            # Find the bot's fact-check post in the thread context
            fact_check_id = self.find_fact_check_id_in_thread(mention_uri)
            
            if fact_check_id:
                # Retrieve sources from BigQuery
                sources = self.get_sources_by_id(fact_check_id)
                sources_response = self.format_sources_response(sources)
            else:
                sources_response = "Could not find the original fact-check to retrieve sources. To get sources, please reply 'sources' directly to one of my fact-check responses in the same conversation thread."
            
            success = self.bluesky_client.post_reply(mention_uri, sources_response)
            
            if success:
                logger.info(f"Successfully posted sources response to {mention_uri}")
            else:
                logger.warning(f"Failed to post sources response to {mention_uri}")
                
        except Exception as e:
            logger.error(f"Error handling sources request {mention_uri}: {e}")
    
    def find_fact_check_id_in_thread(self, mention_uri):
        """
        Find the fact-check ID from bot posts in the thread using conservative methods
        Only matches exact thread relationships to prevent cross-contamination
        """
        try:
            # Method 1: Check if this mention is replying directly to a bot post
            thread_data = self.bluesky_client.get_thread_chain(mention_uri)
            if thread_data:
                # Check if parent is a bot post
                parent_info = thread_data.get("target", {})
                if parent_info.get("author") == f"@{self.bluesky_username}":
                    logger.info("Sources request is replying directly to bot post - searching for fact-check ID")
                    parent_uri = thread_data.get("replying_to", {}).get("uri")
                    if parent_uri and parent_uri in self.post_to_factcheck_map:
                        fact_check_id = self.post_to_factcheck_map[parent_uri]
                        logger.info(f"Found fact-check ID from direct reply mapping: {fact_check_id}")
                        return fact_check_id
                    else:
                        logger.warning(f"Bot post found but no fact-check ID in mapping for URI: {parent_uri}")
                        
                        # Try to extract fact-check ID from bot post text if available
                        if parent_uri:
                            bot_post_text = self.bluesky_client.get_post_text(parent_uri)
                            if bot_post_text:
                                logger.info(f"Bot post text: {bot_post_text[:100]}...")
                                # Could implement ID extraction from post text if needed
            
            # Method 2: DISABLED - was causing cross-contamination
            # The old method was too broad and matched posts from same author regardless of thread
            logger.info("Skipping broad post mapping check to prevent cross-contamination")
            
            # Method 3: Conservative fallback - ONLY if we can't find direct relationship
            logger.warning("No direct thread relationship found - sources request may be invalid")
            logger.info("Consider asking user to reply directly to the bot's fact-check post")
            return None  # Don't use BigQuery fallback to avoid wrong associations
            
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
        Check if a post is in the same conversation thread as a mention
        Uses proper thread traversal to avoid cross-contamination
        """
        try:
            # Get the thread data for the mention to see the conversation
            thread_data = self.bluesky_client.get_thread_chain(mention_uri)
            if not thread_data:
                return False
            
            # Check if the post_uri appears anywhere in the conversation
            conversation = thread_data.get("context", {}).get("conversation", [])
            
            # Also check the replying_to URI
            replying_to_uri = thread_data.get("replying_to", {}).get("uri")
            
            # The post_uri should match either:
            # 1. The direct parent (replying_to)
            # 2. One of the posts in the conversation thread
            if replying_to_uri == post_uri:
                logger.info(f"Found exact URI match in thread: {post_uri}")
                return True
            
            # Check conversation context
            for conv_post in conversation:
                # This is a more conservative check - we need actual URI matching
                # For now, return False to be safe and avoid cross-contamination
                pass
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking if post is in thread: {e}")
            return False
    
    def init_timestamp_tracking(self):
        """Initialize timestamp-based duplicate prevention"""
        try:
            if not self.bq_client:
                logger.warning("BigQuery not available - using memory-only tracking")
                self.last_processed_timestamp = pd.Timestamp('1970-01-01', tz='UTC')
                return
            
            dataset_id = os.getenv('BIGQUERY_DATASET_ID', 'dataset')
            timestamp_table_id = 'oracle_timestamps'
            
            # Create timestamp table if it doesn't exist
            self.bq_client.create_timestamp_table(dataset_id, timestamp_table_id)
            
            # Load last processed timestamp
            self.last_processed_timestamp = self.bq_client.get_last_processed_timestamp(
                dataset_id, timestamp_table_id
            )
            
            logger.info(f"Initialized timestamp tracking. Last processed: {self.last_processed_timestamp}")
            
        except Exception as e:
            logger.error(f"Error initializing timestamp tracking: {e}")
            self.last_processed_timestamp = pd.Timestamp('1970-01-01', tz='UTC')
    
    def update_timestamp_in_bigquery(self):
        """Update the last processed timestamp in BigQuery"""
        try:
            if not self.bq_client:
                return
            
            dataset_id = os.getenv('BIGQUERY_DATASET_ID', 'dataset')
            timestamp_table_id = 'oracle_timestamps'
            
            success = self.bq_client.update_last_processed_timestamp(
                dataset_id, timestamp_table_id, self.last_processed_timestamp
            )
            
            if success:
                logger.info(f"Updated timestamp in BigQuery: {self.last_processed_timestamp}")
            else:
                logger.warning(f"Failed to update timestamp in BigQuery")
                
        except Exception as e:
            logger.error(f"Error updating timestamp in BigQuery: {e}")
    
    
    def monitor_loop(self, check_interval=30):
        """Main monitoring loop"""
        logger.info(f"Starting BskyOracle monitor - checking every {check_interval}s")
        
        while True:
            try:
                # Get new mentions
                mentions = self.get_recent_mentions()
                
                # Process new mentions (already filtered by timestamp)
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