import time
import logging
import os
from datetime import datetime
from bots.transcriptionBot import TranscriptionBot 

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class Scribe(TranscriptionBot):
    def __init__(self):
        super().__init__()
        self.bot_handle = self.bluesky_username
        self.last_processed_timestamp = None
        # Initialize timestamp-based duplicate prevention
        self.processed_mentions = set()

    def monitor_mentions(self):
        """Monitor for mentions and process transcription requests"""
        logger.info("Starting mention monitoring for transcription bot")
        
        while True:
            try:
                notifications = self.bluesky_client.get_notifications(limit=20)
                
                for notification in notifications:
                    if hasattr(notification, 'reason') and notification.reason == 'mention':
                        mention_uri = notification.uri
                        
                        # Skip if already processed
                        if mention_uri in self.processed_mentions:
                            continue
                            
                        # Skip if too old (older than 1 hour)
                        if hasattr(notification, 'indexedAt'):
                            try:
                                notification_time = datetime.fromisoformat(notification.indexedAt.replace('Z', '+00:00'))
                                current_time = datetime.now(notification_time.tzinfo)
                                time_diff = current_time - notification_time
                                
                                if time_diff.total_seconds() > 3600:  # 1 hour
                                    continue
                            except:
                                pass  # If timestamp parsing fails, process anyway
                        
                        logger.info(f"Processing mention: {mention_uri}")
                        self.handle_mention(mention_uri)
                        self.processed_mentions.add(mention_uri)
                        
                        # Clean old processed mentions (keep last 1000)
                        if len(self.processed_mentions) > 1000:
                            self.processed_mentions = set(list(self.processed_mentions)[-500:])
                
                # Sleep before next check
                time.sleep(30)
                
            except Exception as e:
                logger.error(f"Error in mention monitoring: {e}")
                time.sleep(60)  # Wait longer on error

    def handle_mention(self, mention_uri):
        """Handle a single mention for transcription"""
        try:
            logger.debug(f"Handling mention: {mention_uri}")
            
            # Check for duplicate processing
            if self.bluesky_client.has_bot_already_replied(mention_uri, self.bluesky_username):
                logger.debug(f"Already replied to this mention, skipping")
                return
            
            # Proceed with transcription
            result = self.post_transcription_reply(mention_uri)
            
            if result:
                logger.info(f"Successfully processed mention")
            else:
                logger.error(f"Failed to process mention")
            
        except Exception as e:
            logger.error(f"Error handling mention {mention_uri}: {e}")

    def get_mention_text(self, mention_uri):
        """Get the text content of a mention"""
        try:
            return self.bluesky_client.get_post_text(mention_uri)
        except Exception as e:
            logger.debug(f"Error getting mention text: {e}")
            return None

if __name__ == "__main__":
    scribe = Scribe()
    scribe.monitor_mentions()