import json
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from afs_assistant.api import create_app
from afs_assistant.config import Settings
from afs_assistant.core.models import Corpus, SearchRequest
from afs_assistant.indexing.publication import ingest
from afs_assistant.retrieval.retriever import Retrieval
from afs_assistant.core.storage import Conflict

FIXTURE = Path(__file__).parents[2] / "src/afs_assistant/fixtures/demo.json"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(database=Path(self.temp.name) / "test.sqlite3")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        ingest(FIXTURE, self.app.state.store)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_input_and_browser_security(self):
        self.assertEqual(self.client.post("/api/chats", json={"text": "", "request_id": "empty"}).status_code, 422)
        self.assertEqual(self.client.post("/api/chats", content="x" * 33000, headers={"Content-Type": "application/json"}).status_code, 413)
        self.assertEqual(self.client.post("/api/chats", json={"text": "test question", "request_id": "cross"},
                                          headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        response = self.client.get("/")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_immutable_release_and_disputed_evidence(self):
        snapshot = self.app.state.store.corpus()
        retrieval = Retrieval(snapshot)
        ids = [x["evidence"]["evidence_id"] for x in retrieval.search(SearchRequest(query="powder moisture"))["items"]]
        self.assertNotIn("demo-disputed", ids)
        changed = json.loads(FIXTURE.read_text()); changed["items"][0]["text"] = "Changed"
        path = Path(self.temp.name) / "changed.json"; path.write_text(json.dumps(changed))
        with self.assertRaises(Conflict):
            ingest(path, self.app.state.store)


class HybridTests(unittest.TestCase):
    def test_real_vector_path_and_embedding_mismatch(self):
        corpus = Corpus.model_validate_json(FIXTURE.read_text())
        class FakeSettings:
            embedding_model = "test-vectors"
        class FakeGateway:
            settings = FakeSettings()
            def embed(self, texts, timeout=20):
                return [[1.0, 0.0] for text in texts]
        snapshot = {"corpus": corpus.model_dump_json(), "vectors": json.dumps([[1.0, 0.0] for item in corpus.items]), "model": "test-vectors"}
        retriever = Retrieval(snapshot, FakeGateway())
        self.assertEqual(retriever.search(SearchRequest(query="powder moisture"))["mode"], "hybrid")
        snapshot["model"] = "different"
        with self.assertRaises(ValueError):
            Retrieval(snapshot, FakeGateway()).search(SearchRequest(query="moisture"))


if __name__ == "__main__":
    unittest.main()
