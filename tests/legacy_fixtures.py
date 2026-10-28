"""Набор для исторических тестов; новые возможности проверяются отдельно."""
from do_vstrechi.data import load_bundle as load_current_bundle

def load_bundle():
    bundle = load_current_bundle()
    bundle['scenario']['dialogue_features']['client_followups'] = False
    bundle['scenario']['dialogue_features']['automatic_review'] = False
    bundle['scenario']['dialogue_features']['natural_dialogue'] = False
    bundle['scenario']['dialogue_features']['distinct_client_behavior'] = False
    bundle['scenario'].pop('client_profiles', None)
    return bundle
