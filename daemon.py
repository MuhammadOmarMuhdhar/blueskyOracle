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
        self.bot_handle = self.bluesky_username
        self.last_processed_timestamp = None
        # Initialize timestamp-based duplicate prevention
        self.init_timestamp_tracking()
        
    def get_recent_mentions(self):
        """Get recent mentions from notifications, filtered by timestamp"""
        try:
            logger.debug("Checking for new mentions...")
            
            # Get notifications from Bluesky
            notifications = self.bluesky_client.get_notifications()
            
            # Filter for mentions only and by timestamp
            mentions = []
            new_latest_timestamp = self.last_processed_timestamp
            
            for notif in notifications:
                if (hasattr(notif, 'reason') and notif.reason == 'mention' and
                    hasattr(notif, 'uri') and notif.uri):
                    
                    # Check if timestamp is available for filtering
                    if hasattr(notif, 'indexed_at') and notif.indexed_at:
                        # Parse notification timestamp
                        notif_timestamp = pd.to_datetime(notif.indexed_at, utc=True)
                        
                        # Only process mentions newer than last processed timestamp
                        if notif_timestamp > self.last_processed_timestamp:
                            mentions.append(notif.uri)
                            
                            # Track the newest timestamp we've seen
                            if notif_timestamp > new_latest_timestamp:
                                new_latest_timestamp = notif_timestamp
                    else:
                        # No timestamp available - process it (fail-safe)
                        mentions.append(notif.uri)
                        logger.debug(f"Processing mention without timestamp: {notif.uri}")
            
            # Update our tracking timestamp if we found newer mentions
            if new_latest_timestamp > self.last_processed_timestamp:
                logger.info(f"Found {len(mentions)} new mentions")
                self.last_processed_timestamp = new_latest_timestamp
                self.update_timestamp_in_bigquery()
            
            return mentions
            
        except Exception as e:
            logger.error(f"Error getting mentions: {e}")
            return []
    
    def handle_mention(self, mention_uri):
        """Process a single mention"""
        try:
            logger.debug(f"Processing mention: {mention_uri}")
            
            # Get the actual mention text to check if it's a sources request
            mention_text = self.bluesky_client.get_post_text(mention_uri)
            if not mention_text:
                logger.warning(f"Could not retrieve mention text for {mention_uri}")
                return
            
            mention_text = mention_text.lower().strip()
            
            # Check if this is a sources request (keep @ symbols for context)
            clean_text = mention_text.strip()
            if "sources" in clean_text and len(clean_text) <= 15:
                logger.info(f"Processing sources request")
                self.handle_sources_request(mention_uri)
            else:
                # Regular fact-check request - check if we already replied to this mention
                if self.bluesky_client.has_bot_already_replied(mention_uri, self.bluesky_username):
                    logger.debug(f"Already replied to this mention, skipping")
                    return
                
                # Proceed with fact-check
                result = self.post_fact_check_reply(mention_uri)
                
                if result:
                    logger.info(f"Successfully processed mention")
                else:
                    logger.warning(f"Failed to process mention")
            
        except Exception as e:
            logger.error(f"Error handling mention {mention_uri}: {e}")
    
    
    
    def handle_sources_request(self, mention_uri):
        """Handle a request for sources from a previous fact-check"""
        try:
            logger.debug(f"Processing sources request")
            
            # Check for duplicate sources request (prevent infinite loops)
            if self.bluesky_client.has_bot_already_replied(mention_uri, self.bluesky_username):
                logger.debug(f"Duplicate sources request detected, skipping")
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
                logger.info(f"Posted sources response")
            else:
                logger.warning(f"Failed to post sources response")
                
        except Exception as e:
            logger.error(f"Error handling sources request: {e}")
    
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
                # Check both with and without @ symbol since we strip @ in thread parsing
                parent_author = parent_info.get("author", "")
                bot_identifiers = [f"@{self.bluesky_username}", self.bluesky_username, f"@haqiqa.bsky.social", "haqiqa.bsky.social"]
                if parent_author in bot_identifiers:
                    logger.debug("Sources request replying to bot post")
                    parent_uri = thread_data.get("replying_to", {}).get("uri")
                    if parent_uri and parent_uri in self.post_to_factcheck_map:
                        fact_check_id = self.post_to_factcheck_map[parent_uri]
                        logger.debug(f"Found fact-check ID from mapping: {fact_check_id}")
                        return fact_check_id
                    else:
                        logger.debug(f"No mapping found, trying BigQuery fallback")
                        
                        # Fallback: Try to find fact-check ID from BigQuery using parent post content
                        if parent_uri:
                            bot_post_text = self.bluesky_client.get_post_text(parent_uri)
                            if bot_post_text:
                                logger.debug(f"Searching BigQuery for matching response")
                                fact_check_id = self._find_fact_check_id_by_response_text(bot_post_text)
                                if fact_check_id:
                                    logger.debug(f"Found fact-check ID from BigQuery: {fact_check_id}")
                                    return fact_check_id
            
            # Method 2: DISABLED - was causing cross-contamination
            # The old method was too broad and matched posts from same author regardless of thread
            logger.debug("Skipping broad post mapping check to prevent cross-contamination")
            
            # Method 3: Conservative fallback - ONLY if we can't find direct relationship
            logger.debug("No direct thread relationship found")
            return None  # Don't use BigQuery fallback to avoid wrong associations
            
        except Exception as e:
            logger.error(f"Error finding fact-check ID: {e}")
            return None
    
    def _find_fact_check_id_by_response_text(self, response_text: str) -> str:
        """
        Find fact-check ID from BigQuery by matching response text
        """
        try:
            if not self.bq_client:
                logger.debug("BigQuery not available for fact-check ID lookup")
                return None
            
            dataset_id = os.getenv('BIGQUERY_DATASET_ID', 'fact_checks')
            table_id = os.getenv('BIGQUERY_TABLE_ID', 'responses')
            project_id = os.getenv('BIGQUERY_PROJECT_ID')
            
            # Clean the response text for comparison (remove quotes, normalize whitespace)
            clean_response = response_text.replace('"', '').replace("'", "").strip()
            
            # Search for fact-checks with similar response text from the last 24 hours
            query = f"""
            SELECT id, response, timestamp
            FROM `{project_id}.{dataset_id}.{table_id}` 
            WHERE timestamp >= DATETIME_SUB(CURRENT_DATETIME(), INTERVAL 24 HOUR)
            AND LENGTH(response) > 50
            ORDER BY timestamp DESC
            LIMIT 20
            """
            
            result = self.bq_client.query(query)
            
            if len(result) > 0:
                # Look for best match by comparing response text
                for _, row in result.iterrows():
                    stored_response = row.get('response', '').replace('"', '').replace("'", "").strip()
                    
                    # Simple similarity check - if responses share significant content
                    if len(stored_response) > 50 and len(clean_response) > 50:
                        # Check if they share at least 70% of words
                        response_words = set(clean_response.lower().split())
                        stored_words = set(stored_response.lower().split())
                        
                        if len(response_words) > 5 and len(stored_words) > 5:
                            common_words = response_words.intersection(stored_words)
                            similarity = len(common_words) / min(len(response_words), len(stored_words))
                            
                            if similarity > 0.7:
                                fact_check_id = row.get('id')
                                logger.debug(f"Found matching fact-check ID: {fact_check_id} (similarity: {similarity:.2f})")
                                return fact_check_id
            
            logger.debug("No matching fact-check found in BigQuery")
            return None
            
        except Exception as e:
            logger.error(f"Error finding fact-check ID by response text: {e}")
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
                
                # Process mentions (already filtered by timestamp)
                # No need for additional filtering since timestamp-based filtering already handles duplicates
                
                if mentions:
                    for mention in mentions:
                        self.handle_mention(mention)
                        time.sleep(2)  # Small delay between responses
                
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