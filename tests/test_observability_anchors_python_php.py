"""Python and PHP runtime-observability anchor extraction."""
from __future__ import annotations

import hashlib

from graphify.extract import extract_php, extract_python
from graphify.extractors.observability import CANONICALIZATION_VERSION


def _anchors(result: dict) -> list[dict]:
    return [node for node in result["nodes"] if node.get("type") == "observability_anchor"]


def _fingerprint(template: str) -> str:
    material = f"{CANONICALIZATION_VERSION}\n{template}".encode()
    return hashlib.sha256(material).hexdigest()


def test_python_logging_anchors_static_interpolated_and_dynamic(tmp_path):
    source = tmp_path / "worker.py"
    source.write_text(
        '''import logging

logger = logging.getLogger(__name__)

def run(job_id, message):
    logger.info("job started")
    logging.error(f"job {job_id} failed")
    logger.warning(message)
''',
        encoding="utf-8",
    )

    anchors = _anchors(extract_python(source))

    assert len(anchors) == 3
    static = {a["canonical_template"]: a for a in anchors if "canonical_template" in a}
    assert set(static) == {"job started", "job <arg> failed"}
    assert static["job started"]["sha256"] == _fingerprint("job started")
    assert static["job <arg> failed"]["sha256"] == _fingerprint("job <arg> failed")
    assert all(a["metadata"]["framework"] == "python_logging" for a in anchors)
    assert all(a["metadata"]["enclosing_symbol_label"] == "run()" for a in anchors)
    dynamic = [a for a in anchors if a["anchor_kind"] == "DYNAMIC_LOG_CALLSITE"]
    assert len(dynamic) == 1
    assert "canonical_template" not in dynamic[0]


def test_python_module_level_logging_is_attributed_to_file(tmp_path):
    source = tmp_path / "bootstrap.py"
    source.write_text('logging.critical("startup failed")\n', encoding="utf-8")

    anchors = _anchors(extract_python(source))

    assert len(anchors) == 1
    assert anchors[0]["canonical_template"] == "startup failed"
    assert anchors[0]["metadata"]["enclosing_symbol_label"] == "bootstrap.py"


def test_python_arbitrary_info_method_is_not_a_log_anchor(tmp_path):
    source = tmp_path / "client.py"
    source.write_text('def run(client):\n    client.info("not a logger")\n', encoding="utf-8")
    assert _anchors(extract_python(source)) == []


def test_php_psr3_laravel_and_error_log_anchors(tmp_path):
    source = tmp_path / "Worker.php"
    source.write_text(
        '''<?php
class Worker {
    public function run($jobId, $message) {
        $this->logger->info("job started");
        Log::error("job {$jobId} failed");
        error_log($message);
    }
}
''',
        encoding="utf-8",
    )

    anchors = _anchors(extract_php(source))

    assert len(anchors) == 3
    static = {a["canonical_template"]: a for a in anchors if "canonical_template" in a}
    assert set(static) == {"job started", "job <arg> failed"}
    assert static["job started"]["metadata"]["framework"] == "psr3"
    assert static["job <arg> failed"]["metadata"]["framework"] == "laravel_log"
    assert all(a["metadata"]["enclosing_symbol_label"] == "run()" for a in anchors)
    dynamic = [a for a in anchors if a["anchor_kind"] == "DYNAMIC_LOG_CALLSITE"]
    assert len(dynamic) == 1
    assert dynamic[0]["metadata"]["framework"] == "php_error_log"


def test_php_arbitrary_info_method_is_not_a_log_anchor(tmp_path):
    source = tmp_path / "Client.php"
    source.write_text(
        '<?php function run($client) { $client->info("not a logger"); }\n',
        encoding="utf-8",
    )
    assert _anchors(extract_php(source)) == []
