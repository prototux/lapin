"""Which language speech-to-text is told to expect (sessions.stt_language)."""

import unittest
from types import SimpleNamespace

from assistant.sessions import stt_language


def settings(language):
    return {"assistant": {"language": language}}


class SttLanguageTest(unittest.TestCase):
    def test_follow_up_gets_the_conversation_language(self):
        # a French "oui" was heard as an English "Yeah." when left to detection
        self.assertEqual(stt_language(settings("auto"), "followup", SimpleNamespace(lang="fr")), "fr")

    def test_follow_up_without_a_language_yet_detects_it(self):
        self.assertIsNone(stt_language(settings("auto"), "followup", SimpleNamespace(lang=None)))

    def test_new_requests_detect_it(self):
        # people switch languages between requests: only the answer to a question follows the conversation
        for source in ("button", "kws"):
            self.assertIsNone(stt_language(settings("auto"), source, SimpleNamespace(lang="fr")))

    def test_a_fixed_language_applies_to_every_turn(self):
        for source in ("button", "kws", "followup"):
            self.assertEqual(stt_language(settings("en"), source, SimpleNamespace(lang="fr")), "en")


if __name__ == "__main__":
    unittest.main()
