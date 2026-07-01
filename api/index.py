"""
Vercel serverless entry point.
Imports the FastAPI app so Vercel's @vercel/python runtime can serve it.
"""

from app.main import app
