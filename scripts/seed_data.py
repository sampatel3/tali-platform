"""Seed the TAALI database with demo data.

Usage:
    cd backend
    python -m scripts.seed_data
    
    OR from the taali/ root:
    PYTHONPATH=backend python scripts/seed_data.py
"""
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.core.database import SessionLocal, engine, Base
from app.core.security import get_password_hash
from app.models import User, Organization, Assessment, AssessmentStatus, Candidate, Task, AssessmentSession
from datetime import datetime, timedelta
import secrets


def seed():
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        # Check if already seeded
        if db.query(User).first():
            print("Database already seeded. Skipping.")
            return
        
        # 1. Create organization
        org = Organization(
            name="DeepLight AI",
            slug="deeplight-ai",
            plan="pay_per_use",
            assessments_used=13,
            workable_connected=True,
            workable_subdomain="deeplight",
            workable_config={
                "auto_send_on_stage": True,
                "auto_send_stage": "Technical Screen",
                "sync_results": True,
                "auto_advance_threshold": 8.0,
            },
        )
        db.add(org)
        db.flush()
        
        # 2. Create admin user
        user = User(
            email="sam@deeplight.ai",
            hashed_password=get_password_hash("demo1234"),
            full_name="Sam Patel",
            organization_id=org.id,
            is_active=True,
        )
        db.add(user)
        db.flush()
        
        # 3. Create task templates
        debugging_task = Task(
            organization_id=org.id,
            name="Debugging Challenge",
            description="Fix 3 bugs in a data pipeline that processes CSV files. The pipeline has issues with delimiters, empty row handling, and a race condition in async processing.",
            task_type="debugging",
            difficulty="senior",
            duration_minutes=30,
            starter_code='''import pandas as pd
import asyncio

def parse_row(row, delimiter=","):
    """Parse a single CSV row. BUG: wrong delimiter used."""
    return row.split("\\t")  # Bug 1: should use delimiter param

def process_csv(filepath):
    """Process CSV file and return cleaned data."""
    rows = []
    with open(filepath, 'r') as f:
        for line in f:
            parsed = parse_row(line.strip())
            rows.append(parsed)  # Bug 2: no empty row filtering
    return rows

async def pipeline(files):
    """Run pipeline on multiple files."""
    results = []
    for f in files:
        result = process_csv(f)
        results.append(result)  # Bug 3: should await async operations
    return results
''',
            test_code='''import pytest

def test_parse_row_comma():
    assert parse_row("a,b,c") == ["a", "b", "c"]

def test_parse_row_custom_delimiter():
    assert parse_row("a|b|c", delimiter="|") == ["a", "b", "c"]

def test_empty_rows_filtered():
    result = process_csv("test.csv")
    assert all(len(row) > 0 for row in result)

def test_pipeline_async():
    import asyncio
    result = asyncio.run(pipeline(["test.csv"]))
    assert isinstance(result, list)
''',
            is_template=True,
            is_active=True,
        )
        
        ai_task = Task(
            organization_id=org.id,
            name="AI Engineering",
            description="Build and debug a RAG pipeline with vector search, embedding generation, and context window management.",
            task_type="ai_engineering",
            difficulty="senior",
            duration_minutes=45,
            starter_code="# RAG Pipeline starter code\\nimport numpy as np\\n",
            test_code="# Tests for RAG pipeline",
            is_template=True,
            is_active=True,
        )
        
        optimization_task = Task(
            organization_id=org.id,
            name="Performance Optimization",
            description="Optimize a slow data processing function that handles large datasets. Improve time complexity and memory usage.",
            task_type="optimization",
            difficulty="mid",
            duration_minutes=30,
            starter_code="# Optimization challenge starter code\\n",
            test_code="# Performance tests",
            is_template=True,
            is_active=True,
        )
        
        db.add_all([debugging_task, ai_task, optimization_task])
        db.flush()
        
        # 4. Create candidates
        candidates_data = [
            ("Sarah Chen", "sarah.chen@example.com", "Senior Data Engineer"),
            ("Mike Ross", "mike.ross@example.com", "AI Engineer"),
            ("Amy Wong", "amy.wong@example.com", "Full Stack Developer"),
            ("James Liu", "james.liu@example.com", "ML Engineer"),
        ]
        
        candidates = []
        for name, email, position in candidates_data:
            c = Candidate(
                organization_id=org.id,
                email=email,
                full_name=name,
                position=position,
            )
            db.add(c)
            db.flush()
            candidates.append(c)
        
        # 5. Create assessments
        # Sarah - completed, high score
        a1 = Assessment(
            organization_id=org.id,
            candidate_id=candidates[0].id,
            task_id=debugging_task.id,
            token=secrets.token_urlsafe(32),
            status=AssessmentStatus.COMPLETED,
            duration_minutes=30,
            started_at=datetime.utcnow() - timedelta(hours=3),
            completed_at=datetime.utcnow() - timedelta(hours=2, minutes=32),
            score=8.7,
            tests_passed=5,
            tests_total=5,
            code_quality_score=8.5,
            time_efficiency_score=9.0,
            ai_usage_score=8.5,
            ai_prompts=[
                {"message": "What's wrong with the delimiter in line 42?", "timestamp": "2026-02-10T10:05:00"},
                {"message": "How should I handle the edge case for empty CSV rows?", "timestamp": "2026-02-10T10:12:00"},
                {"message": "Can you explain the difference between pandas merge and join?", "timestamp": "2026-02-10T10:18:00"},
                {"message": "Write a test for the fixed parse_row function", "timestamp": "2026-02-10T10:22:00"},
            ],
            timeline=[
                {"time": "00:00", "event": "Started assessment"},
                {"time": "02:30", "event": "Read through codebase and requirements"},
                {"time": "05:00", "event": "Fixed delimiter bug (Bug 1/3)"},
                {"time": "12:00", "event": "Fixed empty row handling (Bug 2/3)"},
                {"time": "18:00", "event": "Identified race condition (Bug 3/3)"},
                {"time": "22:00", "event": "Added test coverage"},
                {"time": "26:00", "event": "Final review and cleanup"},
                {"time": "28:00", "event": "Submitted assessment"},
            ],
            test_results={"passed": 5, "failed": 0, "total": 5},
        )
        
        # Mike - completed, medium score
        a2 = Assessment(
            organization_id=org.id,
            candidate_id=candidates[1].id,
            task_id=ai_task.id,
            token=secrets.token_urlsafe(32),
            status=AssessmentStatus.COMPLETED,
            duration_minutes=45,
            started_at=datetime.utcnow() - timedelta(days=1, hours=5),
            completed_at=datetime.utcnow() - timedelta(days=1, hours=4, minutes=25),
            score=7.2,
            tests_passed=4,
            tests_total=5,
            code_quality_score=7.0,
            time_efficiency_score=7.5,
            ai_usage_score=7.0,
            ai_prompts=[
                {"message": "Write the entire function for me", "timestamp": "2026-02-09T10:05:00"},
                {"message": "Fix the errors in this code", "timestamp": "2026-02-09T10:15:00"},
            ],
            test_results={"passed": 4, "failed": 1, "total": 5},
        )
        
        # Amy - in progress
        a3 = Assessment(
            organization_id=org.id,
            candidate_id=candidates[2].id,
            task_id=optimization_task.id,
            token=secrets.token_urlsafe(32),
            status=AssessmentStatus.IN_PROGRESS,
            duration_minutes=30,
            started_at=datetime.utcnow() - timedelta(minutes=15),
            expires_at=datetime.utcnow() + timedelta(days=6),
        )
        
        # James - completed, lower score
        a4 = Assessment(
            organization_id=org.id,
            candidate_id=candidates[3].id,
            task_id=ai_task.id,
            token=secrets.token_urlsafe(32),
            status=AssessmentStatus.COMPLETED,
            duration_minutes=45,
            started_at=datetime.utcnow() - timedelta(days=3, hours=8),
            completed_at=datetime.utcnow() - timedelta(days=3, hours=7, minutes=20),
            score=6.2,
            tests_passed=3,
            tests_total=5,
            code_quality_score=6.0,
            time_efficiency_score=6.5,
            ai_usage_score=6.0,
            ai_prompts=[
                {"message": "Do everything for me step by step", "timestamp": "2026-02-07T09:05:00"},
            ],
            test_results={"passed": 3, "failed": 2, "total": 5},
        )
        
        db.add_all([a1, a2, a3, a4])
        db.commit()
        
        print("Database seeded successfully!")
        print(f"  Organization: {org.name}")
        print(f"  User: {user.email} / demo1234")
        print(f"  Tasks: {debugging_task.name}, {ai_task.name}, {optimization_task.name}")
        print(f"  Candidates: {len(candidates)}")
        print(f"  Assessments: 4")
        
    except Exception as e:
        db.rollback()
        print(f"Seeding failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
