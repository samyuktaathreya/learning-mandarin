import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import create_questions
import vocab_index_parser


class CreateQuestionsTests(unittest.TestCase):
    def test_diacritic_to_numeric_preserves_numeric_input(self):
        self.assertEqual(vocab_index_parser.diacritic_to_numeric("bu4"), "bu4")
        self.assertEqual(vocab_index_parser.diacritic_to_numeric("péngyou"), "peng2you5")

    def test_build_questions_for_unit_uses_expected_schema(self):
        index_data = {
            "vocab": [
                {"hanzi": "老师", "pinyin": "lao3shi1", "english": "teacher", "unit": 3, "type": "vocab"},
                {"hanzi": "中国", "pinyin": "Zhong1guo2", "english": "China", "unit": 3, "type": "proper_noun"},
            ],
            "grammar": [
                {"hanzi": "了", "pinyin": "le5", "english": "aspect particle", "unit": 3, "type": "grammar"},
            ],
            "proper_nouns": [
                {"hanzi": "中国", "pinyin": "Zhong1guo2", "english": "China", "unit": 3, "type": "proper_noun"},
            ],
        }
        units_data = {
            "3": {
                "sentences": [
                    {"hanzi": "我喜欢老师。", "english": "I like the teacher.", "tags": ["我", "喜欢", "老师"], "pinyin": "wo3 xi3huan1 lao3shi1"}
                ],
                "fill_in_the_blank": [
                    {"question": "我___老师。", "answer": "喜欢", "full_sentence": "我喜欢老师。"}
                ],
            }
        }

        questions = create_questions.build_questions_for_unit(index_data, units_data, "3")

        self.assertTrue(questions)
        first = questions[0]
        self.assertIn("id", first)
        self.assertEqual(first["unit"], 3)
        self.assertIn(first["question_type"], create_questions.QuestionType.values())
        self.assertIn("tags", first)
        self.assertIsInstance(first["tags"], list)


if __name__ == "__main__":
    unittest.main()
