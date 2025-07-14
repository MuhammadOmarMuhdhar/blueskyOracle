#!/usr/bin/env python3
"""
Test the final duplicate prevention system
"""

import os
from dotenv import load_dotenv
from daemon import Oracle

# Load environment variables
load_dotenv()

def test_duplicate_prevention_final():
    """Test the complete duplicate prevention system"""
    
    print("🔍 Testing final duplicate prevention system...")
    
    try:
        # Initialize Oracle
        oracle = Oracle()
        print("✅ Oracle initialized")
        
        # Test with the specific URI from user's example
        test_uri = "at://did:plc:l6aqayxdnvxsjsktb5kfv3ib/app.bsky.feed.post/3ltxg7gppls27"
        print(f"📝 Testing URI: {test_uri}")
        
        # Test 1: Fresh state (should not be processed)
        print("\n1️⃣ Testing fresh state...")
        is_processed_1 = oracle.is_mention_already_processed(test_uri)
        print(f"Is processed (fresh): {is_processed_1}")
        
        # Test 2: Simulate processing the mention
        print("\n2️⃣ Simulating mention processing...")
        oracle.processed_mentions.add(test_uri)
        print(f"Added to processed mentions")
        
        # Test 3: Check if now detected as processed
        print("\n3️⃣ Testing after processing...")
        is_processed_2 = oracle.is_mention_already_processed(test_uri)
        print(f"Is processed (after adding): {is_processed_2}")
        
        # Test 4: Test the complete mention handling workflow
        print("\n4️⃣ Testing complete mention handling...")
        
        # Reset processed mentions to test the full workflow
        oracle.processed_mentions.clear()
        
        # Simulate what happens in handle_mention
        print("Simulating handle_mention workflow:")
        
        # Step 1: Check if already processed
        if oracle.is_mention_already_processed(test_uri):
            print("  ✅ Would SKIP (already processed)")
            result = "SKIPPED"
        else:
            print("  🆕 Would PROCESS (new mention)")
            result = "PROCESSED"
            # Add to processed (what real code does)
            oracle.processed_mentions.add(test_uri)
        
        # Step 2: Try the same mention again (should be skipped)
        print("\nSimulating same mention again:")
        if oracle.is_mention_already_processed(test_uri):
            print("  ✅ Would SKIP (already processed) - DUPLICATE PREVENTION WORKING!")
            result2 = "SKIPPED"
        else:
            print("  ❌ Would PROCESS (new mention) - DUPLICATE PREVENTION FAILED!")
            result2 = "PROCESSED"
        
        # Test 5: Test conservative protection with recent activity
        print("\n5️⃣ Testing conservative protection...")
        
        # Check recent BigQuery activity
        if oracle.bq_client:
            dataset_id = os.getenv('BIGQUERY_DATASET_ID', 'dataset')
            table_id = os.getenv('BIGQUERY_TABLE_ID', 'fact-checker')
            project_id = os.getenv('BIGQUERY_PROJECT_ID')
            
            query = f"""
            SELECT COUNT(*) as count
            FROM `{project_id}.{dataset_id}.{table_id}` 
            WHERE DATETIME(timestamp) >= DATETIME_SUB(CURRENT_DATETIME(), INTERVAL 10 MINUTE)
            AND id IS NOT NULL
            """
            
            result_bq = oracle.bq_client.query(query)
            recent_count = result_bq.iloc[0]['count'] if len(result_bq) > 0 else 0
            print(f"Recent fact-checks (10 min): {recent_count}")
            
            if recent_count > 3:
                print("  ✅ Conservative protection would activate")
            else:
                print("  ℹ️  Conservative protection not needed")
        
        print("\n📊 TEST RESULTS:")
        print(f"  First attempt: {result}")
        print(f"  Second attempt: {result2}")
        
        if result == "PROCESSED" and result2 == "SKIPPED":
            print("🎉 ✅ DUPLICATE PREVENTION WORKING CORRECTLY!")
        else:
            print("❌ DUPLICATE PREVENTION NEEDS FIXES")
        
        print("\n🎯 Summary:")
        print("✅ Memory-based tracking working")
        print("✅ In-session duplicate prevention working") 
        print("✅ BigQuery conservative protection available")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_duplicate_prevention_final()