"""Характер меняет манеру общения, сохраняя факты, переходы и критерии."""
from copy import deepcopy


def profiles(scenario):
    return scenario.get('client_profiles', [])


def selected_profile(scenario, key='calm'):
    if not profiles(scenario) and key == 'calm':
        return None
    return next((deepcopy(p) for p in profiles(scenario) if p['id'] == key), None)


def profile_reply(session, reaction, original):
    return session.get('client_profile', {}).get('replies', {}).get(reaction, original)
