"""User-declared book availability is separate from provider/reference coverage."""
import os
import re


def book_preferences():
    books = sorted({part.strip().lower() for part in os.environ.get('AVAILABLE_BOOKS', '').split(',') if part.strip()})
    if any(not re.fullmatch(r'[a-z0-9_]+', book) for book in books):
        raise ValueError('AVAILABLE_BOOKS must contain comma-separated provider bookmaker keys')
    jurisdiction = os.environ.get('USER_JURISDICTION', '').strip().upper() or None
    if jurisdiction and not re.fullmatch(r'[A-Z0-9-]{2,20}', jurisdiction):
        raise ValueError('Use a short jurisdiction label such as US-VA')
    return {'user_listed_books': books, 'jurisdiction': jurisdiction, 'eligibility_verified': False,
            'note': 'User-listed books only; account access, jurisdiction-specific markets and settlement rules are not verified'}
