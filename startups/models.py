"""The startups app is kept only so its migrations can run (ASSUMPTIONS 458).

The startup portal, the STARTUP table, the Navigatu sync and the staff Startups pages were removed on 2026-11-26 (owner
decision before the user testing): the portal is a Chapter 5 recommendation. Migration startups/0003 drops the table
after registrations/0015 and notifications/0005 removed the columns that pointed to it. Do not add models here.
"""
