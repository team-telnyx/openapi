import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_public_mirror import strip_hidden_operations


class PublicMirrorTest(unittest.TestCase):
    def test_hidden_operations_are_removed_without_dropping_visible_siblings(self):
        source = {
            "paths": {
                "/mixed": {
                    "get": {"operationId": "visible"},
                    "post": {"operationId": "hidden", "x-hidden": True},
                },
                "/hidden": {"delete": {"operationId": "hiddenOnly", "x-hidden": True}},
            }
        }
        public, removed = strip_hidden_operations(source)
        self.assertEqual(removed, 2)
        self.assertIn("get", public["paths"]["/mixed"])
        self.assertNotIn("post", public["paths"]["/mixed"])
        self.assertNotIn("/hidden", public["paths"])


if __name__ == "__main__":
    unittest.main()
