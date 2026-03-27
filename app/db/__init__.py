"""
app/db/__init__.py
Single Supabase client instance shared across all db modules.
"""

from supabase import create_client, Client
import config

supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)