"""Existing component checks that do not need local banking datasets."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_connection import ConfigurationTests
from test_voice_input import VoiceTests

def load_tests(loader, tests, pattern):
    suite = loader.loadTestsFromTestCase(ConfigurationTests)
    for name in ["test_exact_audio_contract_and_limits", "test_no_model_needed_for_text_only_and_empty_voice_not_invented"]:
        suite.addTest(VoiceTests(name))
    return suite
