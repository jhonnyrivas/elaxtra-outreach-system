"""Read-only internal dashboard for the Elaxtra Outreach System.

The dashboard runs inside the same FastAPI app that serves the webhook,
on /dashboard/*. All pages are SSR Jinja2 templates with HTMX for light
interactions (polling, search). JSON counterparts live under /api/*.
"""
