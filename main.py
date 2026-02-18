"""
Cloud Function entry points - thin facade for GCF compatibility.

Google Cloud Functions requires entry point functions to be importable from main.py.
This module re-exports entry points from the pr_review package.
"""
from pr_review.entry_points import (
    review_pr,
    review_pr_pubsub,
    receive_webhook,
    process_dead_letter_queue, 
)
