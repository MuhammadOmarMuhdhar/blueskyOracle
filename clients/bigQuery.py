import pandas as pd
from google.oauth2 import service_account
from google.cloud import bigquery
import numpy as np
import time
import logging
import json
from datetime import datetime, date
import re
import gc
import weakref
from contextlib import contextmanager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class Client:
    def __init__(self, credentials_json, project_id):
        """
        Initialize the BigQuery API with memory management
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.debug("Initializing BigQuery API")
        
        self.credentials_json = credentials_json
        self.project_id = project_id
        self.client = self._build_client()
        self.batch_size = 10000
        
        # Track active jobs for cleanup - USE WEAKREFS TO PREVENT REFERENCE CYCLES
        self._active_jobs = weakref.WeakSet()
        
        self.logger.debug(f"BigQuery API initialized with batch size: {self.batch_size}")
    
    def _build_client(self):
        """Build and return the BigQuery client"""
        self.logger.debug("Building BigQuery client")
        
        credentials = service_account.Credentials.from_service_account_info(
            self.credentials_json,
            scopes=['https://www.googleapis.com/auth/bigquery']
        )
        
        client = bigquery.Client(project=self.project_id, credentials=credentials)
        self.logger.debug("BigQuery client built successfully")
        return client
    
    @contextmanager
    def _managed_query_job(self, query, job_config=None):
        """Context manager for query jobs with automatic cleanup"""
        job = None
        try:
            job = self.client.query(query, job_config=job_config)
            self._active_jobs.add(job)  # Track with weak reference
            yield job
        finally:
            # EXPLICIT CLEANUP
            if job:
                try:
                    # Cancel job if still running
                    if hasattr(job, 'cancel') and job.state in ['PENDING', 'RUNNING']:
                        job.cancel()
                except Exception:
                    pass
                
                # Clear job reference
                job = None
            
            # Force garbage collection
            gc.collect()
    
    @contextmanager
    def _managed_load_job(self, table_ref, job_config=None):
        """Context manager for load jobs with automatic cleanup"""
        job = None
        try:
            job = self.client.load_table_from_dataframe(
                dataframe=None,  # Will be set by caller
                destination=table_ref,
                job_config=job_config
            )
            self._active_jobs.add(job)  # Track with weak reference
            yield job
        finally:
            # EXPLICIT CLEANUP
            if job:
                try:
                    # Cancel job if still running
                    if hasattr(job, 'cancel') and job.state in ['PENDING', 'RUNNING']:
                        job.cancel()
                except Exception:
                    pass
                
                # Clear job reference
                job = None
            
            # Force garbage collection
            gc.collect()
    
    def _cleanup_jobs(self):
        """Manually cleanup any remaining job references"""
        try:
            # WeakSet automatically removes dead references
            active_count = len(self._active_jobs)
            if active_count > 0:
                self.logger.warning(f"Found {active_count} active jobs during cleanup")
            
            # Force garbage collection
            gc.collect()
            
        except Exception as e:
            self.logger.warning(f"Error during job cleanup: {e}")
    
    def __del__(self):
        """Cleanup on destruction"""
        try:
            self._cleanup_jobs()
            if hasattr(self, 'client'):
                self.client.close()
        except Exception:
            pass
    
    def _is_client_healthy(self):
        """Check if the BigQuery client connection is still healthy"""
        try:
            test_query = "SELECT 1 as test_connection"
            with self._managed_query_job(test_query) as job:
                job.result(timeout=5)
                return True
        except Exception as e:
            self.logger.warning(f"Client health check failed: {e}")
            return False

    def _refresh_client(self):
        """Rebuild the BigQuery client with fresh credentials"""
        self.logger.debug("Refreshing BigQuery client connection")
        
        # CLEANUP OLD CLIENT
        try:
            if hasattr(self, 'client') and self.client:
                self.client.close()
        except Exception:
            pass
        
        # Cleanup any remaining jobs
        self._cleanup_jobs()
        
        # Build new client
        self.client = self._build_client()
        return self.client

    def get_healthy_client(self):
        """Get a healthy BigQuery client, refreshing if necessary"""
        if not self._is_client_healthy():
            self.logger.info("Client unhealthy, refreshing connection")
            self._refresh_client()
        return self.client
    
    def _sanitize_dataframe(self, df):
        """Sanitize entire dataframe for BigQuery upload"""
        self.logger.info(f"Sanitizing DataFrame with shape: {df.shape}")
        
        try:
            # Create a copy to avoid modifying original
            df_clean = df.copy()
            
            # Apply sanitization to each column
            for col in df_clean.columns:
                self.logger.debug(f"Sanitizing column: {col}")
                df_clean[col] = df_clean[col].apply(self._sanitize_cell_value)
            
            # Clean column names for BigQuery
            df_clean.columns = [re.sub(r'[^a-zA-Z0-9_]', '_', str(col)) for col in df_clean.columns]
            
            self.logger.info(f"DataFrame sanitization completed")
            return df_clean
            
        except Exception as e:
            self.logger.error(f"Error sanitizing DataFrame: {e}")
            raise
    
    def append(self, dataframe, dataset_id, table_id, create_if_not_exists=True, 
                       chunk_size=None, max_retries=3):
        """
        Append data to an existing BigQuery table with memory management
        
        Args:
            dataframe: pandas DataFrame to append
            dataset_id: BigQuery dataset ID
            table_id: BigQuery table ID
            create_if_not_exists: Create table if it doesn't exist
            chunk_size: Size of chunks for large DataFrames (default: self.batch_size)
            max_retries: Maximum number of retry attempts
        """
        self.logger.info(f"Starting append operation - Dataset: {dataset_id}, Table: {table_id}")
        
        if chunk_size is None:
            chunk_size = self.batch_size
        
        try:
            # Ensure we have a healthy client
            client = self.get_healthy_client()
            
            # Sanitize the dataframe
            df_clean = self._sanitize_dataframe(dataframe)
            
            # Get table reference
            table_ref = client.dataset(dataset_id).table(table_id)
            
            # Check if table exists
            try:
                table = client.get_table(table_ref)
                self.logger.info(f"Table exists with {table.num_rows} rows")
            except Exception as e:
                if create_if_not_exists:
                    self.logger.info(f"Table doesn't exist, will be created: {e}")
                else:
                    raise Exception(f"Table doesn't exist and create_if_not_exists=False: {e}")
            
            # Configure job for append
            job_config = bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                autodetect=True,
                source_format=bigquery.SourceFormat.PARQUET  # More efficient than CSV
            )
            
            # Process in chunks for memory efficiency
            total_rows = len(df_clean)
            rows_processed = 0
            
            for start_idx in range(0, total_rows, chunk_size):
                end_idx = min(start_idx + chunk_size, total_rows)
                chunk = df_clean.iloc[start_idx:end_idx].copy()
                
                self.logger.info(f"Processing chunk {start_idx}-{end_idx} of {total_rows}")
                
                # Retry mechanism for each chunk
                for attempt in range(max_retries):
                    try:
                        # Load chunk to BigQuery
                        job = client.load_table_from_dataframe(
                            chunk, table_ref, job_config=job_config
                        )
                        self._active_jobs.add(job)
                        
                        # Wait for job completion
                        job.result()
                        
                        rows_processed += len(chunk)
                        self.logger.info(f"Successfully appended chunk. Total rows processed: {rows_processed}")
                        break
                        
                    except Exception as e:
                        self.logger.warning(f"Attempt {attempt + 1} failed for chunk {start_idx}-{end_idx}: {e}")
                        if attempt == max_retries - 1:
                            raise Exception(f"Failed to append chunk after {max_retries} attempts: {e}")
                        time.sleep(2 ** attempt)  # Exponential backoff
                
                # Clean up chunk
                del chunk
                gc.collect()
            
            self.logger.info(f"Append operation completed. Total rows appended: {rows_processed}")
            
        except Exception as e:
            self.logger.error(f"Error appending to table: {e}")
            raise
        finally:
            # Clean up
            if 'df_clean' in locals():
                del df_clean
            gc.collect()
    
    def replace(self, dataframe, dataset_id, table_id, chunk_size=None, max_retries=3):
        """
        Replace an entire BigQuery table with new data
        
        Args:
            dataframe: pandas DataFrame to replace table with
            dataset_id: BigQuery dataset ID
            table_id: BigQuery table ID
            chunk_size: Size of chunks for large DataFrames (default: self.batch_size)
            max_retries: Maximum number of retry attempts
        """
        self.logger.info(f"Starting replace operation - Dataset: {dataset_id}, Table: {table_id}")
        
        if chunk_size is None:
            chunk_size = self.batch_size
        
        try:
            # Ensure we have a healthy client
            client = self.get_healthy_client()
            
            # Sanitize the dataframe
            df_clean = self._sanitize_dataframe(dataframe)
            
            # Get table reference
            table_ref = client.dataset(dataset_id).table(table_id)
            
            # Configure job for replace (first chunk)
            job_config_replace = bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,  # Replace existing data
                autodetect=True,
                source_format=bigquery.SourceFormat.PARQUET
            )
            
            # Configure job for append (subsequent chunks)
            job_config_append = bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                autodetect=True,
                source_format=bigquery.SourceFormat.PARQUET
            )
            
            # Process in chunks for memory efficiency
            total_rows = len(df_clean)
            rows_processed = 0
            is_first_chunk = True
            
            for start_idx in range(0, total_rows, chunk_size):
                end_idx = min(start_idx + chunk_size, total_rows)
                chunk = df_clean.iloc[start_idx:end_idx].copy()
                
                self.logger.info(f"Processing chunk {start_idx}-{end_idx} of {total_rows}")
                
                # Use appropriate job config
                current_job_config = job_config_replace if is_first_chunk else job_config_append
                
                # Retry mechanism for each chunk
                for attempt in range(max_retries):
                    try:
                        # Load chunk to BigQuery
                        job = client.load_table_from_dataframe(
                            chunk, table_ref, job_config=current_job_config
                        )
                        self._active_jobs.add(job)
                        
                        # Wait for job completion
                        job.result()
                        
                        rows_processed += len(chunk)
                        chunk_action = "replaced" if is_first_chunk else "appended"
                        self.logger.info(f"Successfully {chunk_action} chunk. Total rows processed: {rows_processed}")
                        break
                        
                    except Exception as e:
                        self.logger.warning(f"Attempt {attempt + 1} failed for chunk {start_idx}-{end_idx}: {e}")
                        if attempt == max_retries - 1:
                            raise Exception(f"Failed to process chunk after {max_retries} attempts: {e}")
                        time.sleep(2 ** attempt)  # Exponential backoff
                
                # After first chunk, switch to append mode
                is_first_chunk = False
                
                # Clean up chunk
                del chunk
                gc.collect()
            
            self.logger.info(f"Replace operation completed. Total rows in new table: {rows_processed}")
            
        except Exception as e:
            self.logger.error(f"Error replacing table: {e}")
            raise
        finally:
            # Clean up
            if 'df_clean' in locals():
                del df_clean
            gc.collect()
    
    def execute_query(self, query, use_storage_api=True):
        """Execute query with proper memory management"""
        try:
            # Ensure we have a healthy client
            client = self.get_healthy_client()
            
            job_config = bigquery.QueryJobConfig(use_query_cache=True)
            
            # Use context manager for automatic cleanup
            with self._managed_query_job(query, job_config) as query_job:
                
                if use_storage_api:
                    try:
                        df = query_job.to_dataframe(create_bqstorage_client=True)
                    except Exception as e:
                        self.logger.warning(f"Storage API failed, using standard API: {e}")
                        df = query_job.to_dataframe()
                else:
                    df = query_job.to_dataframe()
                
                # IMPORTANT: Make a copy to break references to the job
                df_copy = df.copy()
                
                # Clear original dataframe reference
                del df
                
                return df_copy
                
        except Exception as e:
            self.logger.error(f"Error executing query: {str(e)}")
            raise
        finally:
            # Force cleanup
            gc.collect()
    
    def read(self, dataset_id, table_id, query=None, limit=None, use_db_dtypes=True):
        """
        Reads data with proper memory management
        """
        self.logger.info(f"Starting read operation - Dataset: {dataset_id}, Table: {table_id}")
        
        try:
            if query:
                sql_query = query
                self.logger.info("Using custom query")
            else:
                sql_query = f"""
                SELECT *
                FROM `{self.project_id}.{dataset_id}.{table_id}`
                """
                
                if limit:
                    sql_query += f" LIMIT {limit}"
            
            self.logger.info(f"Executing query: {sql_query}")
            
            # Use context manager for automatic cleanup
            with self._managed_query_job(sql_query) as query_job:
                
                try:
                    if use_db_dtypes:
                        df = query_job.to_dataframe()
                    else:
                        raise ValueError("Skipping db-dtypes")
                except (ValueError, ImportError) as e:
                    if "db-dtypes" in str(e) or not use_db_dtypes:
                        self.logger.warning("db-dtypes package not available, using alternative method")
                        
                        # MEMORY-EFFICIENT ROW PROCESSING
                        results = query_job.result()
                        
                        # Process in chunks to avoid memory buildup
                        chunk_size = 10000
                        all_chunks = []
                        
                        current_chunk = []
                        for i, row in enumerate(results):
                            row_dict = {}
                            for key, value in row.items():
                                if hasattr(value, 'isoformat'):
                                    row_dict[key] = value.isoformat()
                                else:
                                    row_dict[key] = value
                            current_chunk.append(row_dict)
                            
                            # Process in chunks
                            if len(current_chunk) >= chunk_size:
                                all_chunks.append(pd.DataFrame(current_chunk))
                                current_chunk = []
                                
                                # Periodic garbage collection
                                if len(all_chunks) % 10 == 0:
                                    gc.collect()
                        
                        # Add remaining rows
                        if current_chunk:
                            all_chunks.append(pd.DataFrame(current_chunk))
                        
                        # Combine chunks efficiently
                        if all_chunks:
                            df = pd.concat(all_chunks, ignore_index=True)
                            # Clear chunk references
                            del all_chunks
                        else:
                            df = pd.DataFrame()
                    else:
                        raise e
                
                # Make a copy to break job references
                df_copy = df.copy()
                del df
                
                self.logger.info(f"Read operation completed - DataFrame shape: {df_copy.shape}")
                return df_copy
                
        except Exception as e:
            self.logger.error(f"Error reading data: {str(e)}")
            raise
        finally:
            gc.collect()
    
    def _sanitize_cell_value(self, value):
        """Comprehensive sanitization of individual cell values for BigQuery"""
        # Handle None/NaN/null values
        if value is None or pd.isna(value):
            return None
        
        # Handle numpy NaN specifically
        if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
            return None
        
        # Handle numpy integers
        if isinstance(value, np.integer):
            return int(value)
        
        # Handle numpy floats
        if isinstance(value, np.floating):
            if np.isnan(value) or np.isinf(value):
                return None
            return float(value)
        
        # Handle numpy arrays - convert to string representation
        if isinstance(value, np.ndarray):
            try:
                as_list = value.tolist()
                return json.dumps(as_list, default=str, ensure_ascii=False)
            except:
                return str(value)
        
        # Handle Python lists - convert to JSON string
        if isinstance(value, list):
            try:
                return json.dumps(value, default=str, ensure_ascii=False)
            except:
                return str(value)
        
        # Handle dictionaries - convert to JSON string
        if isinstance(value, dict):
            try:
                return json.dumps(value, default=str, ensure_ascii=False)
            except:
                return str(value)
        
        # Handle datetime objects
        if isinstance(value, (datetime, date)):
            return value
        
        # Handle pandas Timestamp
        if hasattr(value, 'to_pydatetime'):
            try:
                return value.to_pydatetime()
            except:
                return str(value)
        
        # Handle boolean values
        if isinstance(value, bool):
            return value
        
        # Handle complex numbers
        if isinstance(value, complex):
            return f"{value.real}+{value.imag}i"
        
        # Handle bytes
        if isinstance(value, bytes):
            try:
                return value.decode('utf-8')
            except:
                return str(value)
        
        # Convert to string and clean for remaining types
        try:
            str_value = str(value)
            # Remove control characters
            str_value = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', str_value)
            return str_value
        except Exception as e:
            self.logger.warning(f"Failed to convert value {type(value)} to string: {e}")
            return None
    
    def query(self, sql: str) -> pd.DataFrame:
        """
        Execute a BigQuery SQL query and return results as DataFrame
        
        Args:
            sql: SQL query string
            
        Returns:
            DataFrame with query results, empty DataFrame on error
        """
        try:
            self.logger.info(f"Executing BigQuery query")
            
            # Execute query and convert to DataFrame
            query_job = self.client.query(sql)
            result_df = query_job.to_dataframe()
            
            self.logger.info(f"Query returned {len(result_df)} rows")
            return result_df
            
        except Exception as e:
            self.logger.error(f"Query execution failed: {e}")
            return pd.DataFrame()  # Return empty DataFrame on error
    
    def create_timestamp_table(self, dataset_id: str, table_id: str) -> bool:
        """
        Create a table to store the last processed timestamp
        
        Args:
            dataset_id: BigQuery dataset ID
            table_id: BigQuery table ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.logger.info(f"Creating timestamp table: {dataset_id}.{table_id}")
            
            schema = [
                bigquery.SchemaField("key", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
                bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED")
            ]
            
            table_ref = self.client.dataset(dataset_id).table(table_id)
            table = bigquery.Table(table_ref, schema=schema)
            
            # Create table
            self.client.create_table(table)
            self.logger.info(f"Timestamp table created successfully")
            
            # Insert initial row
            initial_data = pd.DataFrame({
                'key': ['last_processed_mention'],
                'timestamp': [pd.Timestamp('1970-01-01', tz='UTC')],
                'updated_at': [pd.Timestamp.now(tz='UTC')]
            })
            
            self.append(initial_data, dataset_id, table_id, create_if_not_exists=False)
            self.logger.info(f"Initial timestamp row inserted")
            
            return True
            
        except Exception as e:
            if "already exists" in str(e).lower():
                self.logger.info(f"Timestamp table already exists")
                return True
            self.logger.error(f"Error creating timestamp table: {e}")
            return False
    
    def get_last_processed_timestamp(self, dataset_id: str, table_id: str) -> pd.Timestamp:
        """
        Get the last processed timestamp
        
        Args:
            dataset_id: BigQuery dataset ID
            table_id: BigQuery table ID
            
        Returns:
            Last processed timestamp or epoch if not found
        """
        try:
            query = f"""
            SELECT timestamp
            FROM `{self.project_id}.{dataset_id}.{table_id}`
            WHERE key = 'last_processed_mention'
            ORDER BY updated_at DESC
            LIMIT 1
            """
            
            result = self.query(query)
            
            if len(result) > 0:
                timestamp = pd.to_datetime(result.iloc[0]['timestamp'], utc=True)
                self.logger.info(f"Retrieved last processed timestamp: {timestamp}")
                return timestamp
            else:
                # Return epoch time if no record found
                epoch = pd.Timestamp('1970-01-01', tz='UTC')
                self.logger.info(f"No timestamp found, returning epoch: {epoch}")
                return epoch
                
        except Exception as e:
            self.logger.error(f"Error getting last processed timestamp: {e}")
            # Return epoch time on error
            return pd.Timestamp('1970-01-01', tz='UTC')
    
    def update_last_processed_timestamp(self, dataset_id: str, table_id: str, timestamp: pd.Timestamp) -> bool:
        """
        Update the last processed timestamp
        
        Args:
            dataset_id: BigQuery dataset ID
            table_id: BigQuery table ID
            timestamp: New timestamp to store
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # First, try to update existing record
            update_query = f"""
            UPDATE `{self.project_id}.{dataset_id}.{table_id}`
            SET timestamp = @new_timestamp, updated_at = CURRENT_TIMESTAMP()
            WHERE key = 'last_processed_mention'
            """
            
            from google.cloud import bigquery
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("new_timestamp", "TIMESTAMP", timestamp)
                ]
            )
            
            query_job = self.client.query(update_query, job_config=job_config)
            query_job.result()
            
            # Check if update affected any rows
            if query_job.num_dml_affected_rows == 0:
                # No rows updated, insert new record
                self.logger.info("No existing record found, inserting new timestamp")
                
                new_data = pd.DataFrame({
                    'key': ['last_processed_mention'],
                    'timestamp': [timestamp],
                    'updated_at': [pd.Timestamp.now(tz='UTC')]
                })
                
                self.append(new_data, dataset_id, table_id, create_if_not_exists=False)
            
            self.logger.info(f"Updated last processed timestamp to: {timestamp}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating last processed timestamp: {e}")
            return False