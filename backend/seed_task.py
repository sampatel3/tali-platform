"""Seed script: Remove placeholder tasks and add the AWS Glue pilot task.

Usage:
    cd backend
    python seed_task.py
"""

import sys
import os

# Add backend to path so imports work
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.platform.config import settings
from app.models.task import Task

engine = create_engine(settings.DATABASE_URL)
Session = sessionmaker(bind=engine)
db = Session()

# --- 1. Remove existing placeholder tasks ---
existing = db.query(Task).all()
print(f"Found {len(existing)} existing task(s):")
for t in existing:
    print(f"  - [{t.id}] {t.name} (org={t.organization_id}, active={t.is_active})")

deleted = db.query(Task).delete()
db.commit()
print(f"\nDeleted {deleted} placeholder task(s).")

# --- 2. Get the first organization to assign the task to ---
from app.models.user import User

first_user = db.query(User).first()
if not first_user:
    print("\nERROR: No users found in database. Register a business account first, then re-run.")
    db.close()
    sys.exit(1)

org_id = first_user.organization_id
print(f"\nAssigning task to organization_id={org_id} (user: {first_user.email})")

# --- 3. Insert the AWS Glue Pipeline Debugging task ---
STARTER_CODE = r'''# AWS Glue ETL Job: On-Prem SQL Server to S3 Migration
# This job runs nightly to sync customer transactions
# Known issues: Intermittent failures, duplicate records, missing error alerts

import sys
import asyncio
from datetime import datetime, timedelta
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.dynamicframe import DynamicFrame
from pyspark.sql.functions import col, lit, current_timestamp
import boto3
import json

# Initialize Glue context
args = getResolvedOptions(sys.argv, ['JOB_NAME', 'source_connection', 's3_target_path'])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

# Configuration
SOURCE_CONNECTION = args['source_connection']
S3_TARGET_PATH = args['s3_target_path']
BATCH_SIZE = 100000


class TransactionMigrator:
    """Handles migration of transaction data from on-prem to S3"""
    
    def __init__(self, glue_context, source_conn, target_path):
        self.glue_context = glue_context
        self.source_conn = source_conn
        self.target_path = target_path
        self.sns_client = boto3.client('sns')
        self.cloudwatch = boto3.client('cloudwatch')
        
    # BUG 1: This async function is never awaited properly
    async def send_alert(self, message, severity='INFO'):
        """Send alert to SNS topic for monitoring"""
        try:
            self.sns_client.publish(
                TopicArn='arn:aws:sns:us-east-1:123456789:glue-alerts',
                Message=json.dumps({
                    'job_name': args['JOB_NAME'],
                    'severity': severity,
                    'message': message,
                    'timestamp': str(datetime.now())
                }),
                Subject=f'Glue Job Alert: {severity}'
            )
        except Exception as e:
            # Silent failure - alerts never sent
            pass
    
    def extract_transactions(self, start_date, end_date):
        """Extract transactions from source SQL Server"""
        query = f"""
            SELECT 
                transaction_id,
                customer_id,
                amount,
                currency,
                transaction_date,
                status,
                metadata
            FROM dbo.transactions
            WHERE transaction_date >= '{start_date}'
            AND transaction_date < '{end_date}'
        """
        
        # BUG 2: No job bookmarking - causes duplicate processing on retry
        dynamic_frame = self.glue_context.create_dynamic_frame.from_catalog(
            database="onprem_db",
            table_name="transactions",
            push_down_predicate=f"transaction_date >= '{start_date}' AND transaction_date < '{end_date}'"
        )
        
        return dynamic_frame
    
    def transform_transactions(self, dynamic_frame):
        """Apply transformations to transaction data"""
        df = dynamic_frame.toDF()
        
        # Add audit columns
        df = df.withColumn('ingestion_timestamp', current_timestamp())
        df = df.withColumn('source_system', lit('ONPREM_SQLSERVER'))
        
        # BUG 3: Currency conversion fails silently for NULL values
        # and doesn't handle edge cases properly
        df = df.withColumn(
            'amount_usd',
            when(col('currency') == 'GBP', col('amount') * 1.27)
            .when(col('currency') == 'EUR', col('amount') * 1.09)
            .otherwise(col('amount'))  # Assumes USD but could be other currencies
        )
        
        # Missing: Data validation / smoke tests before write
        
        return DynamicFrame.fromDF(df, self.glue_context, 'transformed')
    
    def load_to_s3(self, dynamic_frame, partition_date):
        """Write transformed data to S3 with partitioning"""
        output_path = f"{self.target_path}/year={partition_date.year}/month={partition_date.month:02d}/day={partition_date.day:02d}"
        
        # ISSUE: No optimization - small files problem, no compaction
        self.glue_context.write_dynamic_frame.from_options(
            frame=dynamic_frame,
            connection_type="s3",
            connection_options={"path": output_path},
            format="parquet"
        )
        
        return output_path
    
    def run_migration(self, days_back=1):
        """Main migration orchestration"""
        end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start_date = end_date - timedelta(days=days_back)
        
        print(f"Starting migration for {start_date} to {end_date}")
        
        # ISSUE: No try/catch around main flow, job fails without cleanup
        raw_data = self.extract_transactions(start_date, end_date)
        
        record_count = raw_data.count()
        print(f"Extracted {record_count} records")
        
        if record_count == 0:
            # Async call but not awaited - alert never sends
            self.send_alert("No records found for date range", "WARNING")
            return
        
        transformed_data = self.transform_transactions(raw_data)
        output_path = self.load_to_s3(transformed_data, start_date)
        
        # ISSUE: No validation that write succeeded
        # ISSUE: No record count reconciliation
        
        print(f"Migration complete. Output: {output_path}")
        self.send_alert(f"Migration complete: {record_count} records", "INFO")
        
        # Missing: CloudWatch metrics for monitoring
        
        return record_count


# Main execution
if __name__ == "__main__":
    migrator = TransactionMigrator(glueContext, SOURCE_CONNECTION, S3_TARGET_PATH)
    migrator.run_migration()
    job.commit()
'''

TEST_CODE = r'''import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timedelta
import json

# Mock AWS Glue dependencies for testing
import sys
sys.modules['awsglue.transforms'] = Mock()
sys.modules['awsglue.utils'] = Mock()
sys.modules['awsglue.context'] = Mock()
sys.modules['awsglue.job'] = Mock()
sys.modules['awsglue.dynamicframe'] = Mock()
sys.modules['pyspark.context'] = Mock()
sys.modules['pyspark.sql.functions'] = Mock()


class TestTransactionMigrator:
    """Test suite for the AWS Glue migration job"""
    
    @pytest.fixture
    def migrator(self):
        """Create migrator instance with mocked dependencies"""
        with patch('boto3.client') as mock_boto:
            mock_glue_context = Mock()
            migrator = TransactionMigrator(
                glue_context=mock_glue_context,
                source_conn='test_connection',
                target_path='s3://test-bucket/transactions'
            )
            yield migrator
    
    @pytest.mark.asyncio
    async def test_send_alert_is_awaited(self, migrator):
        """BUG 1: Verify alerts are properly awaited and sent"""
        with patch.object(migrator.sns_client, 'publish') as mock_publish:
            # The alert should be awaited - if not, this test structure will catch it
            result = migrator.send_alert("Test message", "INFO")
            
            # If send_alert is async, it should return a coroutine that needs awaiting
            if asyncio.iscoroutine(result):
                await result
            
            # Verify SNS was actually called
            assert mock_publish.called, "Alert was never sent - async function not awaited"
            
            # Verify the message structure
            call_args = mock_publish.call_args
            message_body = json.loads(call_args[1]['Message'])
            assert message_body['severity'] == 'INFO'
            assert 'timestamp' in message_body
    
    def test_job_bookmarking_enabled(self, migrator):
        """BUG 2: Verify job bookmarking is enabled to prevent duplicates"""
        mock_dynamic_frame = Mock()
        migrator.glue_context.create_dynamic_frame.from_catalog.return_value = mock_dynamic_frame
        
        start_date = datetime.now() - timedelta(days=1)
        end_date = datetime.now()
        
        migrator.extract_transactions(start_date, end_date)
        
        # Check that transformation_ctx is passed for bookmarking
        call_kwargs = migrator.glue_context.create_dynamic_frame.from_catalog.call_args[1]
        
        assert 'transformation_ctx' in call_kwargs, \
            "Job bookmarking not enabled - transformation_ctx missing. This causes duplicate processing on job retry."
    
    def test_currency_conversion_handles_nulls(self, migrator):
        """BUG 3: Verify NULL currency values are handled properly"""
        # Create test dataframe with edge cases
        test_data = [
            {'transaction_id': 1, 'amount': 100.0, 'currency': 'GBP'},
            {'transaction_id': 2, 'amount': 100.0, 'currency': 'EUR'},
            {'transaction_id': 3, 'amount': 100.0, 'currency': None},  # NULL currency
            {'transaction_id': 4, 'amount': 100.0, 'currency': 'JPY'},  # Unsupported currency
            {'transaction_id': 5, 'amount': None, 'currency': 'USD'},   # NULL amount
        ]
        
        mock_df = Mock()
        mock_dynamic_frame = Mock()
        mock_dynamic_frame.toDF.return_value = mock_df
        
        # The transform should explicitly handle NULLs and unknown currencies
        # This test verifies the logic exists
        result = migrator.transform_transactions(mock_dynamic_frame)
        
        # Verify withColumn was called with NULL handling
        withColumn_calls = [str(call) for call in mock_df.withColumn.call_args_list]
        
        # Should have explicit NULL handling in currency conversion
        assert any('isNull' in str(call) or 'isNotNull' in str(call) or 'coalesce' in str(call) 
                   for call in withColumn_calls), \
            "Currency conversion does not handle NULL values - will cause silent data corruption"
    
    def test_smoke_tests_run_before_write(self, migrator):
        """Verify data quality checks run before writing to S3"""
        mock_df = Mock()
        mock_df.count.return_value = 1000
        mock_dynamic_frame = Mock()
        mock_dynamic_frame.toDF.return_value = mock_df
        
        # Transform should include validation
        result = migrator.transform_transactions(mock_dynamic_frame)
        
        # Check for data validation calls
        df_method_calls = [str(call) for call in mock_df.method_calls]
        
        # Should have validation: null checks, count validation, schema validation
        validation_patterns = ['filter', 'where', 'isNull', 'count', 'schema']
        has_validation = any(
            any(pattern in str(call) for pattern in validation_patterns)
            for call in df_method_calls
        )
        
        assert has_validation, \
            "No smoke tests/data validation before S3 write - corrupt data could be written"
    
    def test_write_includes_optimization(self, migrator):
        """Verify S3 write is optimized to prevent small files"""
        mock_dynamic_frame = Mock()
        partition_date = datetime.now()
        
        migrator.load_to_s3(mock_dynamic_frame, partition_date)
        
        # Check write options include optimization
        write_call = migrator.glue_context.write_dynamic_frame.from_options.call_args
        connection_options = write_call[1].get('connection_options', {})
        
        # Should have partitioning or coalesce to prevent small files
        has_optimization = (
            'partitionKeys' in str(connection_options) or
            'coalesce' in str(mock_dynamic_frame.method_calls) or
            'repartition' in str(mock_dynamic_frame.method_calls)
        )
        
        assert has_optimization, \
            "S3 write has no optimization - will create small files problem"
    
    def test_migration_has_proper_error_handling(self, migrator):
        """Verify main migration flow has try/catch with cleanup"""
        # Simulate extraction failure
        migrator.glue_context.create_dynamic_frame.from_catalog.side_effect = Exception("Connection failed")
        
        # run_migration should catch errors and send alerts
        with patch.object(migrator, 'send_alert') as mock_alert:
            try:
                migrator.run_migration()
            except Exception:
                pass  # May still raise, but should have attempted cleanup
            
            # Should have attempted to send error alert
            error_calls = [call for call in mock_alert.call_args_list 
                          if 'ERROR' in str(call) or 'CRITICAL' in str(call)]
            
            assert len(error_calls) > 0, \
                "No error alert sent on failure - ops team won't know about failures"


class TestRecordReconciliation:
    """Tests for data integrity validation"""
    
    def test_source_target_count_match(self):
        """Verify record counts are reconciled between source and target"""
        # This test validates that the job checks source count == target count
        # The current implementation doesn't do this
        
        source_count = 1000
        target_count = 1000  # Should match
        
        assert source_count == target_count, \
            "Record count reconciliation not implemented - data loss could go undetected"
    
    def test_metrics_published_to_cloudwatch(self):
        """Verify operational metrics are published"""
        # The job should publish metrics for monitoring dashboards
        expected_metrics = [
            'RecordsProcessed',
            'ProcessingDurationSeconds', 
            'ErrorCount',
            'SourceTargetCountDelta'
        ]
        
        # Current implementation publishes no metrics
        published_metrics = []  # Would be populated by the job
        
        missing_metrics = set(expected_metrics) - set(published_metrics)
        assert len(missing_metrics) == 0, \
            f"Missing CloudWatch metrics: {missing_metrics} - no visibility into job health"
'''

task = Task(
    organization_id=org_id,
    name="AWS Glue Pipeline Debugging",
    description="You've inherited a production AWS Glue job that migrates customer transaction data from an on-premises SQL Server to S3. The pipeline has been failing intermittently in production, causing data quality issues and SLA breaches. Your task is to identify and fix the bugs, improve error handling, add proper job bookmarking, and implement smoke tests to validate the migration.",
    task_type="debugging",
    difficulty="senior",
    duration_minutes=30,
    starter_code=STARTER_CODE,
    test_code=TEST_CODE,
    is_active=True,
    is_template=False,
)

db.add(task)
db.commit()
db.refresh(task)

print(f"\n✓ Created task: [{task.id}] {task.name}")
print(f"  Type: {task.task_type} | Difficulty: {task.difficulty} | Duration: {task.duration_minutes}m")
print(f"  Starter code: {len(task.starter_code)} chars")
print(f"  Test code: {len(task.test_code)} chars")
print(f"  Organization: {org_id}")
print(f"\nDone! The task is now available in the UI.")

db.close()
